"""Remote navigation regressions using temporary mount folders and mocked KIOFuse."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QSettings, QThreadPool, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

import main
import network


class NetworkTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = main.create_app()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.mount = self.root / 'mount'
        self.mount.mkdir()
        self.mapping_patch = patch.dict(network._mounts, clear=True)
        self.mapping_patch.start()
        self.addCleanup(self.mapping_patch.stop)
        self.addCleanup(self.temporary.cleanup)

    def wait_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            QTest.qWait(5)
        return predicate()


class NetworkTests(NetworkTestCase):
    def test_url_validation_and_encoded_folder_names(self):
        self.assertEqual(network.validate_remote_url(' smb://server/share name '), 'smb://server/share%20name')
        self.assertEqual(network.validate_remote_url('sftp://user@server'), 'sftp://user@server/')
        for uri in ('http://server', 'smb:///share', 'sftp://', 'smb://server/%ZZ',
                    'smb://server/share#fragment', 'ftp://server/?query', 'smb://server/\nfile'):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                network.validate_remote_url(uri)
        for uri in ('remote:/', 'mtp:/device', 'recentlyused:/'):
            self.assertEqual(network.validate_remote_url(uri), uri)

    def test_mapping_preserves_uri_for_nested_names_and_excludes_password(self):
        network.remember_mount('sftp://user:secret@server/share', self.mount)
        path = self.mount / 'żółć # 100%.txt'
        uri = network.uri_for_path(path)
        self.assertEqual(uri, 'sftp://user@server/share/%C5%BC%C3%B3%C5%82%C4%87%20%23%20100%25.txt')
        self.assertNotIn('secret', network.display_location(path))
        self.assertNotIn('secret', network.display_location('sftp://user:secret@server/share'))
        self.assertEqual(network.remote_title(self.mount), 'share')
        self.assertIsNone(network.uri_for_path(str(self.mount) + '-other'))

    def test_deepest_mount_wins_and_breadcrumbs_do_not_show_fuse_scaffolding(self):
        child = self.mount / 'nested'
        child.mkdir()
        network.remember_mount('smb://first/share', self.mount)
        network.remember_mount('sftp://second/folder', child)
        self.assertEqual(network.uri_for_path(child / 'child'), 'sftp://second/folder/child')
        self.assertEqual(network.breadcrumbs(child / 'child'),
                         [('second · folder', str(child)), ('child', str(child / 'child'))])

    def test_parent_of_mount_root_is_remote_parent(self):
        network.remember_mount('smb://server/share/folder', self.mount)
        self.assertEqual(network.parent_location(self.mount), 'smb://server/share')
        self.assertEqual(network.parent_location(self.mount / 'child'), 'smb://server/share/folder')

    def test_mount_runs_in_worker_and_keeps_gui_responsive(self):
        release = threading.Event()
        started = threading.Event()
        ready, failure = Mock(), Mock()
        thread_ids = []
        gui_thread = threading.get_ident()

        def mount(_uri):
            thread_ids.append(threading.get_ident())
            started.set()
            release.wait(2)
            return str(self.mount)

        timer = QTimer()
        ticks = []
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(5)
        try:
            with patch('network.mount_remote', side_effect=mount):
                network.mount_async('smb://server/share', ready, failure, job_factory=main.FileJob)
                self.assertTrue(self.wait_until(lambda: started.is_set() and ticks))
                ready.assert_not_called()
                release.set()
                self.assertTrue(self.wait_until(lambda: ready.called))
        finally:
            release.set()
            timer.stop()
            QThreadPool.globalInstance().waitForDone(2000)
        self.assertNotEqual(thread_ids, [gui_thread])
        ready.assert_called_once_with(str(self.mount))
        failure.assert_not_called()
        self.assertEqual(network.uri_for_path(self.mount), 'smb://server/share')

    def test_cached_mount_is_checked_without_calling_kio_again(self):
        network.remember_mount('smb://server/share', self.mount)
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote') as mount:
            network.mount_async('smb://server/share', ready, failure,
                                job_factory=main.FileJob, submit=lambda job: job.run())
        mount.assert_not_called()
        ready.assert_called_once_with(str(self.mount))
        failure.assert_not_called()

    def test_missing_cached_mount_is_remounted(self):
        network.remember_mount('smb://server/share', self.root / 'missing')
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote', return_value=str(self.mount)) as mount:
            network.mount_async('smb://server/share', ready, failure,
                                job_factory=main.FileJob, submit=lambda job: job.run())
        mount.assert_called_once()
        ready.assert_called_once_with(str(self.mount))
        failure.assert_not_called()

    def test_mount_failure_returns_error_without_saving_mapping(self):
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote', side_effect=ValueError('KIOFuse unavailable')):
            network.mount_async('smb://server/share', ready, failure,
                                job_factory=main.FileJob, submit=lambda job: job.run())
        ready.assert_not_called()
        failure.assert_called_once_with('KIOFuse unavailable')
        self.assertIsNone(network.uri_for_path(self.mount))

    def test_timeout_delivers_once_and_discards_late_mount(self):
        jobs = []
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote', return_value=str(self.mount)):
            job = network.mount_async('smb://server/share', ready, failure,
                                      job_factory=main.FileJob, submit=jobs.append, timeout_ms=5)
            self.assertTrue(self.wait_until(lambda: failure.called))
            self.assertIn(job, network._jobs)
            job.run()
        ready.assert_not_called()
        failure.assert_called_once()
        self.assertNotIn(job, network._jobs)
        self.assertIsNone(network.uri_for_path(self.mount))

    def test_deleted_owner_receives_no_callback(self):
        jobs = []
        owner = QObject()
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote', return_value=str(self.mount)):
            job = network.mount_async('smb://server/share', ready, failure,
                                      job_factory=main.FileJob, submit=jobs.append, owner=owner)
            delete(owner)
            job.run()
        ready.assert_not_called()
        failure.assert_not_called()

    def test_remote_file_can_be_mounted_for_dbus_selection_and_cached(self):
        remote_file = self.mount / 'file.txt'
        remote_file.write_text('remote')
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote', return_value=str(remote_file)) as mount:
            network.mount_async('sftp://server/file.txt', ready, failure, allow_file=True,
                                job_factory=main.FileJob, submit=lambda job: job.run())
            network.mount_async('sftp://server/file.txt', ready, failure, allow_file=True,
                                job_factory=main.FileJob, submit=lambda job: job.run())
        mount.assert_called_once()
        self.assertEqual(ready.call_count, 2)
        ready.assert_called_with(str(remote_file))
        failure.assert_not_called()
        self.assertEqual(network.uri_for_path(remote_file), 'sftp://server/file.txt')
        self.assertEqual(network.uri_for_path(remote_file.parent), 'sftp://server/')

    def test_remote_file_is_rejected_for_folder_navigation(self):
        remote_file = self.mount / 'file.txt'
        remote_file.write_text('remote')
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote', return_value=str(remote_file)):
            network.mount_async('sftp://server/file.txt', ready, failure,
                                job_factory=main.FileJob, submit=lambda job: job.run())
        ready.assert_not_called()
        failure.assert_called_once()

    def test_remote_broken_symlink_can_be_revealed_without_reading_its_target(self):
        link = self.mount / 'link'
        link.symlink_to('missing-target')
        ready, failure = Mock(), Mock()
        with patch('network.mount_remote', return_value=str(link)):
            network.mount_async('sftp://server/link', ready, failure, allow_file=True,
                                job_factory=main.FileJob, submit=lambda job: job.run())
        ready.assert_called_once_with(str(link))
        failure.assert_not_called()


class RemoteNavigationTests(NetworkTestCase):
    def setUp(self):
        super().setUp()
        settings = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        with patch('main.QSettings', return_value=settings):
            self.window = main.Explorer(str(self.root))
        self.addCleanup(self.close_window)
        self.window.error = Mock()
        self.requests = []

        def mount(uri, ready, failure, **kwargs):
            self.requests.append((uri, ready, failure, kwargs))

        self.mount_patch = patch('network.mount_async', side_effect=mount)
        self.mount_patch.start()
        self.addCleanup(self.mount_patch.stop)

    def close_window(self):
        self.window.close()
        self.app.processEvents()

    def complete_request(self, index=0, path=None):
        uri, ready, _failure, _kwargs = self.requests[index]
        path = path or self.mount
        network.remember_mount(uri, path)
        ready(str(path))

    def test_pending_mount_leaves_current_folder_usable_and_calls_ready_after_mount(self):
        page = self.window.page
        ready = Mock()
        self.assertTrue(page.navigate('smb://server/share', on_ready=ready))
        self.assertEqual(page.location, str(self.root))
        self.assertEqual(page.pending_remote, 'smb://server/share')
        ready.assert_not_called()
        self.complete_request()
        self.assertEqual(page.location, str(self.mount))
        self.assertEqual(page.pending_remote, '')
        self.assertEqual(page.history[-1], 'smb://server/share')
        ready.assert_called_once_with(str(self.mount))

    def test_newer_local_navigation_ignores_old_remote_result(self):
        page = self.window.page
        page.navigate('smb://old/share')
        page.navigate('home')
        self.complete_request()
        self.assertEqual(page.location, 'home')
        self.assertEqual(page.history[-1], 'home')

    def test_newest_remote_request_wins_even_when_older_request_finishes_last(self):
        first, second = self.root / 'first', self.root / 'second'
        first.mkdir()
        second.mkdir()
        page = self.window.page
        page.navigate('smb://old/share')
        page.navigate('sftp://new/folder')
        self.complete_request(1, second)
        self.complete_request(0, first)
        self.assertEqual(page.location, str(second))
        self.assertEqual(page.history[-1], 'sftp://new/folder')

    def test_old_mount_error_does_not_interrupt_later_navigation(self):
        page = self.window.page
        page.navigate('smb://old/share')
        page.navigate('home')
        self.requests[0][2]('old error')
        self.window.error.assert_not_called()
        self.assertEqual(page.location, 'home')

    def test_mount_error_leaves_history_and_location_unchanged(self):
        page = self.window.page
        history = list(page.history)
        page.navigate('smb://server/share')
        self.requests[0][2]('offline')
        self.assertEqual(page.location, str(self.root))
        self.assertEqual(page.history, history)
        self.assertEqual(page.pending_remote, '')
        self.window.error.assert_called_once_with('offline')

    def test_remote_history_index_changes_only_after_success(self):
        page = self.window.page
        page.navigate('smb://server/share')
        self.complete_request()
        page.travel(-1)
        self.assertEqual(page.history_index, 0)
        page.travel(1)
        self.assertEqual(page.history_index, 0)
        self.requests[-1][2]('offline')
        self.assertEqual(page.history_index, 0)
        page.travel(1)
        self.complete_request(len(self.requests) - 1)
        self.assertEqual(page.history_index, 1)

    def test_remote_address_and_saved_session_use_original_uri(self):
        page = self.window.page
        page.navigate('smb://server/share')
        self.complete_request()
        self.window.edit_address()
        self.assertEqual(self.window.address_edit.text(), 'smb://server/share')
        self.window.finish_address()
        self.window.save_session()
        self.assertEqual(self.window.settings.value('session_tabs', [], type=list), ['smb://server/share'])

    def test_pending_remote_address_is_saved_without_waiting_for_mount(self):
        self.window.page.navigate('smb://server/share')
        self.window.save_session()
        self.assertEqual(self.window.settings.value('session_tabs', [], type=list), ['smb://server/share'])

    def test_existing_local_mount_path_navigates_synchronously(self):
        network.remember_mount('smb://server/share', self.mount)
        ready = Mock()
        self.assertTrue(self.window.page.navigate(str(self.mount), on_ready=ready))
        self.assertEqual(self.requests, [])
        ready.assert_called_once_with(str(self.mount))
        self.assertEqual(self.window.page.history[-1], 'smb://server/share')

    def test_closed_pending_remote_tab_keeps_requested_uri_for_reopening(self):
        self.window.add_tab('smb://server/share')
        self.window.close_tab(1)
        self.assertEqual(self.window.tabs.count(), 1)
        self.assertEqual(self.window.closed_tabs[-1], 'smb://server/share')
        self.window.reopen_tab()
        self.assertEqual(self.window.tabs.count(), 2)
        self.assertEqual(self.window.page.pending_remote, 'smb://server/share')

    def test_connect_to_server_validation_rejects_missing_host(self):
        with patch('main.QInputDialog.getText', return_value=('smb://', True)):
            self.window.connect_to_server()
        self.assertEqual(self.requests, [])
        self.window.error.assert_called_once()

    def test_connect_to_server_starts_remote_request(self):
        with patch('main.QInputDialog.getText', return_value=('sftp://user@server/folder', True)):
            self.window.connect_to_server()
        self.assertEqual(self.requests[0][0], 'sftp://user@server/folder')
        self.window.error.assert_not_called()


if __name__ == '__main__':
    unittest.main()
