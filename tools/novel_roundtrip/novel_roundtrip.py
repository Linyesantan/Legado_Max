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


def _hunks(base, other):
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal']


def _clash(x, y):
    """两处按段落的改动是否落在同一位置；相邻但不重叠的段落可以各自保留。"""
    if x[0] == x[1] and y[0] == y[1]:
        return x[0] == y[0]
    if x[0] == x[1]:
        return y[0] < x[0] < y[1]
    if y[0] == y[1]:
        return x[0] < y[0] < x[1]
    return x[0] < y[1] and y[0] < x[1]


def _region(base, hunks, start, end):
    out, cursor = [], start
    for i1, i2, lines in hunks:
        out.extend(base[cursor:i1])
        out.extend(lines)
        cursor = i2
    out.extend(base[cursor:end])
    return ''.join(out)


_INDENT = ' \u3000\t'


def _strip_lines(text):
    return [l.lstrip(_INDENT) for l in text.splitlines(True)]


def _indent_only(base_text, ai_text):
    return base_text != ai_text and _strip_lines(base_text) == _strip_lines(ai_text)


def _apply_indent(ai_text, user_text):
    ai_lines = [l for l in ai_text.splitlines(True) if l.strip()]
    prefix = ''
    if ai_lines and all(l.startswith('\u3000\u3000') for l in ai_lines if not l.lstrip(_INDENT).startswith('#')):
        prefix = '\u3000\u3000'
    if not prefix:
        return user_text
    out = []
    for l in user_text.splitlines(True):
        body = l.lstrip(_INDENT)
        out.append(prefix + body if body.strip() and not body.startswith('#') else l)
    return ''.join(out)


def merge3(base_raw, ai_raw, user_raw):
    """按段落三方合并：不同段直接合并；同段改成一样只留一份；同段改得不同算冲突。

    返回 (合并稿, 冲突列表)。冲突位置在合并稿里先采用用户版本，仅作预览。"""
    base = base_raw.splitlines(True)
    events = sorted([(h, 'ai') for h in _hunks(base, ai_raw.splitlines(True))] +
                    [(h, 'user') for h in _hunks(base, user_raw.splitlines(True))],
                    key=lambda e: (e[0][0], e[0][1]))
    clusters = []
    for hunk, side in events:
        if clusters and any(_clash(hunk, h) for h, _ in clusters[-1]):
            clusters[-1].append((hunk, side))
        else:
            clusters.append([(hunk, side)])
    out, conflicts, cursor = [], [], 0
    for cluster in clusters:
        start = min(h[0] for h, _ in cluster)
        end = max(h[1] for h, _ in cluster)
        out.append(''.join(base[cursor:start]))
        ai = [h for h, side in cluster if side == 'ai']
        user = [h for h, side in cluster if side == 'user']
        user_text = _region(base, user, start, end)
        if ai and user:
            ai_text = _region(base, ai, start, end)
            base_text = ''.join(base[start:end])
            if _indent_only(base_text, ai_text):
                # AI 只规范了段首缩进：用用户正文，套上 AI 的缩进，不算冲突。
                user_text = _apply_indent(ai_text, user_text)
            elif ai_text != user_text:
                conflicts.append({'line': start + 1, 'base': ''.join(base[start:end]),
                                  'ai': ai_text, 'user': user_text})
            out.append(user_text)
        else:
            out.append(_region(base, ai or user, start, end))
        cursor = end
    out.append(''.join(base[cursor:]))
    return ''.join(out), conflicts


def validate_text(text):
    """写入前的通用校验：标题行存在、正文非空、无保留标识和控制字符。"""
    if PREFIX in text or MAGIC in text:
        raise Rejected('正文混入保留标识')
    if not re.match(r'^# [^\n]+\n', text):
        raise Rejected('首行必须是“# 标题”')
    if not text.partition('\n')[2].strip():
        raise Rejected('正文为空，拒绝整章清空')


def encode_like(text, current):
    """按现有文件的 BOM 和行尾写回，参照 save_auto。"""
    output = text.replace('\n', '\r\n' if b'\r\n' in current else '\n').encode('utf-8')
    if current.startswith(b'\xef\xbb\xbf'):
        output = b'\xef\xbb\xbf' + output
    return output


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

    def item(self, number):
        matches = [p.name for p in self.body.glob('*.md')
                   if (m := re.fullmatch(r'第(\d+)章-.+\.md', p.name)) and int(m[1]) == number]
        if len(matches) != 1:
            raise Rejected(f'第{number}章缺失或章号重复')
        return {'num': number, 'file': matches[0]}

    def last_after(self, filename):
        """history 里最后一次写这个文件后的快照；没有记录返回 None。"""
        for (payload,) in self.db.execute("SELECT payload FROM records WHERE status='applied' ORDER BY rowid DESC"):
            record = json.loads(payload)
            for item in record.get('files') or []:
                if item.get('file') == filename:
                    return item['after']
        return None

    def note_external(self, item, data):
        """绕过本工具改了正文：补一条 kind=external 记录，让 history 和 undo 都能看到。

        判断依据是文件 sha 与 history 最后一次 after 不同；写回成功后 after 会更新，
        所以同一次外部改动只会记录一次。"""
        expected = self.last_after(item['file'])
        if expected is None or digest(data) == expected:
            return None
        try:
            before = self.read_blob(expected)
        except (OSError, Rejected):
            return None
        try:
            old_text = decode(before)
        except Rejected:
            old_text = ''
        new_text = decode(data)
        record = {'id': 'external-' + uuid.uuid4().hex, 'time': now(), 'status': 'applied',
                  'kind': 'external', 'batch': None, 'undo_of': None,
                  'files': [{'file': item['file'], 'num': item['num'],
                             'mode': self.path(item['file']).stat().st_mode & 0o777,
                             'before': expected, 'after': self.blob(data)}],
                  'chapters': [changes(item['num'], item['file'], old_text, new_text)]}
        record['totals'] = {k: sum(c[k] for c in record['chapters']) for k in
                            ['added_chars', 'removed_chars', 'net_chars', 'added_lines', 'removed_lines']}
        self.record(record, 'applied')
        return record

    def snapshot(self, number):
        item = self.item(number)
        data = self.path(item['file']).read_bytes()
        self.note_external(item, data)
        # 基准快照必须落盘：章节从没进过 history 时，write --base 也要能取回 base。
        self.blob(data)
        return {'chapter': number, 'file': item['file'], 'revision': digest(data), 'raw': decode(data)}

    def write_ai(self, number, base, new_data):
        """AI 改稿：基准一致直接写；否则与当前正文三方合并，冲突则不写。"""
        if not isinstance(base, str) or not re.fullmatch(r'[a-f0-9]{64}', base):
            raise Rejected('--base 必须是 64 位 sha256')
        item = self.item(number)
        current = self.path(item['file']).read_bytes()
        self.note_external(item, current)
        ai_raw = decode(new_data)
        validate_text(ai_raw)
        if digest(current) == base:
            output = encode_like(ai_raw, current)
            result = self.commit([(item, current, output)] if output != current else [], 'ai')
            result.update(chapter=number, file=item['file'], revision=digest(output), merged=False)
            return result
        try:
            base_raw = decode(self.read_blob(base))
        except (OSError, Rejected):
            raise Rejected('找不到基准快照 ' + base + '，请重新 snapshot')
        current_raw = decode(current)
        # 角色：base=基准，ai=AI 新稿，user=服务器当前内容（含手机改动）。
        # 冲突时保留用户侧，AI 不能覆盖手机改过的段落。
        merged, conflicts = merge3(base_raw, ai_raw, current_raw)
        if conflicts:
            record = {'id': 'conflict-' + uuid.uuid4().hex, 'time': now(), 'status': 'conflict',
                      'kind': 'ai-conflict', 'chapter': number, 'file': item['file'],
                      'base_revision': base, 'server_revision': digest(current),
                      'base_raw': base_raw, 'server_raw': current_raw, 'ai_raw': ai_raw,
                      'merged_preview': merged, 'conflicts': conflicts, 'server_changed': True}
            self.record(record, 'conflict')
            return {'status': 'conflict', 'id': record['id'], 'chapter': number,
                    'file': item['file'], 'conflicts': conflicts,
                    'base_raw': base_raw, 'server_raw': current_raw, 'ai_raw': ai_raw,
                    'merged_preview': merged}
        validate_text(merged)
        output = encode_like(merged, current)
        result = self.commit([(item, current, output)] if output != current else [], 'ai-merge')
        result.update(chapter=number, file=item['file'], revision=digest(output), merged=True)
        return result

    def export(self):
        batch = uuid.uuid4().hex
        items, texts = [], []
        for p in self.body.glob('*.md'):
            m = re.fullmatch(r'第(\d+)章-.+\.md', p.name)
            if not m:
                raise Rejected('无法识别章节文件名：' + p.name)
            data = self.path(p.name).read_bytes()
            self.note_external({'num': int(m[1]), 'file': p.name}, data)
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
        output = self.state / 'outbox' / f'{self.project.name}-{batch}.txt'
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
                    issues.append(f"第{item['num']}章：服务器正文在导出后被其他流程修改，请重新导出后改稿")
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

    def history(self, pending=False):
        # pending=True 时一并列出未写入正文的冲突/意图记录，供 AI 处理；撤销只看已写入记录。
        statuses = ('applied', 'conflict', 'recorded') if pending else ('applied',)
        marks = ','.join('?' * len(statuses))
        return [json.loads(r[0]) for r in self.db.execute(
            f"SELECT payload FROM records WHERE status IN ({marks}) ORDER BY rowid DESC", statuses)]

    def open_conflicts(self):
        # 未处理的冲突：正文没写入，必须优先呈现给用户，不能被后面的改动挤掉。
        out = []
        for (payload,) in self.db.execute("SELECT payload FROM records WHERE status='conflict' ORDER BY rowid DESC"):
            r = json.loads(payload)
            out.append({'id': r['id'], 'time': r.get('time'), 'chapter': r.get('chapter'), 'file': r.get('file'),
                        'conflicts': len(r.get('conflicts', []))})
        return out

    def resolve(self, key):
        row = self.db.execute("SELECT payload FROM records WHERE id=? AND status='conflict'", (key,)).fetchone()
        if not row:
            raise Rejected('没有这条未处理冲突：' + key)
        data = json.loads(row[0])
        data['status'] = 'resolved'
        data['resolved_time'] = now()
        self.record(data, 'resolved')
        return {'status': 'resolved', 'id': key}

    def report(self, key='latest', pending=False):
        rows = self.history(pending)
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
    text = _render(r)
    if r.get('open_conflicts'):
        text += '\n\n⚠ 未处理冲突（正文未写入）：' + '；'.join(f"{c['id']} 第{c['chapter']}章 {c['conflicts']}处" for c in r['open_conflicts'])
    return text


def _render(r):
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


def render_conflict(r):
    lines = [f"{r['id']}  第{r['chapter']}章 {r['file']}  status=conflict  {len(r['conflicts'])} 处冲突",
             '正文未写入。三版对比：']
    for c in r['conflicts']:
        lines.append(f"\n@@ 基准 第 {c['line']} 行 @@\n{c['base'].rstrip()}")
        lines.append(f"@@ 服务器当前（含手机改动，保留） @@\n{c['user'].rstrip()}")
        lines.append(f"@@ AI 新稿（未采用） @@\n{c['ai'].rstrip()}")
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', default=os.environ.get('NOVEL_PROJECT', str(Path.home() / 'books/novel')),
                    help='小说项目目录，默认取 NOVEL_PROJECT')
    ap.add_argument('--state', default=os.environ.get('NOVEL_STATE'),
                    help='历史状态目录，默认 ~/.local/state/novel-roundtrip/<项目目录名>')
    ap.add_argument('--json', action='store_true', help='输出已存储的结构化记录，不重新 diff 正文')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('export', help='导出全部正文及批次快照')
    for name in ['check', 'import']:
        p = sub.add_parser(name, help='仅校验' if name == 'check' else '校验并写回')
        p.add_argument('file')
    p = sub.add_parser('snapshot', help='读取章节基准快照（只读，不改正文）')
    p.add_argument('chapter', type=int)
    p = sub.add_parser('write', help='按基准写入 AI 改稿；基准已变则三方合并，冲突不写')
    p.add_argument('chapter', type=int)
    p.add_argument('--base', required=True, help='snapshot 返回的 revision')
    p.add_argument('--file', required=True, help='AI 新稿路径')
    p = sub.add_parser('changes', help='读取既有改动记录，默认最近一次')
    p.add_argument('id', nargs='?', default='latest')
    p.add_argument('--limit', type=int, default=1, help='一次读取最近 N 次记录，供写作上下文使用')
    sub.add_parser('history', help='列出改动历史')
    p = sub.add_parser('resolve', help='用户已裁决并处理完的冲突，标记为已处理')
    p.add_argument('id')
    p = sub.add_parser('undo', help='恢复指定回传之前的章节，拒绝覆盖后续修改')
    p.add_argument('id', nargs='?', default='latest')
    a = ap.parse_args()
    try:
        s = Store(a.project, a.state or str(Path.home() / '.local/state/novel-roundtrip' / Path(a.project).name))
        # 查询只读取已提交记录，连未完成事务的正文恢复也留给写入类命令。
        manager = contextlib.nullcontext() if a.cmd in ('changes', 'history') else s.locked()
        with manager:
            if a.cmd == 'export':
                r = s.export()
            elif a.cmd in ('import', 'check'):
                r = s.ingest(a.file, check=a.cmd == 'check')
            elif a.cmd == 'snapshot':
                r = s.snapshot(a.chapter)
            elif a.cmd == 'write':
                with Path(a.file).open('rb') as f:
                    new_data = f.read(MAX_BYTES + 1)
                if len(new_data) > MAX_BYTES:
                    raise Rejected('文件超过 50 MiB')
                r = s.write_ai(a.chapter, a.base, new_data)
            elif a.cmd == 'changes':
                if a.limit < 1 or a.limit > 100:
                    raise Rejected('--limit 必须在 1 到 100 之间')
                r = ({'status': 'changes', 'records': s.history(True)[:a.limit]}
                     if a.limit > 1 and a.id == 'latest' else s.report(a.id, True))
                pending = [c for c in s.open_conflicts() if c['id'] != r.get('id')]
                if pending:
                    r['open_conflicts'] = pending
            elif a.cmd == 'history':
                r = {'status': 'history', 'records': [{k: v for k, v in x.items() if k not in ('files', 'chapters', 'base_raw', 'server_raw', 'edited_raw', 'ai_raw', 'merged_preview', 'merge', 'conflicts')} for x in s.history(True)]}
            elif a.cmd == 'resolve':
                r = s.resolve(a.id)
            else:
                r = s.undo(a.id)
        if r.get('status') == 'conflict':
            print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else render_conflict(r))
            # 只有 write 把冲突当失败；changes 查询到历史冲突破仍是正常退出。
            return 3 if a.cmd == 'write' else 0
        human = not a.json and a.cmd != 'snapshot' and r.get('status') == 'applied'
        print(json.dumps(r, ensure_ascii=False, indent=2) if not human else render(r))
        return 0
    except Rejected as e:
        print(json.dumps({'status': 'rejected', 'issues': e.issues}, ensure_ascii=False, indent=2))
        return 2


if __name__ == '__main__':
    sys.exit(main())
