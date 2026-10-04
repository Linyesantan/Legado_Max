import json
from pathlib import Path
import tempfile
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
        second.write_bytes(second.read_bytes()+b'VM edit\n')
        with self.assertRaisesRegex(nr.Rejected, '第2章.*VM'):
            self.store.ingest(self.incoming(self.text.replace('旧句', '新句')))
        self.assertEqual(self.original['第0001章-开始.md'], (self.body/'第0001章-开始.md').read_bytes())
        self.assertTrue(second.read_bytes().endswith(b'VM edit\n'))

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


if __name__ == '__main__':
    unittest.main()
