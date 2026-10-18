import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError

from manuscript_api import DeltaStore, apply_operations, handle, merge3, SCHEMA
from novel_roundtrip import Rejected


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        project = self.root/'books/demo'
        (project/'正文').mkdir(parents=True)
        self.file = project/'正文/第0001章-测试.md'
        self.raw = '# 第1章 测试\n\n他🙂写了旧句。\n\n这一行不改。\n'
        self.file.write_text(self.raw)
        self.s = DeltaStore(project, self.root/'state/demo')
        self.secret = 'test-only-secret'
        self.snapshot = self.s.snapshot(1, self.secret)

    def tearDown(self):
        self.s.db.close()
        self.temp.cleanup()

    def payload(self, changes=None):
        ops = changes if changes is not None else [dict(offset=self.raw.index('旧'),removed='旧',added='新')]
        after = apply_operations(self.raw, ops)
        return dict(schema=SCHEMA,base_revision=self.snapshot['revision'],
                    after_sha256=hashlib.sha256(after.encode()).hexdigest(), proof=self.snapshot['proof'], changes=ops)

    def test_only_changed_characters_sent(self):
        p = self.payload()
        text = json.dumps(p,ensure_ascii=False)
        self.assertNotIn('这一行不改',text)
        self.assertEqual([{'offset':self.raw.index('旧'),'removed':'旧','added':'新'}],p['changes'])
        r = self.s.apply_delta(1,p,self.secret)
        self.assertEqual('applied',r['status'])
        self.assertEqual(self.raw.replace('旧','新'),self.file.read_text())
        report = self.s.report()
        self.assertEqual('phone',report['kind'])
        self.assertEqual(3,report['chapters'][0]['edits'][0]['old_start'])
        self.assertEqual(1,report['totals']['added_chars'])
        self.assertEqual(1,report['totals']['removed_chars'])

    def test_duplicate_even_after_new_change(self):
        p = self.payload()
        self.s.apply_delta(1,p,self.secret)
        self.file.write_text('external revision')
        self.assertEqual('duplicate',self.s.apply_delta(1,p,self.secret)['status'])
        self.assertEqual('external revision',self.file.read_text())
        self.assertEqual(1,len(self.s.history()))

    def test_undo(self):
        r = self.s.apply_delta(1,self.payload(),self.secret)
        self.s.undo(r['id'])
        self.assertEqual(self.raw,self.file.read_text())

    def test_conflict(self):
        self.file.write_text(self.raw+'VM修改')
        with self.assertRaisesRegex(Rejected,'已在服务器'):
            self.s.apply_delta(1,self.payload(),self.secret)
        self.assertEqual(self.raw+'VM修改',self.file.read_text())

    def test_intent_records_without_writing(self):
        edited = self.raw.replace('旧句', '我的句')
        payload = self.payload([dict(offset=self.raw.index('旧句'), removed='旧句', added='我的句')])
        payload.update(mode='intent', base_raw=self.raw, edited_raw=edited)
        self.file.write_text(self.raw.replace('这一行不改', 'AI改过这一行'))
        result = self.s.record_intent(1, payload, self.secret)
        self.assertEqual('recorded', result['status'])
        self.assertIn('AI改过这一行', self.file.read_text())
        row = self.s.db.execute("SELECT payload FROM records WHERE status='recorded'").fetchone()
        record = json.loads(row[0])
        self.assertEqual(self.raw, record['base_raw'])
        self.assertEqual(edited, record['edited_raw'])
        self.assertTrue(record['server_changed'])

    def auto(self, edited, base=None):
        base = base if base is not None else self.raw
        import difflib
        ops, sm = [], difflib.SequenceMatcher(None, base, edited, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != 'equal':
                ops.append(dict(offset=i1, removed=base[i1:i2], added=edited[j1:j2]))
        return dict(mode='auto', schema=SCHEMA, base_revision=self.snapshot['revision'],
                    after_sha256=hashlib.sha256(edited.encode()).hexdigest(), proof=self.snapshot['proof'],
                    changes=ops, base_raw=base, edited_raw=edited)

    def test_auto_writes_when_server_unchanged(self):
        edited = self.raw.replace('旧句', '我的句')
        r = self.s.save_auto(1, self.auto(edited), self.secret)
        self.assertEqual('applied', r['status'])
        self.assertEqual(edited, self.file.read_text())

    def test_auto_merges_different_paragraphs(self):
        edited = self.raw.replace('旧句', '我的句')
        self.file.write_text(self.raw.replace('这一行不改', 'AI改过这一行'))
        r = self.s.save_auto(1, self.auto(edited), self.secret)
        self.assertEqual('merged', r['status'])
        self.assertEqual(self.raw.replace('旧句', '我的句').replace('这一行不改', 'AI改过这一行'), self.file.read_text())
        rec = self.s.history()[0]
        self.assertEqual('phone-merge', rec['kind'])
        self.assertEqual(edited, rec['merge']['edited_raw'])
        retry = self.s.save_auto(1, self.auto(edited), self.secret)
        self.assertEqual(('duplicate', 'applied'), (retry['status'], retry['result']))

    def test_auto_same_edit_on_both_sides_kept_once(self):
        edited = self.raw.replace('旧句', '新句')
        self.file.write_text(edited)
        r = self.s.save_auto(1, self.auto(edited), self.secret)
        self.assertEqual('unchanged', r['status'])
        self.assertEqual(edited, self.file.read_text())

    def test_auto_conflict_records_without_writing(self):
        ai = self.raw.replace('旧句', 'AI句')
        self.file.write_text(ai)
        edited = self.raw.replace('旧句', '我的句').replace('这一行不改', '我也改这行')
        r = self.s.save_auto(1, self.auto(edited), self.secret)
        self.assertEqual(('conflict', 1), (r['status'], r['conflicts']))
        self.assertEqual(ai, self.file.read_text())
        self.assertEqual([], self.s.history())
        rec = self.s.history(True)[0]
        self.assertEqual('phone-conflict', rec['kind'])
        self.assertIn('AI句', rec['conflicts'][0]['ai'])
        self.assertIn('我的句', rec['conflicts'][0]['user'])
        self.assertIn('我也改这行', rec['merged_preview'])
        self.assertEqual('duplicate', self.s.save_auto(1, self.auto(edited), self.secret)['status'])

    def test_auto_merge_preserves_crlf_bom(self):
        raw = b'\xef\xbb\xbf' + self.raw.replace('\n', '\r\n').encode()
        self.file.write_bytes(raw)
        self.snapshot = self.s.snapshot(1, self.secret)
        self.file.write_bytes(raw.replace('这一行不改'.encode(), 'AI改'.encode()))
        r = self.s.save_auto(1, self.auto(self.raw.replace('旧句', '我的句')), self.secret)
        self.assertEqual('merged', r['status'])
        self.assertEqual(raw.replace('旧句'.encode(), '我的句'.encode()).replace('这一行不改'.encode(), 'AI改'.encode()),
                         self.file.read_bytes())

    def test_auto_rejects_inconsistent_changes(self):
        p = self.auto(self.raw.replace('旧句', '我的句'))
        p['changes'] = []
        with self.assertRaisesRegex(Rejected, '不一致'):
            self.s.save_auto(1, p, self.secret)
        self.assertEqual(self.raw, self.file.read_text())

    def test_merge3_insertions(self):
        base = '# T\n\nA\n\nB\n'
        merged, conflicts = merge3(base, '# T\n\nA\n\nAI新段\n\nB\n', '# T\n\nA\n\nB\n\n我加的结尾\n')
        self.assertEqual([], conflicts)
        self.assertEqual('# T\n\nA\n\nAI新段\n\nB\n\n我加的结尾\n', merged)
        _, conflicts = merge3(base, '# T\n\nA\n\nX\n\nB\n', '# T\n\nA\n\nY\n\nB\n')
        self.assertEqual(1, len(conflicts))

    def test_indent_only_ai_change_is_not_conflict(self):
        base = '# T\n\n旧句子很长。\n\nB\n'
        merged, conflicts = merge3(base, '# T\n\n\u3000\u3000旧句子很长。\n\nB\n', '# T\n\n新句子。\n\nB\n')
        self.assertEqual([], conflicts)
        self.assertIn('\u3000\u3000新句子。\n', merged)

    def test_bad_removed(self):
        p = self.payload();p['changes'][0]['removed']='错误原文'
        with self.assertRaisesRegex(Rejected,'原文不匹配'):
            self.s.apply_delta(1,p,self.secret)
        self.assertEqual(self.raw,self.file.read_text())

    def test_bad_after_hash(self):
        p=self.payload();p['after_sha256']='a'*64
        with self.assertRaisesRegex(Rejected,'校验值不匹配'): self.s.apply_delta(1,p,self.secret)
        self.assertEqual(self.raw,self.file.read_text())

    def test_wrong_proof(self):
        p=self.payload();p['proof']='bad'
        with self.assertRaisesRegex(Rejected,'凭据无效'): self.s.apply_delta(1,p,self.secret)

    def test_noop(self):
        self.assertEqual('unchanged',self.s.apply_delta(1,self.payload([]),self.secret)['status'])
        self.assertEqual([],self.s.history())

    def test_multi_save_fresh_snapshots(self):
        self.s.apply_delta(1,self.payload(),self.secret)
        self.raw=self.file.read_text();self.snapshot=self.s.snapshot(1,self.secret)
        p=self.payload([dict(offset=self.raw.index('新'),removed='新',added='更好')])
        self.s.apply_delta(1,p,self.secret)
        self.assertEqual(2,len(self.s.history()))

    def test_unicode_and_newlines(self):
        ops=[dict(offset=self.raw.index('🙂'),removed='🙂',added='🦋'),
             dict(offset=self.raw.index('这一行'),removed='',added='新段。\n\n')]
        self.s.apply_delta(1,self.payload(ops),self.secret)
        self.assertIn('他🦋',self.file.read_text())
        self.assertIn('新段。\n\n这一行',self.file.read_text())

    def test_overlap_invalid_and_empty_chapter(self):
        for ops in [[dict(offset=-1,removed='',added='x')],
                    [dict(offset=0,removed='#',added=''),dict(offset=0,removed='#',added='')],
                    [dict(offset=0,removed=self.raw,added='')],
                    [dict(offset=2,removed='',added='\ud800')],
                    [dict(offset=2,removed='',added='\ufeff')]]:
            with self.assertRaises(Rejected): apply_operations(self.raw,ops)

    def test_source_bom_crlf_preserved(self):
        raw=b'\xef\xbb\xbf'+self.raw.replace('\n','\r\n').encode()
        self.file.write_bytes(raw)
        self.snapshot=self.s.snapshot(1,self.secret)
        self.s.apply_delta(1,self.payload(),self.secret)
        self.assertEqual(raw.replace('旧'.encode(),'新'.encode()),self.file.read_bytes())

    def test_http_auth_write_retry_read_and_undo(self):
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def _send(self,obj,code=200):
                data=json.dumps(obj,ensure_ascii=False).encode()
                self.send_response(code);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def do_GET(self): handle(self,self.path,outer.root/'books',outer.secret,'Basic testing',outer.root/'state')
            do_POST=do_GET
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            opener=build_opener(ProxyHandler({}));url=f'http://127.0.0.1:{server.server_port}/manuscript/demo/1'
            def call(payload=None,auth=True):
                req=Request(url,data=json.dumps(payload).encode() if payload else None,
                            headers={'Authorization':'Basic testing'} if auth else {})
                with opener.open(req) as r:return json.load(r)
            with self.assertRaises(HTTPError) as e:call(auth=False)
            self.assertEqual(403,e.exception.code)
            self.assertEqual(self.raw,call()['raw'])
            p=self.payload();self.assertEqual('applied',call(p)['status'])
            self.assertEqual('duplicate',call(p)['status'])
            self.assertEqual(self.raw.replace('旧','新'),call()['raw'])
        finally:server.shutdown();server.server_close();t.join()


if __name__=='__main__': unittest.main()
