import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

import novel_roundtrip as nr


class RoundtripTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root / 'book'
        self.body = self.project / '正文'
        self.body.mkdir(parents=True)
        self.original = {
            '第0001章-开始.md': '# 第1章 开始\n\n　　旧句。 \n\n第二段🙂\n'.encode(),
            '第0002章-接续.md': '# 第2章 接续\n\n另一个人。\n'.encode(),
        }
        for name, data in self.original.items():
            (self.body / name).write_bytes(data)
        (self.project / '.webnovel').mkdir()
        (self.project / '.webnovel/state.json').write_text('do not touch')
        self.store = nr.Store(self.project, root / 'state')
        self.export = self.store.export()
        self.file = Path(self.export['file'])
        self.text = self.file.read_text()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def incoming(self, text):
        p = Path(self.temp.name) / 'incoming.txt'
        p.write_bytes(text.encode('utf-8'))
        return p

    def assertOriginal(self):
        for name, data in self.original.items():
            self.assertEqual(data, (self.body / name).read_bytes())

    def rejected(self, text, message):
        with self.assertRaisesRegex(nr.Rejected, message):
            self.store.ingest(self.incoming(text))
        self.assertOriginal()
        self.assertEqual([], self.store.history())

    def test_unchanged_byte_exact(self):
        self.assertEqual('unchanged', self.store.ingest(self.file)['status'])
        self.assertOriginal()
        self.assertEqual([], self.store.history())

    def test_bom_crlf_duplicate(self):
        incoming = self.incoming('\ufeff' + self.text.replace('旧句', '新句').replace('\n', '\r\n'))
        r = self.store.ingest(incoming)
        self.assertEqual('applied', r['status'])
        self.assertEqual(3, r['chapters'][0]['edits'][0]['old_start'])
        self.assertEqual(['　　旧句。 \n'], r['chapters'][0]['edits'][0]['removed'])
        self.assertEqual(1, r['totals']['added_chars'])
        self.assertEqual(1, r['totals']['removed_chars'])
        self.assertEqual('duplicate', self.store.ingest(self.incoming(self.text.replace('旧句', '新句')))['status'])
        self.assertEqual(1, len(self.store.history()))

    def test_undo_exact_and_receipt_survives(self):
        f = self.incoming(self.text.replace('旧句', '新句'))
        r = self.store.ingest(f)
        self.store.undo(r['id'])
        self.assertOriginal()
        self.assertEqual('duplicate', self.store.ingest(f)['status'])

    def test_no_change_to_other_files(self):
        self.store.ingest(self.incoming(self.text.replace('旧句', '新句')))
        self.assertEqual('do not touch', (self.project / '.webnovel/state.json').read_text())
        self.assertEqual({'正文', '.webnovel'}, {p.name for p in self.project.iterdir()})

    def test_missing_title(self):
        self.rejected(self.text.replace('# 第2章 接续\n', ''), '第2章.*首行')

    def test_missing_chapter(self):
        start = self.text.index('@@NOVEL:BEGIN:2@@')
        end = self.text.index('@@NOVEL:EOF:')
        self.rejected(self.text[:start] + self.text[end:], '第2章.*缺失')

    def test_reordered(self):
        start = self.text.index('@@NOVEL:BEGIN:1@@')
        second = self.text.index('@@NOVEL:BEGIN:2@@')
        end = self.text.index('@@NOVEL:EOF:')
        self.rejected(self.text[:start]+self.text[second:end]+self.text[start:second]+self.text[end:], '顺序错误')

    def test_duplicate_chapter(self):
        start = self.text.index('@@NOVEL:BEGIN:1@@')
        end = self.text.index('@@NOVEL:BEGIN:2@@')
        self.rejected(self.text[:end]+self.text[start:end]+self.text[end:], '第1章.*重复')

    def test_missing_end(self):
        self.rejected(self.text.replace('@@NOVEL:END:1@@', ''), '第1章.*结束边界')

    def test_truncated(self):
        self.rejected(self.text[:-20], '结束标识')

    def test_extra_content(self):
        self.rejected(self.text+'多余文字', '多余内容')

    def test_empty_chapter(self):
        self.rejected(self.text.replace('\n\n另一个人。\n', '\n'), '第2章.*正文为空')

    def test_wrong_batch(self):
        self.rejected(self.text.replace(self.export['batch'], 'a'*32), '未知导出批次')

    def test_invalid_utf8(self):
        self.file.write_bytes(b'\xff')
        with self.assertRaisesRegex(nr.Rejected, 'UTF-8'):
            self.store.ingest(self.file)
        self.assertOriginal()

    def test_vm_conflict_all_or_nothing(self):
        second = self.body / '第0002章-接续.md'
        second.write_bytes(second.read_bytes()+b'server edit\n')
        with self.assertRaisesRegex(nr.Rejected, '第2章.*服务器'):
            self.store.ingest(self.incoming(self.text.replace('旧句', '新句')))
        self.assertEqual(self.original['第0001章-开始.md'], (self.body/'第0001章-开始.md').read_bytes())
        self.assertTrue(second.read_bytes().endswith(b'server edit\n'))

    def test_other_revision_of_consumed_export_rejected(self):
        self.store.ingest(self.incoming(self.text.replace('旧句', '新句')))
        with self.assertRaisesRegex(nr.Rejected, '本批次已成功回收'):
            self.store.ingest(self.incoming(self.text.replace('旧句', '更新句')))
        self.assertIn('新句', (self.body/'第0001章-开始.md').read_text())

    def test_check_never_writes(self):
        r = self.store.ingest(self.incoming(self.text.replace('旧句', '新句')), check=True)
        self.assertEqual([1], r['changed_chapters'])
        self.assertOriginal()

    def test_report_does_not_read_manuscript_or_diff(self):
        self.store.ingest(self.incoming(self.text.replace('旧句', '新句')))
        with patch.object(nr.difflib, 'SequenceMatcher', side_effect=AssertionError('must not diff')):
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('must not read book')):
                self.assertEqual('applied', self.store.report()['status'])

    def test_failed_second_write_rolls_back(self):
        atomic = nr.atomic
        failed = False
        def fail(path, data, mode=0o600):
            nonlocal failed
            if path == self.body/'第0002章-接续.md' and not failed:
                failed = True
                raise OSError('simulated disk failure')
            return atomic(path, data, mode)
        incoming = self.incoming(self.text.replace('旧句', '新句').replace('另一个人', '另外两人'))
        with patch.object(nr, 'atomic', side_effect=fail):
            with self.assertRaises(OSError):
                self.store.ingest(incoming)
        self.assertOriginal()
        self.assertEqual([], self.store.history())
        self.assertEqual('applied', self.store.ingest(incoming)['status'])

    def test_recover_process_death(self):
        path = self.body/'第0001章-开始.md'
        before = path.read_bytes()
        after = before.replace('旧句'.encode(), '新句'.encode())
        self.store.record({'id':'crash', 'files':[{'file':path.name,'before':self.store.blob(before),
                         'after':self.store.blob(after),'mode':0o644}]}, 'pending')
        path.write_bytes(after)
        self.store.recover()
        self.assertOriginal()

    def test_symlink_rejected(self):
        p = self.body/'第0001章-开始.md'
        p.unlink()
        p.symlink_to(self.project/'.webnovel/state.json')
        with self.assertRaisesRegex(nr.Rejected, '符号链接'):
            self.store.ingest(self.file)
        self.assertEqual('do not touch', (self.project/'.webnovel/state.json').read_text())

    def test_original_bom_crlf_no_final_newline(self):
        p = self.body/'第0001章-开始.md'
        raw = b'\xef\xbb\xbf' + '# 第1章 开始\r\n\r\n　正文  '.encode()
        p.write_bytes(raw)
        e = self.store.export()
        self.assertEqual('unchanged', self.store.ingest(e['file'])['status'])
        self.assertEqual(raw, p.read_bytes())


class SnapshotWriteTests(unittest.TestCase):
    """PC 端写正文必须带基准校验：snapshot -> AI 新稿 -> write --base。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root/'book'
        self.body = self.project/'正文'
        self.body.mkdir(parents=True)
        self.file = self.body/'第0001章-开始.md'
        self.base = '# 第1章 开始\n\n　　他走进屋里。\n\n　　桌上有一张地图。\n'
        self.file.write_text(self.base, encoding='utf-8')
        self.store = nr.Store(self.project, root/'state')

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def snap(self):
        return self.store.snapshot(1)

    def incoming(self, text, name='ai.md', newline='\n', bom=False):
        p = Path(self.temp.name)/name
        data = text.replace('\n', newline).encode('utf-8')
        p.write_bytes((b'\xef\xbb\xbf' if bom else b'') + data)
        return p

    def phone_commit(self, text):
        current = self.file.read_bytes()
        output = text.encode('utf-8')
        self.store.commit([({'num': 1, 'file': self.file.name}, current, output)], 'phone')
        return output

    def test_snapshot_returns_base_revision(self):
        snap = self.snap()
        self.assertEqual({'chapter', 'file', 'revision', 'raw'}, set(snap))
        self.assertEqual(nr.digest(self.file.read_bytes()), snap['revision'])
        self.assertEqual(self.base, snap['raw'])

    def test_write_with_matching_base_writes_and_undoes(self):
        snap = self.snap()
        polished = self.base.replace('他走进屋里', '他推门进屋')
        r = self.store.write_ai(1, snap['revision'], self.incoming(polished).read_bytes())
        self.assertEqual(('applied', False), (r['status'], r['merged']))
        self.assertEqual(polished, self.file.read_text())
        self.assertEqual('ai', self.store.history()[0]['kind'])
        self.store.undo(r['id'])
        self.assertEqual(self.base, self.file.read_text())

    def test_write_stale_base_merges_phone_change(self):
        snap = self.snap()
        with_phone = self.base + '　　5km叫不远？\n'
        self.phone_commit(with_phone)
        polished = self.base.replace('他走进屋里', '他推门进屋')
        r = self.store.write_ai(1, snap['revision'], self.incoming(polished).read_bytes())
        self.assertEqual(('applied', True), (r['status'], r['merged']))
        text = self.file.read_text()
        self.assertIn('5km', text)
        self.assertIn('推门进屋', text)
        kinds = [x['kind'] for x in self.store.history()]
        self.assertEqual(['ai-merge', 'phone'], kinds)

    def test_write_conflict_keeps_file_and_records(self):
        snap = self.snap()
        self.phone_commit(self.base.replace('桌上有一张地图', '桌上有一张手绘地图'))
        ai = self.base.replace('桌上有一张地图', '桌上摊着一张军用地图')
        r = self.store.write_ai(1, snap['revision'], self.incoming(ai).read_bytes())
        self.assertEqual('conflict', r['status'])
        self.assertEqual(1, len(r['conflicts']))
        self.assertEqual(self.base.replace('桌上有一张地图', '桌上有一张手绘地图'), self.file.read_text())
        self.assertEqual(['phone'], [x['kind'] for x in self.store.history()])
        rec = self.store.history(True)[0]
        self.assertEqual('ai-conflict', rec['kind'])
        self.assertIn('军用地图', rec['conflicts'][0]['ai'])
        self.assertIn('手绘地图', rec['conflicts'][0]['user'])

    def test_write_preserves_bom_crlf(self):
        raw = b'\xef\xbb\xbf' + self.base.replace('\n', '\r\n').encode()
        self.file.write_bytes(raw)
        snap = self.snap()
        self.phone_commit(self.base + '　　手机加的。\n')
        self.file.write_bytes(b'\xef\xbb\xbf' + (self.base + '　　手机加的。\n').replace('\n', '\r\n').encode())
        r = self.store.write_ai(1, snap['revision'], self.incoming(self.base.replace('地图', '海图')).read_bytes())
        self.assertEqual('applied', r['status'])
        data = self.file.read_bytes()
        self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', data.replace(b'\r\n', b''))
        self.assertIn('手机加的'.encode(), data)

    def test_snapshot_blob_supports_write_on_fresh_chapter(self):
        # 章节从没进过 history：snapshot 必须落盘基准，手机先回传后仍能合并
        snap = self.snap()
        self.assertTrue(self.store.read_blob(snap['revision']))
        with_phone = self.base + '　　手机补一句。\n'
        self.phone_commit(with_phone)
        polished = self.base.replace('他走进屋里', '他推门进屋')
        r = self.store.write_ai(1, snap['revision'], self.incoming(polished).read_bytes())
        self.assertEqual(('applied', True), (r['status'], r['merged']))
        text = self.file.read_text()
        self.assertIn('手机补一句', text)
        self.assertIn('推门进屋', text)
        self.assertEqual('ai-merge', self.store.history()[0]['kind'])

    def test_write_rejects_unknown_base(self):
        with self.assertRaisesRegex(nr.Rejected, '找不到基准快照'):
            self.store.write_ai(1, 'a'*64, self.incoming(self.base).read_bytes())
        self.assertEqual(self.base, self.file.read_text())

    def test_external_change_recorded_and_undoable(self):
        snap = self.snap()
        self.phone_commit(self.base + '　　手机写的。\n')
        self.file.write_text(self.base + '　　手机写的。\n　　绕过工具加的一句。\n')
        self.store.snapshot(1)
        rec = self.store.history()[0]
        self.assertEqual('external', rec['kind'])
        self.assertIn('绕过工具', self.file.read_text())
        self.store.undo(rec['id'])
        self.assertEqual(self.base + '　　手机写的。\n', self.file.read_text())

    def test_external_recorded_once(self):
        self.snap()
        self.phone_commit(self.base)
        self.file.write_text(self.base + '外部\n')
        self.store.snapshot(1)
        self.store.snapshot(1)
        self.store.export()
        self.assertEqual(1, len([x for x in self.store.history() if x['kind'] == 'external']))


class SnapshotWriteCliTests(unittest.TestCase):
    """命令行契约：snapshot 输出 JSON；冲突退出码非 0 且不写文件。"""

    def setUp(self):
        import os, subprocess
        self.subprocess, self.os = subprocess, os
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.project = root/'book'
        body = self.project/'正文'
        body.mkdir(parents=True)
        self.file = body/'第0001章-开始.md'
        self.base = '# 第1章 开始\n\n　　原文。\n'
        self.file.write_text(self.base, encoding='utf-8')
        self.env = dict(os.environ, NOVEL_PROJECT=str(self.project), NOVEL_STATE=str(root/'state'))
        self.script = str(Path(__file__).with_name('novel_roundtrip.py'))

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return self.subprocess.run([sys.executable, self.script, *args], capture_output=True,
                                   text=True, env=self.env)

    def test_snapshot_then_write_and_conflict_exit_code(self):
        snap = self.run_cli('snapshot', '1')
        self.assertEqual(0, snap.returncode, snap.stderr)
        data = json.loads(snap.stdout)
        self.assertEqual(self.base, data['raw'])
        ai = Path(self.temp.name)/'ai.md'
        ai.write_text(self.base.replace('原文', 'AI稿'), encoding='utf-8')
        ok = self.run_cli('write', '1', '--base', data['revision'], '--file', str(ai))
        self.assertEqual(0, ok.returncode, ok.stdout + ok.stderr)
        self.assertEqual(self.base.replace('原文', 'AI稿'), self.file.read_text())

        snap2 = json.loads(self.run_cli('snapshot', '1').stdout)
        self.file.write_text(self.base.replace('原文', '手机稿'), encoding='utf-8')
        ai2 = Path(self.temp.name)/'ai2.md'
        ai2.write_text(self.base.replace('原文', '另一AI稿'), encoding='utf-8')
        conflict = self.run_cli('write', '1', '--base', snap2['revision'], '--file', str(ai2))
        self.assertNotEqual(0, conflict.returncode)
        self.assertIn('status=conflict', conflict.stdout)
        self.assertEqual(self.base.replace('原文', '手机稿'), self.file.read_text())
        # 查询历史冲突是正常退出，不能把 changes 也当失败
        changes = self.run_cli('--json', 'changes')
        self.assertEqual(0, changes.returncode)
        self.assertEqual('ai-conflict', json.loads(changes.stdout)['kind'])


if __name__ == '__main__':
    unittest.main()
