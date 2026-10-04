#!/usr/bin/env python3
"""无损 TXT 改稿回传。只有 import/undo 修改正文；历史在独立 state 目录。"""
import argparse
import contextlib
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import uuid

MAGIC = 'NOVEL-ROUNDTRIP 1 '
PREFIX = '@@NOVEL:'
MAX_BYTES = 50 * 1024 * 1024


class Rejected(Exception):
    def __init__(self, issues):
        self.issues = issues if isinstance(issues, list) else [issues]
        super().__init__('；'.join(self.issues))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat()


def decode(data):
    if len(data) > MAX_BYTES:
        raise Rejected('文件超过 50 MiB，拒绝处理')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        raise Rejected('文件不是有效 UTF-8；请使用阅读Max的“分享改稿 TXT”')
    if '\x00' in text or '\ufeff' in text:
        raise Rejected('正文中出现 NUL 或非文件开头的 BOM')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def pack(batch, chapters):
    result = MAGIC + batch + '\n'
    for num, raw in chapters:
        result += f'{PREFIX}BEGIN:{num}@@\n{raw}\n{PREFIX}END:{num}@@\n'
    return result + f'{PREFIX}EOF:{batch}@@\n'


def parse(text, manifest):
    batch = manifest['batch']
    issues = []
    if not text.startswith(MAGIC + batch + '\n'):
        raise Rejected('导出批次标识损坏')
    cursor = len(MAGIC + batch + '\n')
    result = []
    expected = [x['num'] for x in manifest['chapters']]
    seen = re.findall(r'^@@NOVEL:BEGIN:(\d+)@@$', text, re.M)
    actual = [int(x) for x in seen]
    for num in expected:
        if num not in actual:
            issues.append(f'第{num}章：整章缺失或起始边界被删除')
    for num in set(actual):
        if actual.count(num) > 1:
            issues.append(f'第{num}章：重复出现')
        if num not in expected:
            issues.append(f'第{num}章：不在本次导出清单内')
    if actual != expected:
        issues.append(f'章节顺序错误：应为 {expected}，实际 {actual}')
    if issues:
        raise Rejected(issues)
    for item in manifest['chapters']:
        num = item['num']
        begin = f'{PREFIX}BEGIN:{num}@@\n'
        end = f'\n{PREFIX}END:{num}@@\n'
        if not text.startswith(begin, cursor):
            raise Rejected(f'第{num}章：起始边界损坏，或前章末尾出现额外文本')
        start = cursor + len(begin)
        stop = text.find(end, start)
        if stop < 0:
            raise Rejected(f'第{num}章：结束边界缺失或损坏')
        raw = text[start:stop]
        if PREFIX in raw or MAGIC in raw:
            raise Rejected(f'第{num}章：边界混入正文，可能删除了上一章的结束标识')
        if not re.match(r'^# [^\n]+\n', raw):
            issues.append(f'第{num}章：首行“# 标题”被删除、清空或损坏')
        if not raw.partition('\n')[2].strip():
            issues.append(f'第{num}章：正文为空，拒绝整章清空')
        result.append((item, raw))
        cursor = stop + len(end)
    if text[cursor:] != f'{PREFIX}EOF:{batch}@@\n':
        issues.append(f'第{expected[-1]}章之后：文件结束标识损坏、文件被截断或存在多余内容')
    if issues:
        raise Rejected(issues)
    return result


def changes(num, filename, old, new):
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    edits = []
    added_chars = removed_chars = added_lines = removed_lines = 0
    for op, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        edits.append({'operation': op, 'old_start': i + 1, 'old_count': j - i,
                      'new_start': k + 1, 'new_count': l - k,
                      'removed': a[i:j], 'added': b[k:l]})
        removed_lines += j - i
        added_lines += l - k
        x, y = ''.join(a[i:j]), ''.join(b[k:l])
        for cop, p, q, r, s in difflib.SequenceMatcher(None, x, y, autojunk=False).get_opcodes():
            if cop != 'equal':
                removed_chars += len(x[p:q].replace('\r', '').replace('\n', ''))
                added_chars += len(y[r:s].replace('\r', '').replace('\n', ''))
    return {'chapter': num, 'file': filename, 'edits': edits,
            'added_chars': added_chars, 'removed_chars': removed_chars,
            'net_chars': added_chars - removed_chars,
            'added_lines': added_lines, 'removed_lines': removed_lines}


def atomic(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.novel-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Store:
    def __init__(self, project, state):
        self.project = Path(project).expanduser().resolve()
        self.body = self.project / '正文'
        self.state = Path(state).expanduser().resolve()
        if self.state == self.project or self.project in self.state.parents:
            raise Rejected('历史目录必须放在小说项目之外')
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.state / 'history.sqlite3', timeout=30)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, status TEXT, payload TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS receipts (sha TEXT PRIMARY KEY, result TEXT)')
        self.db.commit()

    @contextlib.contextmanager
    def locked(self):
        with (self.state / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.recover()
            yield

    def blob(self, data):
        key = digest(data)
        path = self.state / 'blobs' / key
        if not path.exists():
            atomic(path, data)
        return key

    def read_blob(self, key):
        data = (self.state / 'blobs' / key).read_bytes()
        if digest(data) != key:
            raise Rejected('历史快照校验失败：' + key)
        return data

    def path(self, filename):
        if Path(filename).name != filename or not re.fullmatch(r'第\d+章-.+\.md', filename):
            raise Rejected('非法章节路径')
        path = self.body / filename
        if self.body.is_symlink() or path.is_symlink() or not path.is_file():
            raise Rejected('章节丢失或是符号链接：' + filename)
        if path.stat().st_nlink != 1:
            raise Rejected('章节是硬链接：' + filename)
        return path

    def export(self):
        batch = uuid.uuid4().hex
        items, texts = [], []
        for p in self.body.glob('*.md'):
            m = re.fullmatch(r'第(\d+)章-.+\.md', p.name)
            if not m:
                raise Rejected('无法识别章节文件名：' + p.name)
            data = self.path(p.name).read_bytes()
            raw = decode(data)
            if PREFIX in raw or MAGIC in raw or not re.match(r'^# [^\n]+\n', raw):
                raise Rejected('章节标题或保留标识异常：' + p.name)
            if b'\r' in data.replace(b'\r\n', b''):
                raise Rejected('源文件含独立 CR：' + p.name)
            if b'\r\n' in data and b'\n' in data.replace(b'\r\n', b''):
                raise Rejected('源文件混合换行：' + p.name)
            item = {'num': int(m[1]), 'file': p.name, 'hash': self.blob(data),
                    'bom': data.startswith(b'\xef\xbb\xbf'),
                    'newline': '\r\n' if b'\r\n' in data else '\n'}
            items.append(item)
            texts.append((item['num'], raw))
        items.sort(key=lambda x: x['num'])
        texts.sort()
        if not items or len({x['num'] for x in items}) != len(items):
            raise Rejected('章节清单为空或章号重复')
        manifest = {'batch': batch, 'project': str(self.project), 'time': now(), 'chapters': items}
        payload = pack(batch, texts)
        parse(payload, manifest)
        for item in items:
            if digest(self.path(item['file']).read_bytes()) != item['hash']:
                raise Rejected('导出过程中正文发生变化，请重试：' + item['file'])
        atomic(self.state / 'exports' / (batch + '.json'), json.dumps(manifest, ensure_ascii=False).encode())
        output = self.state / 'outbox' / f'dushi-{batch}.txt'
        atomic(output, payload.encode('utf-8'))
        return {'status': 'exported', 'batch': batch, 'file': str(output), 'chapters': len(items)}

    def record(self, data, status):
        self.db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',
                        (data['id'], status, json.dumps(data, ensure_ascii=False)))
        self.db.commit()

    def recover(self):
        for row in self.db.execute("SELECT payload FROM records WHERE status='pending'").fetchall():
            data = json.loads(row[0])
            # 先检查全部文件，第三种状态绝不覆盖。
            for item in data['files']:
                if digest(self.path(item['file']).read_bytes()) not in (item['before'], item['after']):
                    raise Rejected('未完成事务与外部改稿冲突，请保留现场处理：' + item['file'])
            for item in data['files']:
                path = self.path(item['file'])
                if digest(path.read_bytes()) == item['after']:
                    atomic(path, self.read_blob(item['before']), item['mode'])
            data['status'] = 'recovered'
            self.record(data, 'recovered')

    def commit(self, candidates, kind, batch=None, receipt=None, undo_of=None):
        if not candidates:
            result = {'status': 'unchanged', 'chapters': [], 'batch': batch}
        else:
            data = {'id': dt.datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8],
                    'time': now(), 'status': 'applied', 'kind': kind, 'batch': batch,
                    'undo_of': undo_of, 'files': [], 'chapters': [],
                    'char_count_definition': 'Unicode code points，含空格和标点，不含 CR/LF'}
            patches = []
            for item, before, after in candidates:
                path = self.path(item['file'])
                if path.read_bytes() != before:
                    raise Rejected('写入前检测到并发修改：' + item['file'])
                data['files'].append({'file': item['file'], 'num': item['num'],
                                      'mode': path.stat().st_mode & 0o777,
                                      'before': self.blob(before), 'after': self.blob(after)})
                old, new = before.decode('utf-8-sig'), after.decode('utf-8-sig')
                data['chapters'].append(changes(item['num'], item['file'], old, new))
                patches.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                               fromfile='a/正文/' + item['file'], tofile='b/正文/' + item['file']))
            data['totals'] = {k: sum(c[k] for c in data['chapters']) for k in
                              ['added_chars', 'removed_chars', 'net_chars', 'added_lines', 'removed_lines']}
            atomic(self.state / 'reports' / (data['id'] + '.diff'), ''.join(patches).encode())
            self.record(data, 'pending')
            try:
                for item in data['files']:
                    path = self.path(item['file'])
                    if digest(path.read_bytes()) != item['before']:
                        raise Rejected('提交时正文被其他程序修改：' + item['file'])
                    atomic(path, self.read_blob(item['after']), item['mode'])
                for item in data['files']:
                    if digest(self.path(item['file']).read_bytes()) != item['after']:
                        raise Rejected('提交后检测到外部并发修改：' + item['file'])
                # 状态和去重凭据在同一 SQLite 事务提交。
                result = data
                self.db.execute('UPDATE records SET status=? WHERE id=?', ('applied', data['id']))
                if receipt:
                    self.db.execute('INSERT INTO receipts VALUES (?,?)', (receipt, json.dumps(result, ensure_ascii=False)))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                self.recover()
                raise
        if receipt and not candidates:
            self.db.execute('INSERT INTO receipts VALUES (?,?)', (receipt, json.dumps(result, ensure_ascii=False)))
            self.db.commit()
        return result

    def ingest(self, path, check=False):
        with Path(path).open('rb') as f:
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise Rejected('文件超过 50 MiB')
        raw_hash = self.blob(raw)
        try:
            text = decode(raw)
            sha = digest(text.encode())
            found = self.db.execute('SELECT result FROM receipts WHERE sha=?', (sha,)).fetchone()
            if found:
                previous = json.loads(found[0])
                return {'status': 'duplicate', 'original_id': previous.get('id'), 'batch': previous.get('batch')}
            m = re.match(r'^NOVEL-ROUNDTRIP 1 ([a-f0-9]{32})\n', text)
            if not m:
                raise Rejected('导出批次标识缺失或损坏；必须发送“分享改稿 TXT”生成的文件')
            manifest_path = self.state / 'exports' / (m[1] + '.json')
            if not manifest_path.is_file():
                raise Rejected('未知导出批次：' + m[1])
            manifest = json.loads(manifest_path.read_text())
            if manifest['project'] != str(self.project):
                raise Rejected('导出批次属于其他小说')
            parsed = parse(text, manifest)
            baseline = {x['file']: x['hash'] for x in manifest['chapters']}
            for (payload,) in self.db.execute("SELECT payload FROM records WHERE status='applied' ORDER BY rowid"):
                r = json.loads(payload)
                if r['batch'] == m[1] and r['kind'] == 'phone':
                    raise Rejected('本批次已成功回收过其他内容。为避免旧副本覆盖新稿，请重新导出开始下一轮改稿；原样重复发送会自动去重')
            candidates, issues = [], []
            for item, content in parsed:
                current = self.path(item['file']).read_bytes()
                if digest(current) != baseline[item['file']]:
                    issues.append(f"第{item['num']}章：VM 在导出后被其他流程修改，请重新导出后改稿")
                    continue
                encoded = content.replace('\n', item['newline']).encode('utf-8')
                if item['bom']:
                    encoded = b'\xef\xbb\xbf' + encoded
                if encoded != current:
                    candidates.append((item, current, encoded))
            if issues:
                raise Rejected(issues)
            if check:
                return {'status': 'valid', 'batch': m[1], 'changed_chapters': [i[0]['num'] for i in candidates]}
            return self.commit(candidates, 'phone', m[1], sha)
        except Rejected as e:
            error = {'status': 'rejected', 'time': now(), 'issues': e.issues, 'input_sha256': raw_hash}
            atomic(self.state / 'rejected' / (raw_hash + '.json'), json.dumps(error, ensure_ascii=False).encode())
            raise

    def history(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM records WHERE status='applied' ORDER BY rowid DESC")]

    def report(self, key='latest'):
        rows = self.history()
        for r in rows:
            if key == 'latest' or r['id'] == key:
                return r
        return {'status': 'empty', 'chapters': []}

    def undo(self, key):
        record = self.report(key)
        if 'id' not in record:
            raise Rejected('没有可恢复的记录')
        candidates = []
        for item in record['files']:
            current = self.path(item['file']).read_bytes()
            if digest(current) != item['after']:
                raise Rejected('该章已有后续修改，拒绝覆盖：' + item['file'])
            candidates.append((item, current, self.read_blob(item['before'])))
        return self.commit(candidates, 'undo', undo_of=record['id'])


def render(r):
    status = r['status']
    if status != 'applied':
        return json.dumps(r, ensure_ascii=False, indent=2)
    lines = [f"{r['id']}  {r['time']}  {r['kind']}"]
    for c in r['chapters']:
        lines.append(f"\n第{c['chapter']}章 {c['file']}  +{c['added_chars']}字 -{c['removed_chars']}字（净{c['net_chars']:+}）")
        for e in c['edits']:
            lines.append(f"@@ 原 {e['old_start']} 行起 {e['old_count']} 行 → 新 {e['new_start']} 行起 {e['new_count']} 行 @@")
            for offset, text in enumerate(e['removed']):
                lines.append(f"- {e['old_start']+offset}: {text.rstrip(chr(10)).rstrip(chr(13))}")
            for offset, text in enumerate(e['added']):
                lines.append(f"+ {e['new_start']+offset}: {text.rstrip(chr(10)).rstrip(chr(13))}")
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', default=str(Path.home() / 'books/dushi'))
    ap.add_argument('--state', default=str(Path.home() / '.local/state/novel-roundtrip/dushi'))
    ap.add_argument('--json', action='store_true', help='输出已存储的结构化记录，不重新 diff 正文')
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('export', help='导出全部正文及批次快照')
    e.add_argument('--send-qq', action='store_true', help='调用 qq-send-file.py 发到本人 QQ 私聊')
    for name in ['check', 'import']:
        p = sub.add_parser(name, help='仅校验' if name == 'check' else '校验并写回')
        p.add_argument('file')
    p = sub.add_parser('changes', help='读取既有改动记录，默认最近一次')
    p.add_argument('id', nargs='?', default='latest')
    sub.add_parser('history', help='列出改动历史')
    p = sub.add_parser('undo', help='恢复指定回传之前的章节，拒绝覆盖后续修改')
    p.add_argument('id', nargs='?', default='latest')
    a = ap.parse_args()
    try:
        s = Store(a.project, a.state)
        # 查询只读取已提交记录，连未完成事务的正文恢复也留给写入类命令。
        manager = contextlib.nullcontext() if a.cmd in ('changes', 'history') else s.locked()
        with manager:
            if a.cmd == 'export':
                r = s.export()
            elif a.cmd in ('import', 'check'):
                r = s.ingest(a.file, check=a.cmd == 'check')
            elif a.cmd == 'changes':
                r = s.report(a.id)
            elif a.cmd == 'history':
                r = {'status': 'history', 'records': [{k: v for k, v in x.items() if k not in ('files', 'chapters')} for x in s.history()]}
            else:
                r = s.undo(a.id)
        if a.cmd == 'export' and a.send_qq:
            owner_file = Path.home()/'.config/novel-roundtrip/qq-owner'
            if not owner_file.is_file() or not owner_file.read_text().strip():
                raise Rejected('请先配置 ~/.config/novel-roundtrip/qq-owner，避免发给错误联系人')
            run = subprocess.run([sys.executable, str(Path.home()/'.local/bin/qq-send-file.py'),
                                  r['file'], '--c2c', '--openid', owner_file.read_text().strip()],
                                 capture_output=True, text=True)
            r['qq_sent'] = run.returncode == 0
            if run.returncode:
                r['qq_error'] = run.stdout + run.stderr
        print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render(r))
        return 1 if r.get('qq_sent') is False else 0
    except Rejected as e:
        print(json.dumps({'status': 'rejected', 'issues': e.issues}, ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__':
    sys.exit(main())
