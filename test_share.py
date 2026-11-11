"""Sharing regressions using only temporary files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import share


class PackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'output'

    def test_empty_directories_are_preserved(self):
        folder = self.root / 'folder'
        (folder / 'empty').mkdir(parents=True)
        result = share.pack_paths([folder], self.output)
        with zipfile.ZipFile(result) as bundle:
            self.assertIn('folder/', bundle.namelist())
            self.assertIn('folder/empty/', bundle.namelist())

    def test_two_packs_in_one_second_do_not_overwrite(self):
        source = self.root / 'file'
        source.write_text('first')
        with patch('share.time.time', return_value=123):
            first = share.pack_paths([source], self.output)
            source.write_text('second')
            second = share.pack_paths([source], self.output)
        self.assertNotEqual(first, second)
        with zipfile.ZipFile(first) as bundle:
            self.assertEqual(bundle.read('file'), b'first')

    def test_cancel_before_single_file_leaves_no_archive(self):
        source = self.root / 'file'
        source.write_text('data')
        with self.assertRaises(InterruptedError):
            share.pack_paths([source], self.output, should_stop=lambda: True)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_failed_pack_cleans_up_partial_archive(self):
        with self.assertRaises(FileNotFoundError):
            share.pack_paths([self.root / 'missing'], self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_directory_symlink_contents_are_included(self):
        folder = self.root / 'folder'
        folder.mkdir()
        external = self.root / 'external'
        external.mkdir()
        (external / 'file').write_text('linked data')
        (folder / 'linked').symlink_to(external, target_is_directory=True)
        result = share.pack_paths([folder], self.output)
        with zipfile.ZipFile(result) as bundle:
            self.assertIn('folder/linked/file', bundle.namelist())
            self.assertEqual(bundle.read('folder/linked/file'), b'linked data')

    def test_directory_symlink_cycle_fails_without_partial_archive(self):
        folder = self.root / 'folder'
        folder.mkdir()
        (folder / 'loop').symlink_to(folder, target_is_directory=True)
        with self.assertRaises(OSError):
            share.pack_paths([folder], self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_cancel_during_large_file_removes_partial_output(self):
        source = self.root / 'large'
        source.write_bytes(b'x' * (5 * 1024 * 1024))
        checks = 0
        def cancel():
            nonlocal checks
            checks += 1
            return checks >= 4
        with self.assertRaises(InterruptedError):
            share.pack_paths([source], self.output, should_stop=cancel)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(source.stat().st_size, 5 * 1024 * 1024)

    def test_unreadable_directory_does_not_produce_incomplete_archive(self):
        folder = self.root / 'folder'
        folder.mkdir()
        with patch.object(Path, 'iterdir', side_effect=PermissionError('unreadable folder')):
            with self.assertRaises(PermissionError):
                share.pack_paths([folder], self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_output_inside_source_is_not_packed_into_itself(self):
        folder = self.root / 'folder'
        folder.mkdir()
        (folder / 'file').write_text('data')
        result = share.pack_paths([folder], folder / 'cache')
        with zipfile.ZipFile(result) as bundle:
            self.assertEqual(bundle.read('folder/file'), b'data')
            self.assertFalse(any(name.endswith('.zip') for name in bundle.namelist()))


if __name__ == '__main__':
    unittest.main()
