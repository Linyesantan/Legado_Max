"""Authenticated chapter snapshots and exact insertion/deletion updates over AWS relay."""
import hashlib
import hmac
import json
from pathlib import Path
import re

from novel_roundtrip import Store, Rejected, decode, digest

SCHEMA = 'novel-delta/1'
MAX_PATCH = 2 * 1024 * 1024


def signature(secret, slug, chapter, revision):
    return hmac.new(secret.encode(), f'{SCHEMA}:{slug}:{chapter}:{revision}'.encode(), hashlib.sha256).hexdigest()


def apply_operations(raw, operations):
    if not isinstance(operations, list) or len(operations) > 10000:
        raise Rejected('改动列表格式错误或过长')
    cursor = 0
    previous = -1
    chunks = []
    for op in operations:
        if not isinstance(op, dict) or set(op) != {'offset', 'removed', 'added'}:
            raise Rejected('增删操作字段错误')
        offset, removed, added = op['offset'], op['removed'], op['added']
        if type(offset) is not int or not isinstance(removed, str) or not isinstance(added, str):
            raise Rejected('增删操作类型错误')
        if offset < cursor or offset <= previous or offset > len(raw):
            raise Rejected('增删位置越界、重叠或乱序')
        if not removed and not added:
            raise Rejected('空增删操作')
        if raw[offset:offset + len(removed)] != removed:
            raise Rejected(f'第 {offset} 字处的被删原文不匹配，拒绝猜测合并')
        chunks.extend((raw[cursor:offset], added))
        cursor = offset + len(removed)
        previous = offset
    chunks.append(raw[cursor:])
    result = ''.join(chunks)
    try:
        encoded = result.encode('utf-8', errors='strict')
    except UnicodeError:
        raise Rejected('改动含无效 Unicode 字符')
    if len(encoded) > MAX_PATCH or '\x00' in result or '\ufeff' in result or '\r' in result:
        raise Rejected('改后内容过大或包含非法控制字符')
    if not re.match(r'^# [^\n]+\n', result) or not result.partition('\n')[2].strip():
        raise Rejected('标题缺失或整章正文被清空')
    return result


class DeltaStore(Store):
    def item(self, number):
        matches = [p.name for p in self.body.glob('*.md')
                   if (m := re.fullmatch(r'第(\d+)章-.+\.md', p.name)) and int(m[1]) == number]
        if len(matches) != 1:
            raise Rejected(f'第{number}章缺失或章号重复')
        return {'num': number, 'file': matches[0]}

    def snapshot(self, number, secret):
        item = self.item(number)
        data = self.path(item['file']).read_bytes()
        # 导出与回写都保留原文件 BOM 和一致的行尾，不猜混合换行。
        if b'\r' in data.replace(b'\r\n', b'') or (b'\r\n' in data and b'\n' in data.replace(b'\r\n', b'')):
            raise Rejected('源章节使用混合换行，拒绝无损校验不明确的改稿')
        raw = decode(data)
        apply_operations(raw, [])
        revision = self.blob(data)
        return {'schema': SCHEMA, 'chapter': number, 'title': raw.split('\n', 1)[0][2:],
                'raw': raw, 'revision': revision,
                'proof': signature(secret, self.project.name, number, revision)}

    def apply_delta(self, number, payload, secret, check=False):
        if not isinstance(payload, dict) or set(payload) != {'schema', 'base_revision', 'after_sha256', 'proof', 'changes'}:
            raise Rejected('改动包字段错误')
        if payload['schema'] != SCHEMA:
            raise Rejected('不支持的改动包版本')
        base, after = payload['base_revision'], payload['after_sha256']
        if not isinstance(base, str) or not re.fullmatch('[a-f0-9]{64}', base):
            raise Rejected('原版本校验值错误')
        if not isinstance(after, str) or not re.fullmatch('[a-f0-9]{64}', after):
            raise Rejected('目标版本校验值错误')
        proof = signature(secret, self.project.name, number, base)
        if not isinstance(payload['proof'], str) or not hmac.compare_digest(proof, payload['proof']):
            raise Rejected('改稿校验凭据无效，请重新打开编辑器')
        canonical = json.dumps({'chapter': number, **payload}, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()
        receipt = 'aws:' + digest(canonical)
        old_receipt = self.db.execute('SELECT result FROM receipts WHERE sha=?', (receipt,)).fetchone()
        if old_receipt:
            previous = json.loads(old_receipt[0])
            return {'status': 'duplicate', 'id': previous.get('id'), 'message': '已经保存，未重复写入'}
        item = self.item(number)
        current = self.path(item['file']).read_bytes()
        if digest(current) != base:
            raise Rejected(f'第{number}章已在 VM 或其他设备修改，当前草稿已保留，请重新核对；未覆盖新稿')
        new = apply_operations(decode(current), payload['changes'])
        if digest(new.encode('utf-8')) != after:
            raise Rejected('应用增删后的全文校验值不匹配，未写入')
        # 再检查源格式，不让直接构造的请求绕过 snapshot 的格式校验。
        self.snapshot(number, secret)
        output = new.replace('\n', '\r\n' if b'\r\n' in current else '\n').encode('utf-8')
        if current.startswith(b'\xef\xbb\xbf'):
            output = b'\xef\xbb\xbf' + output
        if check:
            return {'status': 'valid', 'chapter': number, 'changed': output != current}
        self.blob(canonical)
        result = self.commit([(item, current, output)] if output != current else [], 'phone-aws', receipt=receipt)
        return {'status': result['status'], 'id': result.get('id'), 'chapter': number,
                'totals': result.get('totals', {}), 'revision': digest(output)}


def strict_object(pairs):
    d = {}
    for key, value in pairs:
        if key in d:
            raise Rejected('JSON 字段重复')
        d[key] = value
    return d


def handle(handler, path, books_root, secret, expected_auth, state_root=None):
    match = re.fullmatch(r'/manuscript/([a-zA-Z0-9_-]+)/(\d+)', path)
    if not match:
        return False
    def send(data, code=200):
        handler._send(data, code=code)
        return True
    if not secret or not expected_auth or not hmac.compare_digest(handler.headers.get('Authorization', ''), expected_auth):
        return send({'error': '改稿接口认证失败'}, 403)
    slug, number = match[1], int(match[2])
    project = Path(books_root) / slug
    if not (project / '正文').is_dir():
        return send({'error': '没有这本小说'}, 404)
    try:
        store = DeltaStore(project, (Path(state_root) if state_root else Path.home()/'.local/state/novel-roundtrip') / slug)
        try:
            with store.locked():
                if handler.command == 'GET':
                    return send(store.snapshot(number, secret))
                if handler.command != 'POST':
                    return send({'error': '不支持的方法'}, 405)
                length = int(handler.headers.get('Content-Length', '0'))
                if length <= 0 or length > MAX_PATCH:
                    return send({'error': '改动包长度无效'}, 413)
                raw = handler.rfile.read(length)
                if len(raw) != length:
                    raise Rejected('改动包被截断')
                store.blob(raw)
                payload = json.loads(raw.decode('utf-8-sig', errors='strict'), object_pairs_hook=strict_object)
                return send(store.apply_delta(number, payload, secret))
        finally:
            store.db.close()
    except Rejected as e:
        return send({'status': 'rejected', 'error': str(e), 'issues': e.issues}, 409)
    except (ValueError, UnicodeError, TypeError):
        return send({'status': 'rejected', 'error': '改动包不是有效 JSON/UTF-8'}, 400)
