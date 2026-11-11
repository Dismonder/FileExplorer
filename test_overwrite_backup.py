"""Overwrite undo/redo and crash recovery use isolated files and state."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main
import overwrite_backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        state = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        state.start()
        self.addCleanup(state.stop)
        self.source, self.target = self.root / 'source', self.root / 'target'
        self.source.write_text('new')
        self.target.write_text('previous')
        self.callbacks = dict(version=main.tree_version, rename=main.rename_noreplace,
                              move=lambda a, b: main.TransferJob([]).move_entry(a, b, 0))

    def overwrite(self, cut=False):
        result = []
        job = main.TransferJob([(self.source, self.target)], cut=cut,
                               overwrites={self.target: main.entry_version(self.target)})
        job.signals.finished.connect(lambda error, history: result.append((error, history)))
        job.run()
        self.assertEqual(result[0][0], '')
        return result[0][1].backups[self.target]

    def test_saved_version_remains_discoverable_after_process_history_is_lost(self):
        saved = self.overwrite()
        found = overwrite_backup.list_backups()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].previous.read_text(), 'previous')
        self.assertEqual(found[0].state, 'applied')
        self.assertEqual(saved.directory.stat().st_mode & 0o777, 0o700)

    def test_undo_refuses_modified_target_and_keeps_previous_version(self):
        saved = self.overwrite()
        self.target.write_text('user edit')
        with self.assertRaises(OSError):
            saved.undo(**self.callbacks)
        self.assertEqual(self.target.read_text(), 'user edit')
        self.assertEqual(saved.previous.read_text(), 'previous')

    def test_undo_refuses_modified_backup(self):
        saved = self.overwrite()
        saved.previous.write_text('edited saved version')
        with self.assertRaises(OSError):
            saved.undo(**self.callbacks)
        self.assertEqual(self.target.read_text(), 'new')

    def test_undo_move_conflict_preserves_both_versions(self):
        saved = self.overwrite(cut=True)
        self.source.write_text('unrelated new source')
        with self.assertRaises(FileExistsError):
            saved.undo(**self.callbacks)
        self.assertEqual(self.source.read_text(), 'unrelated new source')
        self.assertEqual(self.target.read_text(), 'new')
        self.assertEqual(saved.previous.read_text(), 'previous')

    def test_redo_refuses_modified_restored_version(self):
        saved = self.overwrite()
        saved.undo(**self.callbacks)
        self.target.write_text('edit after undo')
        with self.assertRaises(OSError):
            saved.redo(**self.callbacks)
        self.assertEqual(self.target.read_text(), 'edit after undo')
        self.assertEqual(saved.current.read_text(), 'new')

    def test_failed_restoration_rolls_back_without_losing_redo_or_file(self):
        saved = self.overwrite()
        def rename(source, target):
            if source == saved.previous:
                raise PermissionError('simulated restore failure')
            main.rename_noreplace(source, target)
        with self.assertRaises(PermissionError):
            saved.undo(**dict(self.callbacks, rename=rename))
        self.assertEqual(self.target.read_text(), 'new')
        self.assertEqual(saved.previous.read_text(), 'previous')
        saved.undo(**self.callbacks)
        self.assertEqual(self.target.read_text(), 'previous')

    def test_directory_and_symlink_versions_round_trip(self):
        self.source.unlink()
        self.target.unlink()
        self.source.mkdir()
        (self.source / 'nested').mkdir()
        (self.source / 'nested' / 'data').write_text('new tree')
        self.target.symlink_to('missing-original-link')
        saved = self.overwrite()
        saved.undo(**self.callbacks)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(os.readlink(self.target), 'missing-original-link')
        saved.redo(**self.callbacks)
        self.assertEqual((self.target / 'nested' / 'data').read_text(), 'new tree')

    def test_journal_write_failure_after_transition_does_not_break_history(self):
        saved = self.overwrite()
        with patch.object(saved, 'save', side_effect=OSError('disk full')):
            saved.undo(**self.callbacks)
        self.assertEqual(saved.state, 'undone')
        self.assertTrue(saved.warning)
        self.assertEqual(self.target.read_text(), 'previous')
        saved.redo(**self.callbacks)
        self.assertEqual(self.target.read_text(), 'new')

    def test_registry_rejects_unsafe_or_foreign_directory(self):
        saved = self.overwrite()
        saved.directory.chmod(0o755)
        self.assertEqual(overwrite_backup.list_backups(), [])


if __name__ == '__main__':
    unittest.main()
