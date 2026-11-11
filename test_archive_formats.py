import ctypes.util
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archive_formats import extract_extra_archive, safe_member_path


@unittest.skipUnless(ctypes.util.find_library('archive'), 'system libarchive unavailable')
class ExtraArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'output'
        self.output.mkdir()

    def test_real_rar_regular_file(self):
        fixture = Path(__file__).parent / 'tests/fixtures/test_read_format_rar_subblock.rar'
        extract_extra_archive(fixture, self.output)
        self.assertEqual((self.output / 'test.txt').read_bytes(), b'test text document\r\n')

    def test_real_rar_preserves_contained_link_and_empty_directory(self):
        fixture = Path(__file__).parent / 'tests/fixtures/test_read_format_rar.rar'
        extract_extra_archive(fixture, self.output)
        self.assertTrue((self.output / 'testemptydir').is_dir())
        self.assertTrue((self.output / 'testlink').is_symlink())
        self.assertEqual((self.output / 'testlink').read_bytes(), b'test text document\r\n')
        self.assertEqual(os.readlink(self.output / 'testlink'), 'test.txt')

    @unittest.skipUnless(shutil.which('7z'), '7z fixture generator unavailable')
    def test_real_7zip_unicode_file_and_empty_folder(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'zażółć 100%.txt').write_text('treść')
        (source / 'empty').mkdir()
        archive = self.root / 'package.7z'
        subprocess.run(['7z', 'a', '-t7z', '-bd', str(archive), '.'], cwd=source,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=15)
        extract_extra_archive(archive, self.output)
        self.assertEqual((self.output / 'zażółć 100%.txt').read_text(), 'treść')
        self.assertTrue((self.output / 'empty').is_dir())

    def test_corrupt_archive_fails(self):
        source = self.root / 'corrupt.rar'
        source.write_bytes(b'not an archive')
        with self.assertRaises(OSError):
            extract_extra_archive(source, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_traversal_is_rejected_before_writing(self):
        archive = self.root / 'unsafe.rar'
        with tarfile.open(archive, 'w') as output:
            entry = tarfile.TarInfo('../victim')
            entry.size = 4
            output.addfile(entry, io.BytesIO(b'evil'))
        with self.assertRaises(ValueError):
            extract_extra_archive(archive, self.output)
        self.assertFalse((self.root / 'victim').exists())

    def test_outside_symlink_is_rejected(self):
        archive = self.root / 'unsafe.rar'
        with tarfile.open(archive, 'w') as output:
            entry = tarfile.TarInfo('escape')
            entry.type = tarfile.SYMTYPE
            entry.linkname = '../outside'
            output.addfile(entry)
        with self.assertRaises(ValueError):
            extract_extra_archive(archive, self.output)
        self.assertFalse(os.path.lexists(self.output / 'escape'))

    def test_duplicate_member_is_not_overwritten(self):
        archive = self.root / 'duplicates.rar'
        with tarfile.open(archive, 'w') as output:
            for content in (b'first', b'other'):
                entry = tarfile.TarInfo('file')
                entry.size = len(content)
                output.addfile(entry, io.BytesIO(content))
        with self.assertRaises(FileExistsError):
            extract_extra_archive(archive, self.output)
        self.assertEqual((self.output / 'file').read_bytes(), b'first')

    def test_special_files_are_rejected(self):
        archive = self.root / 'fifo.rar'
        with tarfile.open(archive, 'w') as output:
            entry = tarfile.TarInfo('fifo')
            entry.type = tarfile.FIFOTYPE
            output.addfile(entry)
        with self.assertRaises(ValueError):
            extract_extra_archive(archive, self.output)
        self.assertFalse((self.output / 'fifo').exists())

    def test_missing_library_reports_required_dependency(self):
        with patch('archive_formats.ctypes.util.find_library', return_value=None):
            with self.assertRaisesRegex(OSError, 'libarchive'):
                extract_extra_archive(self.root / 'file.7z', self.output)

    def test_absolute_and_parent_member_paths_are_rejected(self):
        for name in ('/etc/passwd', '../escape', 'folder/../../escape'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_member_path(name)


if __name__ == '__main__':
    unittest.main()
