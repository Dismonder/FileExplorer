"""Regression tests for transfers and history; all files stay in temporary folders."""
import errno
import os
import shutil
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import main


class FileOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state_home = patch.dict(os.environ, {'XDG_STATE_HOME': str(self.root / 'state')})
        state_home.start()
        self.addCleanup(state_home.stop)
        mounts = patch('trash_backend.mount_points', return_value=[])
        mounts.start()
        self.addCleanup(mounts.stop)

    def history(self, entry, redo=False):
        errors = []
        state = SimpleNamespace(busy=False, replayable=lambda _: True,
                                status_text=SimpleNamespace(setText=lambda _: None))
        def run(operation, after=None, on_error=None):
            try:
                operation()
            except Exception as exc:
                errors.append(exc)
                if on_error:
                    on_error()
            else:
                if after:
                    after()
        state.run_job = run
        source, target = [entry], []
        main.Explorer.apply_history(state, source, target, redo)
        return source, target, errors

    def test_progress_keeps_large_byte_counts(self):
        values = []
        job = main.TransferJob([])
        job.signals.progress.connect(lambda copied, total, _: values.append((copied, total)))
        job.copied, job.total = 3 * 1024**3, 5 * 1024**3
        job.report('large.bin', force=True)
        self.assertEqual(values, [(job.copied, job.total)])

    def test_cross_device_move_preserves_source_when_walk_fails(self):
        source, target = self.root / 'src', self.root / 'dst'
        source.mkdir()
        (source / 'valuable.txt').write_text('keep me')
        def unreadable_walk(path, **kwargs):
            kwargs['onerror'](PermissionError('unreadable subdirectory'))
            return iter(())
        with patch('main.rename_noreplace', side_effect=OSError(errno.EXDEV, 'cross device')), \
             patch('main.os.walk', side_effect=unreadable_walk):
            with self.assertRaises(PermissionError):
                main.TransferJob([], cut=True).move_entry(source, target, 0)
        self.assertEqual((source / 'valuable.txt').read_text(), 'keep me')

    def test_copy_sets_directory_permissions_after_children(self):
        source, target = self.root / 'src', self.root / 'dst'
        (source / 'nested').mkdir(parents=True)
        (source / 'nested' / 'file.txt').write_text('data')
        original = main.shutil.copystat
        def copystat(src, dst, **kwargs):
            if Path(src).is_dir():
                self.assertTrue((target / 'nested' / 'file.txt').exists())
            return original(src, dst, **kwargs)
        with patch('main.shutil.copystat', side_effect=copystat):
            main.TransferJob([]).copy_entry(source, target)
        self.assertEqual((target / 'nested' / 'file.txt').read_text(), 'data')

    def test_transfer_does_not_overwrite_unexpected_destination(self):
        source, target = self.root / 'src', self.root / 'dst'
        source.write_text('source')
        target.write_text('destination')
        for cut in (False, True):
            errors = []
            job = main.TransferJob([(source, target)], cut=cut)
            job.signals.finished.connect(lambda error, _: errors.append(error))
            job.run()
            self.assertTrue(errors[0])
            self.assertEqual(source.read_text(), 'source')
            self.assertEqual(target.read_text(), 'destination')

    def test_undo_move_conflict_preserves_files_and_history(self):
        original, moved = self.root / 'original', self.root / 'moved'
        original.write_text('new file')
        moved.write_text('old file')
        entry = ('transfer', [('move', original, moved)])
        source, target, errors = self.history(entry)
        self.assertIsInstance(errors[0], FileExistsError)
        self.assertEqual(source, [entry])
        self.assertEqual(target, [])
        self.assertEqual(original.read_text(), 'new file')
        self.assertEqual(moved.read_text(), 'old file')

    def test_partial_undo_tracks_only_completed_items(self):
        old_a, old_b = self.root / 'a', self.root / 'b'
        new_a, new_b = self.root / 'moved-a', self.root / 'moved-b'
        old_a.write_text('conflict')
        new_a.write_text('A')
        new_b.write_text('B')
        a, b = ('move', old_a, new_a), ('move', old_b, new_b)
        source, target, errors = self.history(('transfer', [a, b]))
        self.assertEqual(len(errors), 1)
        self.assertEqual(source, [('transfer', [a])])
        self.assertEqual(target, [('transfer', [b])])
        self.assertEqual(old_b.read_text(), 'B')
        self.assertFalse(new_b.exists())

    def test_redo_conflict_does_not_create_destructive_undo_entry(self):
        original, target_file = self.root / 'original', self.root / 'target'
        original.write_text('source')
        target_file.write_text('unrelated')
        entry = ('transfer', [('copy', original, target_file)])
        source, target, errors = self.history(entry, redo=True)
        self.assertIsInstance(errors[0], FileExistsError)
        self.assertEqual(source, [entry])
        self.assertEqual(target, [])
        self.assertEqual(target_file.read_text(), 'unrelated')

    def test_undo_trash_does_not_merge_into_existing_directory(self):
        original, trashed = self.root / 'original', self.root / 'trashed'
        original.mkdir()
        trashed.mkdir()
        (trashed / 'file').write_text('data')
        entry = ('trash', [(original, trashed)])
        source, target, errors = self.history(entry)
        self.assertIsInstance(errors[0], FileExistsError)
        self.assertEqual(source, [entry])
        self.assertEqual(target, [])
        self.assertEqual(list(original.iterdir()), [])
        self.assertTrue((trashed / 'file').exists())

    def prepare(self, sources, directory):
        errors, planned = [], []
        state = SimpleNamespace(busy=False, error=errors.append,
                                run_transfer_prepared=lambda pairs, cut, **kwargs: planned.extend(pairs))
        dialog = SimpleNamespace(resolution='overwrite', apply_all=True,
                                 exec=lambda: main.QDialog.Accepted, deleteLater=lambda: None)
        with patch('main.ConflictDialog', return_value=dialog):
            main.Explorer.run_transfer(state, sources, directory)
        return planned, errors

    def test_batch_same_names_receive_distinct_destinations(self):
        sources = []
        for folder in ('one', 'two', 'three'):
            source = self.root / folder / 'report.txt'
            source.parent.mkdir()
            source.write_text(folder)
            sources.append(source)
        destination = self.root / 'destination'
        destination.mkdir()
        planned, errors = self.prepare(sources, destination)
        self.assertFalse(errors)
        self.assertEqual(len({target for _, target in planned}), 3)
        job = main.TransferJob(planned)
        job.run()
        self.assertEqual({p.read_text() for p in destination.iterdir()}, {'one', 'two', 'three'})

    def test_overwrite_cannot_delete_parent_of_source(self):
        source = self.root / 'report' / 'nested' / 'report'
        source.parent.mkdir(parents=True)
        source.write_text('irreplaceable')
        planned, errors = self.prepare([source], self.root)
        self.assertTrue(source.exists(), 'conflict resolution deleted its own source')
        self.assertEqual(source.read_text(), 'irreplaceable')
        self.assertFalse(planned)
        self.assertTrue(errors)

    def test_overwrite_cannot_delete_another_selected_source(self):
        first = self.root / 'first' / 'report'
        first.parent.mkdir()
        first.write_text('first')
        other = self.root / 'report' / 'other'
        other.parent.mkdir()
        other.write_text('other')
        planned, errors = self.prepare([first, other], self.root)
        self.assertTrue(other.exists())
        self.assertTrue(errors)
        self.assertEqual([src for src, _ in planned], [other])

    def test_overwrite_cannot_remove_another_selected_file(self):
        first = self.root / 'folder' / 'report'
        first.parent.mkdir()
        first.write_text('first')
        second = self.root / 'report'
        second.write_text('second')
        planned, errors = self.prepare([first, second], self.root)
        self.assertTrue(errors)
        self.assertEqual(second.read_text(), 'second')
        self.assertEqual([src for src, _ in planned], [second])

    def test_fifo_copy_is_rejected_before_opening(self):
        source = self.root / 'pipe'
        os.mkfifo(source)
        with patch('builtins.open', side_effect=AssertionError('opening FIFO would hang')):
            with self.assertRaises(shutil.SpecialFileError):
                main.TransferJob([]).copy_entry(source, self.root / 'copy')

    def test_partial_trash_keeps_successful_items_undoable(self):
        first, second = self.root / 'first', self.root / 'second'
        first.write_text('first')
        second.write_text('second')
        records, errors = [], []
        page = SimpleNamespace(selected_paths=lambda: [str(first), str(second)],
                               neighbour_of_selection=lambda: None)
        state = SimpleNamespace(busy=False, typing=lambda: False, page=page,
                                record=records.append, status_text=SimpleNamespace(setText=lambda _: None),
                                select_successor=lambda *_: None)
        def run(operation, after=None, on_error=None):
            try:
                operation()
            except OSError as exc:
                errors.append(exc)
                if on_error:
                    on_error()
            else:
                if after:
                    after()
        state.run_job = run
        original = main.trash_backend.write_info
        def write_info(src, target, home_root):
            if src == second:
                raise PermissionError('simulated failure of second item')
            return original(src, target, home_root)
        with patch('main.TRASH_ROOT', self.root / 'Trash'), patch('trash_backend.write_info', side_effect=write_info):
            main.Explorer.trash(state)
        self.assertEqual(len(errors), 1)
        self.assertEqual(records, [('trash', [(first, self.root / 'Trash' / 'files' / 'first')])])
        self.assertTrue(second.exists())

    def test_batch_reserves_names_with_unicode_and_existing_copy_names(self):
        names = ['zażółć.txt', '.ukryty', 'plik — kopia.txt', 'archiwum.tar.gz', 'a\nb.txt']
        for name in names:
            with self.subTest(name=name):
                destination = self.root / ('out-' + name)
                destination.mkdir()
                (destination / name).write_text('existing')
                sources = []
                for i in range(4):
                    folder = self.root / f'{name}-{i}'
                    folder.mkdir()
                    source = folder / name
                    source.write_text(str(i))
                    sources.append(source)
                state = SimpleNamespace(busy=False, error=lambda error: self.fail(str(error)))
                planned = []
                state.run_transfer_prepared = lambda pairs, cut, **kwargs: planned.extend(pairs)
                dialog = SimpleNamespace(resolution='rename', apply_all=True,
                                         exec=lambda: main.QDialog.Accepted, deleteLater=lambda: None)
                with patch('main.ConflictDialog', return_value=dialog):
                    main.Explorer.run_transfer(state, sources, destination)
                self.assertEqual(len({target for _, target in planned}), 4)
                job = main.TransferJob(planned)
                job.run()
                self.assertEqual({p.read_text() for p in destination.iterdir()}, {'existing', '0', '1', '2', '3'})

    def test_overwrite_guard_resolves_symlinked_source_parent(self):
        real = self.root / 'report'
        real.mkdir()
        source = real / 'report'
        source.write_text('data')
        alias = self.root / 'alias'
        alias.symlink_to(real, target_is_directory=True)
        planned, errors = self.prepare([alias / 'report'], self.root)
        self.assertTrue(errors)
        self.assertFalse(planned)
        self.assertEqual(source.read_text(), 'data')

    def test_broken_symlink_is_copied_without_following_it(self):
        source, target = self.root / 'link', self.root / 'copy'
        source.symlink_to('missing-target')
        main.TransferJob([(source, target)]).run()
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), 'missing-target')

    def test_cancellation_mid_file_removes_only_partial_destination(self):
        source, target = self.root / 'large', self.root / 'copy'
        payload = b'0123456789' * 100
        source.write_bytes(payload)
        job = main.TransferJob([(source, target)])
        job.CHUNK = 32
        original_report = job.report
        def cancel_after_chunk(name, force=False):
            if job.copied:
                job.cancel_requested = True
            original_report(name, force)
        job.report = cancel_after_chunk
        results = []
        job.signals.finished.connect(lambda error, completed: results.append((error, completed)))
        job.run()
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), payload)
        self.assertEqual(results, [('', [])])

    def test_confirming_overwrite_does_not_delete_destination_before_worker(self):
        source = self.root / 'source' / 'report'
        source.parent.mkdir()
        source.write_text('new')
        target = self.root / 'report'
        target.write_text('old')
        planned, errors = self.prepare([source], self.root)
        self.assertFalse(errors)
        self.assertEqual(planned, [(source, target)])
        self.assertTrue(target.exists())
        self.assertEqual(target.read_text(), 'old')

    def test_failed_copy_does_not_leave_partial_file(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_text('complete')
        job = main.TransferJob([(source, target)])
        def disk_full(src, dst):
            dst.write_text('partial')
            raise OSError(errno.ENOSPC, 'no space left')
        with patch.object(job, 'copy_file', side_effect=disk_full):
            job.run()
        self.assertFalse(target.exists())
        self.assertEqual(source.read_text(), 'complete')

    def test_restore_broken_symlink_from_trash(self):
        trash = self.root / 'Trash'
        (trash / 'files').mkdir(parents=True)
        (trash / 'info').mkdir()
        original = self.root / 'link'
        stored = trash / 'files' / 'link'
        stored.symlink_to('missing')
        info = trash / 'info' / 'link.trashinfo'
        info.write_text('metadata')
        item = SimpleNamespace(data=lambda column, role: str(stored if role == main.Qt.UserRole else original))
        page = SimpleNamespace(location='trash', trash_tree=SimpleNamespace(selectedItems=lambda: [item]))
        state = SimpleNamespace(busy=False, page=page, run_job=lambda operation: operation())
        with patch('main.TRASH_ROOT', trash):
            main.Explorer.restore_from_trash(state)
        self.assertTrue(original.is_symlink())
        self.assertEqual(os.readlink(original), 'missing')
        self.assertFalse(os.path.lexists(stored))
        self.assertFalse(info.exists())

    def test_overwrite_failure_and_cancellation_preserve_original(self):
        for failure in ('disk-full', 'cancel'):
            with self.subTest(failure=failure):
                source, target = self.root / 'source', self.root / 'target'
                source.write_text('new data')
                target.write_text('old data')
                job = main.TransferJob([(source, target)], overwrites={target: main.entry_version(target)})
                def incomplete(src, dst):
                    dst.write_text('partial')
                    if failure == 'disk-full':
                        raise OSError(errno.ENOSPC, 'disk full')
                    return False
                results = []
                job.signals.finished.connect(lambda error, done: results.append((error, done)))
                with patch.object(job, 'copy_entry', side_effect=incomplete):
                    job.run()
                self.assertEqual(target.read_text(), 'old data')
                self.assertEqual(source.read_text(), 'new data')
                self.assertEqual(results[0][1], [])
                self.assertEqual(list(self.root.glob('.fileexplorer-transfer-*')), [])

    def test_overwrite_rechecks_destination_after_copy(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_text('new data')
        target.write_text('old data')
        job = main.TransferJob([(source, target)], overwrites={target: main.entry_version(target)})
        original_copy = job.copy_entry
        def changed_destination(src, dst):
            result = original_copy(src, dst)
            target.write_text('external edit')
            return result
        with patch.object(job, 'copy_entry', side_effect=changed_destination):
            job.run()
        self.assertEqual(target.read_text(), 'external edit')

    def test_failed_publish_restores_original_destination(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_text('new data')
        target.write_text('old data')
        job = main.TransferJob([(source, target)], overwrites={target: main.entry_version(target)})
        original_rename = main.rename_noreplace
        def cannot_publish(src, dst):
            if src.name == 'payload':
                raise PermissionError('simulated publication failure')
            return original_rename(src, dst)
        with patch('main.rename_noreplace', side_effect=cannot_publish):
            job.run()
        self.assertEqual(target.read_text(), 'old data')
        self.assertEqual(list(self.root.glob('.fileexplorer-transfer-*')), [])

    def test_successful_overwrite_copy_and_move(self):
        for cut in (False, True):
            with self.subTest(cut=cut):
                source, target = self.root / 'source', self.root / 'target'
                source.write_text('new data')
                target.write_text('old data')
                job = main.TransferJob([(source, target)], cut=cut,
                                       overwrites={target: main.entry_version(target)})
                results = []
                job.signals.finished.connect(lambda error, done: results.append((error, done)))
                job.run()
                self.assertEqual(target.read_text(), 'new data')
                self.assertEqual(source.exists(), not cut)
                self.assertEqual(results, [('', [('move' if cut else 'copy', source, target)])])
                self.assertEqual(list(self.root.glob('.fileexplorer-transfer-*')), [])

    def test_undo_redo_overwrite_restores_both_versions(self):
        for cut in (False, True):
            with self.subTest(cut=cut):
                source, target = self.root / 'source', self.root / 'target'
                source.write_text('new data')
                target.write_text('old data')
                job = main.TransferJob([(source, target)], cut=cut,
                                       overwrites={target: main.entry_version(target)})
                results = []
                job.signals.finished.connect(lambda error, done: results.append((error, done)))
                job.run()
                entry = ('transfer', results[0][1])
                pending, replay, errors = self.history(entry)
                self.assertFalse(errors)
                self.assertEqual(target.read_text(), 'old data')
                self.assertEqual(source.read_text(), 'new data')
                self.assertFalse(pending)
                self.assertEqual(len(replay), 1)
                _, repeated, errors = self.history(replay[0], redo=True)
                self.assertFalse(errors)
                self.assertEqual(target.read_text(), 'new data')
                self.assertEqual(source.exists(), not cut)
                self.assertEqual(len(repeated), 1)
                _, _, errors = self.history(repeated[0])
                self.assertFalse(errors)
                self.assertEqual(target.read_text(), 'old data')


    def test_mixed_overwrite_history_undoes_and_repeats_all_entries(self):
        destination = self.root / 'destination'
        destination.mkdir()
        transfers = []
        for name in ('safe-before', 'overwritten', 'safe-after'):
            source, target = self.root / name, destination / name
            source.write_text(name)
            transfers.append((source, target))
        overwritten = destination / 'overwritten'
        overwritten.write_text('previous data')
        results = []
        job = main.TransferJob(transfers, overwrites={overwritten: main.entry_version(overwritten)})
        job.signals.finished.connect(lambda error, done: results.append((error, done)))
        job.run()
        pending, replay, errors = self.history(('transfer', results[0][1]))
        self.assertFalse(errors)
        self.assertEqual(overwritten.read_text(), 'previous data')
        self.assertFalse((destination / 'safe-before').exists())
        self.assertFalse((destination / 'safe-after').exists())
        self.assertFalse(pending)
        self.assertEqual(replay[0][1].overwritten, {overwritten})
        self.assertIn(overwritten, replay[0][1].backups)
        _, undo_again, redo_errors = self.history(replay[0], redo=True)
        self.assertFalse(redo_errors)
        self.assertEqual(overwritten.read_text(), 'overwritten')
        self.assertEqual((destination / 'safe-before').read_text(), 'safe-before')
        self.assertEqual((destination / 'safe-after').read_text(), 'safe-after')
        self.assertIn(overwritten, undo_again[0][1].backups)


    def test_partial_transfer_retains_overwrite_history_marker(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_text('new data')
        target.write_text('old data')
        unexpected = self.root / 'unexpected'
        unexpected.write_text('do not replace')
        results = []
        job = main.TransferJob([(source, target), (source, unexpected)],
                               overwrites={target: main.entry_version(target)})
        job.signals.finished.connect(lambda error, done: results.append((error, done)))
        job.run()
        self.assertTrue(results[0][0])
        pending, replay, errors = self.history(('transfer', results[0][1]))
        self.assertFalse(errors)
        self.assertFalse(pending)
        self.assertTrue(replay)
        self.assertEqual(target.read_text(), 'old data')
        self.assertEqual(unexpected.read_text(), 'do not replace')


    def test_tree_snapshot_never_stats_a_symlink_target(self):
        directory = self.root / 'directory'
        directory.mkdir()
        (directory / 'file').write_text('data')
        link = self.root / 'link'
        link.symlink_to(directory, target_is_directory=True)
        with patch('main.os.stat', side_effect=AssertionError('must not follow a symbolic link')):
            snapshot = main.tree_version(link)
        self.assertEqual(snapshot, (('', main.entry_version(link)),))

    def test_tree_snapshot_does_not_follow_nested_or_broken_symlinks(self):
        outside, directory = self.root / 'outside', self.root / 'directory'
        outside.mkdir()
        valuable = outside / 'file'
        valuable.write_text('before')
        directory.mkdir()
        (directory / 'link').symlink_to(outside, target_is_directory=True)
        (directory / 'broken').symlink_to(self.root / 'missing')
        before = main.tree_version(directory)
        valuable.write_text('changed outside the copied tree')
        self.assertEqual(main.tree_version(directory), before)
        self.assertEqual({name for name, _ in before}, {'', 'link', 'broken'})

    def test_unreadable_tree_snapshot_refuses_destructive_undo(self):
        directory = self.root / 'directory'
        directory.mkdir()
        (directory / 'file').write_text('keep')
        def unreadable_walk(path, **kwargs):
            kwargs['onerror'](PermissionError('unreadable directory'))
            return iter(())
        with patch('main.os.walk', side_effect=unreadable_walk):
            with self.assertRaises(PermissionError):
                main.tree_version(directory)
            history = main.TransferHistory([('copy', self.root / 'source', directory)])
        self.assertIsNone(history.versions[directory])
        pending, replay, errors = self.history(('transfer', history))
        self.assertTrue(errors)
        self.assertTrue(pending)
        self.assertFalse(replay)
        self.assertEqual((directory / 'file').read_text(), 'keep')

    def test_kernel_zero_result_does_not_produce_an_empty_copy(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_bytes(b'important data')
        with patch('main.os.copy_file_range', return_value=0):
            main.TransferJob([(source, target)]).run()
        self.assertEqual(target.read_bytes(), b'important data')

    def test_copy_rejects_source_modified_during_read(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_bytes(b'a' * 128)
        job = main.TransferJob([(source, target)])
        job.CHUNK = 16
        changed = False
        def mutate(name, force=False):
            nonlocal changed
            if job.copied and not changed:
                changed = True
                source.write_bytes(b'b' * 128)
        job.report = mutate
        results = []
        job.signals.finished.connect(lambda error, done: results.append((error, done)))
        job.run()
        self.assertTrue(results[0][0])
        self.assertFalse(target.exists())
        self.assertEqual(source.read_bytes(), b'b' * 128)

    def test_cross_device_move_preserves_entries_added_after_directory_copy(self):
        source, target = self.root / 'source', self.root / 'target'
        source.mkdir()
        (source / 'original').write_text('original')
        job = main.TransferJob([(source, target)], cut=True)
        original_copy = job.copy_entry
        original_rename = main.rename_noreplace
        def changed_source(src, dst):
            result = original_copy(src, dst)
            if src == source:
                (source / 'new-important-file').write_text('added during copy')
            return result
        def cross_device(src, dst):
            if src == source:
                raise OSError(errno.EXDEV, 'simulated different filesystem')
            return original_rename(src, dst)
        results = []
        job.signals.finished.connect(lambda error, done: results.append((error, done)))
        with patch.object(job, 'copy_entry', side_effect=changed_source), \
             patch('main.rename_noreplace', side_effect=cross_device):
            job.run()
        self.assertTrue(results[0][0])
        self.assertEqual(results[0][1], [])
        self.assertEqual((source / 'new-important-file').read_text(), 'added during copy')
        self.assertFalse(target.exists())

    def test_directory_copy_refuses_late_edits_inside_existing_subfolder(self):
        source, target = self.root / 'source', self.root / 'target'
        (source / 'nested').mkdir(parents=True)
        member = source / 'nested' / 'file'
        member.write_text('before')
        job = main.TransferJob([(source, target)])
        original_copy = job.copy_entry
        def changed_source(src, dst):
            result = original_copy(src, dst)
            if src == source:
                member.write_text('after')
            return result
        results = []
        job.signals.finished.connect(lambda error, done: results.append((error, done)))
        with patch.object(job, 'copy_entry', side_effect=changed_source):
            job.run()
        self.assertTrue(results[0][0])
        self.assertFalse(target.exists())
        self.assertEqual(member.read_text(), 'after')

    def test_undo_copy_refuses_subsequent_file_edits(self):
        source, target = self.root / 'source', self.root / 'target'
        source.write_text('original')
        target.write_text('original')
        entry = ('transfer', main.TransferHistory([('copy', source, target)]))
        target.write_text('important later edit')
        pending, redo, errors = self.history(entry)
        self.assertTrue(errors)
        self.assertEqual(target.read_text(), 'important later edit')
        self.assertEqual(pending, [entry])
        self.assertFalse(redo)

    def test_undo_copy_refuses_edits_inside_existing_subfolder(self):
        target = self.root / 'copy'
        (target / 'sub').mkdir(parents=True)
        file = target / 'sub' / 'file'
        file.write_text('before')
        entry = ('transfer', main.TransferHistory([('copy', None, target)]))
        file.write_text('after')
        _, redo, errors = self.history(entry)
        self.assertTrue(errors)
        self.assertFalse(redo)
        self.assertEqual(file.read_text(), 'after')

    def test_undo_unchanged_copy_still_works(self):
        target = self.root / 'copy'
        target.write_text('data')
        entry = ('transfer', main.TransferHistory([('copy', self.root / 'source', target)]))
        pending, redo, errors = self.history(entry)
        self.assertFalse(errors)
        self.assertFalse(pending)
        self.assertTrue(redo)
        self.assertFalse(target.exists())

    def test_empty_trash_preserves_metadata_and_undo_for_failed_removal(self):
        trash = self.root / 'Trash'
        (trash / 'files').mkdir(parents=True)
        (trash / 'info').mkdir()
        stored = trash / 'files' / 'locked'
        stored.write_text('data')
        metadata = trash / 'info' / 'locked.trashinfo'
        metadata.write_text('original location')
        entry = ('trash', [(self.root / 'original', stored)])
        errors = []
        def run(operation, after=None, on_error=None):
            try:
                operation()
            except OSError as exc:
                errors.append(exc)
                if on_error:
                    on_error()
            else:
                if after:
                    after()
        state = SimpleNamespace(busy=False, undo_stack=[entry], redo_stack=[], run_job=run)
        original_remove = main.remove_entry
        def locked(path):
            if path == stored:
                raise PermissionError('file cannot be removed')
            original_remove(path)
        with patch('main.TRASH_ROOT', trash), patch('main.remove_entry', side_effect=locked), \
             patch('main.QMessageBox.question', return_value=main.QMessageBox.Yes):
            main.Explorer.empty_trash(state)
        self.assertTrue(stored.exists())
        self.assertTrue(metadata.exists())
        self.assertEqual(state.undo_stack, [entry])
        self.assertTrue(errors, 'partial failure must be reported')

    def test_trash_metadata_never_overwrites_existing_symlink(self):
        trash = self.root / 'Trash'
        (trash / 'info').mkdir(parents=True)
        victim = self.root / 'victim'
        victim.write_text('precious')
        (trash / 'info' / 'file.trashinfo').symlink_to(victim)
        with patch('main.TRASH_ROOT', trash):
            with self.assertRaises(FileExistsError):
                main.write_trashinfo(self.root / 'file', trash / 'files' / 'file')
        self.assertEqual(victim.read_text(), 'precious')

    def test_permanent_delete_uses_origin_page_after_navigation(self):
        trash = self.root / 'Trash'
        (trash / 'files').mkdir(parents=True)
        (trash / 'info').mkdir()
        stored = trash / 'files' / 'file'
        stored.write_text('data')
        metadata = trash / 'info' / 'file.trashinfo'
        metadata.write_text('metadata')
        page = SimpleNamespace(location='trash', selected_paths=lambda: [str(stored)],
                               neighbour_of_selection=lambda: None)
        queued = []
        state = SimpleNamespace(busy=False, typing=lambda: False, page=page,
                                select_successor=lambda *_: None,
                                run_job=lambda operation, after: queued.append(operation))
        with patch('main.TRASH_ROOT', trash), \
             patch('main.QMessageBox.warning', return_value=main.QMessageBox.Yes):
            main.Explorer.delete_permanently(state)
            state.page = SimpleNamespace(location=str(self.root))
            queued[0]()
        self.assertFalse(stored.exists())
        self.assertFalse(metadata.exists())


if __name__ == '__main__':
    unittest.main()
