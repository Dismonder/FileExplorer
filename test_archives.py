"""Archives are real; UI prompts and job dispatch are replaced by synchronous harnesses."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import tarfile
import io
import unittest
import zipfile
from unittest.mock import patch

import main


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, path, contents):
        with zipfile.ZipFile(path, 'w') as bundle:
            for name, data in contents.items():
                bundle.writestr(name, data)
        return path

    def extract(self, archives, folder=False):
        records, errors = [], []
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
        state = SimpleNamespace(busy=False, run_job=run, record=records.append,
                                page=SimpleNamespace(selected_paths=lambda: [str(p) for p in archives],
                                                     current_dir=lambda: str(self.root)))
        main.Explorer.extract_archive(state, folder)
        return records, errors

    def create(self, sources, name='created', before_worker=None):
        records, errors = [], []
        def run(operation, after=None):
            if before_worker:
                before_worker()
            try:
                operation()
            except Exception as exc:
                errors.append(exc)
            else:
                if after:
                    after()
        state = SimpleNamespace(busy=False, run_job=run, record=records.append,
                                error=errors.append,
                                page=SimpleNamespace(selected_paths=lambda: [str(p) for p in sources],
                                                     current_dir=lambda: str(self.root)))
        with patch('main.QInputDialog.getText', return_value=(name, True)), \
             patch('main.QInputDialog.getItem', return_value=('ZIP (.zip)', True)):
            main.Explorer.archive(state)
        return records, errors

    def test_archive_failure_does_not_leave_partial_destination(self):
        source = self.root / 'source'
        source.write_text('valuable')
        def fail(base_name, *_args, **_kwargs):
            Path(base_name + '.zip').write_bytes(b'incomplete')
            raise OSError('simulated archive write failure')
        with patch('main.shutil.make_archive', side_effect=fail):
            records, errors = self.create([source])
        self.assertTrue(errors)
        self.assertFalse(records)
        self.assertFalse((self.root / 'created.zip').exists())
        self.assertEqual(source.read_text(), 'valuable')

    def test_archive_never_overwrites_a_destination_created_after_dialog(self):
        source = self.root / 'source'
        source.write_text('source')
        destination = self.root / 'created.zip'
        records, errors = self.create([source], before_worker=lambda: destination.write_bytes(b'keep me'))
        self.assertTrue(errors)
        self.assertFalse(records)
        self.assertEqual(destination.read_bytes(), b'keep me')

    def test_archive_success_is_recorded_and_contains_selected_entries(self):
        folder = self.root / 'folder'
        folder.mkdir()
        (folder / 'file').write_text('folder data')
        source = self.root / 'source'
        source.write_text('file data')
        records, errors = self.create([source, folder])
        self.assertFalse(errors)
        self.assertEqual(records, [('transfer', [('copy', None, self.root / 'created.zip')])])
        with zipfile.ZipFile(self.root / 'created.zip') as archive:
            self.assertEqual(archive.read('source'), b'file data')
            self.assertEqual(archive.read('folder/file'), b'folder data')

    def test_extract_does_not_write_through_existing_symlink(self):
        outside = self.root / 'outside'
        outside.mkdir()
        valuable = outside / 'file.txt'
        valuable.write_text('original')
        (self.root / 'folder').symlink_to(outside, target_is_directory=True)
        archive = self.archive(self.root / 'package.zip', {'folder/file.txt': 'replacement'})
        _, errors = self.extract([archive])
        self.assertEqual(valuable.read_text(), 'original')
        self.assertTrue(errors)

    def test_same_stem_archives_do_not_merge_destination_folders(self):
        archives = []
        for name in ('one', 'two'):
            parent = self.root / name
            parent.mkdir()
            archives.append(self.archive(parent / 'package.zip', {'file.txt': name}))
        records, errors = self.extract(archives, folder=True)
        self.assertFalse(errors)
        targets = [target for _, _, target in records[0][1]]
        self.assertEqual(len(set(targets)), 2)
        self.assertEqual({(p / 'file.txt').read_text() for p in targets}, {'one', 'two'})

    def test_failed_extraction_does_not_leave_a_destination(self):
        broken = self.root / 'broken.zip'
        broken.write_bytes(b'not a zip')
        records, errors = self.extract([broken], folder=True)
        self.assertTrue(errors)
        self.assertFalse(records)
        self.assertFalse((self.root / 'broken').exists())

    def test_partial_batch_keeps_first_archive_undoable(self):
        first = self.archive(self.root / 'good.zip', {'file': 'data'})
        broken = self.root / 'broken.zip'
        broken.write_bytes(b'not a zip')
        records, errors = self.extract([first, broken], folder=True)
        self.assertTrue(errors)
        self.assertEqual(records, [('transfer', [('copy', None, self.root / 'good')])])
        self.assertEqual((self.root / 'good' / 'file').read_text(), 'data')

    def test_tar_link_outside_destination_is_rejected(self):
        victim = self.root / 'victim'
        victim.write_text('keep')
        archive = self.root / 'unsafe.tar'
        with tarfile.open(archive, 'w') as bundle:
            link = tarfile.TarInfo('escape')
            link.type = tarfile.SYMTYPE
            link.linkname = str(self.root)
            bundle.addfile(link)
            payload = b'overwrite'
            entry = tarfile.TarInfo('escape/victim')
            entry.size = len(payload)
            bundle.addfile(entry, io.BytesIO(payload))
        records, errors = self.extract([archive], folder=True)
        self.assertTrue(errors)
        self.assertFalse(records)
        self.assertEqual(victim.read_text(), 'keep')
        self.assertFalse((self.root / 'unsafe').exists())

    def test_crc_failure_after_first_member_leaves_no_partial_output(self):
        archive = self.archive(self.root / 'corrupt.zip', {'first': 'valid', 'second': 'unique-payload'})
        archive.write_bytes(archive.read_bytes().replace(b'unique-payload', b'broken-payload'))
        records, errors = self.extract([archive])
        self.assertTrue(errors)
        self.assertFalse(records)
        self.assertFalse((self.root / 'first').exists())
        self.assertFalse((self.root / 'second').exists())

    def test_extract_here_records_only_new_outputs_for_undo(self):
        archive = self.archive(self.root / 'valid.zip', {'nested/file': 'data', 'empty/': ''})
        records, errors = self.extract([archive])
        self.assertFalse(errors)
        self.assertEqual((self.root / 'nested' / 'file').read_text(), 'data')
        self.assertTrue((self.root / 'empty').is_dir())
        self.assertEqual({p for _, _, p in records[0][1]}, {self.root / 'nested', self.root / 'empty'})
        self.assertTrue(archive.exists())


if __name__ == '__main__':
    unittest.main()
