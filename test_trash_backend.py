"""Per-volume trash regressions; never touches a user's actual trash."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trash_backend as trash


class TrashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'data' / 'Trash'
        self.volume = self.root / 'volume'
        self.volume.mkdir()
        self.uid = str(os.getuid())

    def external(self, shared=False):
        path = self.volume / f'.Trash-{self.uid}'
        if shared:
            (self.volume / '.Trash').mkdir(mode=0o1777, exist_ok=True)
            (self.volume / '.Trash').chmod(0o1777)
            path = self.volume / '.Trash' / self.uid
        return trash.ensure(trash.TrashRoot(path, self.volume))

    def test_home_preserves_unicode_percent_and_broken_link(self):
        source = self.root / 'żółty % link'
        source.symlink_to('missing')
        target, metadata = trash.reserve(source, self.home)
        source.rename(target)
        self.assertTrue(target.is_symlink())
        self.assertEqual(trash.read_info(target, trash.TrashRoot(self.home))[0], str(source))
        self.assertIn('%25', metadata.read_text())

    def test_external_uses_relative_path_and_restores_shared_metadata(self):
        root = self.external(shared=True)
        source = self.volume / 'dir' / 'żółty.txt'
        source.parent.mkdir()
        source.write_text('data')
        target = root.files / source.name
        metadata = trash.write_info(source, target, self.home)
        self.assertIn('Path=dir/', metadata.read_text())
        source.rename(target)
        self.assertEqual(trash.read_info(target, root)[0], str(source))
        self.assertEqual(trash.info_path(target, self.home), metadata)

    def test_lists_both_external_trash_variants_and_home(self):
        locations = [trash.ensure(trash.TrashRoot(self.home)), self.external(), self.external(True)]
        for root in locations:
            source = (self.root if root.topdir is None else self.volume) / 'same.txt'
            source.write_text(str(root.path))
            target = root.files / source.name
            trash.write_info(source, target, self.home)
            source.rename(target)
        entries = list(trash.entries(self.home, mounts=[self.volume]))
        self.assertEqual(len(entries), 3)
        self.assertEqual(len({path for path, _, _ in entries}), 3)

    def test_shared_without_sticky_or_symlink_is_ignored(self):
        root = self.external(True)
        (self.volume / '.Trash').chmod(0o777)
        self.assertNotIn(root, trash.roots(self.home, [self.volume]))
        (self.volume / '.Trash').rename(self.volume / 'other')
        (self.volume / '.Trash').symlink_to(self.volume / 'other', target_is_directory=True)
        self.assertNotIn(root, trash.roots(self.home, [self.volume]))

    def test_invalid_shared_falls_back_to_private_without_touching_link(self):
        (self.volume / '.Trash').symlink_to(self.root)
        source = self.volume / 'source'
        source.write_text('data')
        with patch('trash_backend._existing_device', return_value=-1):
            result = trash.choose(source, self.home, [self.volume])
        self.assertEqual(result, self.external())
        self.assertFalse((self.root / self.uid).exists())

    def test_collision_metadata_and_existing_files_are_never_overwritten(self):
        source = self.root / 'data.txt'
        source.write_text('one')
        target, _ = trash.reserve(source, self.home)
        source.rename(target)
        source.write_text('two')
        target2, metadata2 = trash.reserve(source, self.home)
        self.assertNotEqual(target, target2)
        source.rename(target2)
        source.write_text('three')
        target3, _ = trash.reserve(source, self.home)
        self.assertNotEqual(target3, target2)
        self.assertEqual(target.read_text(), 'one')
        self.assertEqual(target2.read_text(), 'two')
        self.assertTrue(metadata2.exists())

    def test_metadata_symlink_and_fifo_never_read(self):
        root = trash.ensure(trash.TrashRoot(self.home))
        target = root.files / 'entry'
        metadata = root.info / 'entry.trashinfo'
        other = self.root / 'outside'
        other.write_text('[Trash Info]\nPath=/outside\n')
        metadata.symlink_to(other)
        self.assertEqual(trash.read_info(target, root), ('', ''))
        metadata.unlink()
        os.mkfifo(metadata)
        self.assertEqual(trash.read_info(target, root), ('', ''))

    def test_first_metadata_value_wins_and_escape_is_rejected(self):
        root = self.external()
        target = root.files / 'entry'
        metadata = root.info / 'entry.trashinfo'
        metadata.write_text('[Trash Info]\nPath=good\nPath=../bad\nDeletionDate=2026-09-30T12:00:00\n')
        self.assertEqual(trash.read_info(target, root)[0], str(self.volume / 'good'))
        for path in ('../bad', '%2e%2e/bad', '/outside', 'bad%00path'):
            metadata.write_text(f'[Trash Info]\nPath={path}\n')
            self.assertEqual(trash.read_info(target, root)[0], '')

    def test_scan_does_not_create_trash_and_rejects_files_symlink(self):
        self.assertEqual(trash.roots(self.home, [self.volume]), [])
        self.assertFalse(self.home.exists())
        root = self.external()
        root.files.rmdir()
        root.files.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(trash.roots(self.home, [self.volume]), [])

    def test_mount_matching_does_not_follow_final_symlink(self):
        source = self.volume / 'link'
        source.symlink_to(self.root / 'outside')
        self.assertEqual(trash.containing_mount(source, [self.root, self.volume]), self.volume)


if __name__ == '__main__':
    unittest.main()
