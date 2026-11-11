"""Regressions for desktop integration; never register on the real session bus."""
import configparser
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_config = tempfile.TemporaryDirectory(prefix='fileexplorer-integration-config-')
os.environ['XDG_CONFIG_HOME'] = _config.name

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtTest import QTest
import main
from linux_integration import FileManagerService, local_path, normalize_location, desktop_exec
from install_integration import desktop_entry, mime_configuration


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = main.create_app()
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.files = tempfile.TemporaryDirectory(prefix='fileexplorer-integration-')
        self.root = Path(self.files.name)
        self.service = FileManagerService(main.Explorer)
        self.error = patch.object(main.Explorer, 'error')
        self.error_mock = self.error.start()

    def tearDown(self):
        for window in list(self.service.windows):
            window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.error.stop()
        self.files.cleanup()

    def test_trash_uri_keeps_virtual_location(self):
        window = self.service.show(['trash:/'])
        self.assertEqual(window.page.location, 'trash')
        self.error_mock.assert_not_called()

    def test_file_url_preserves_percent_and_unicode(self):
        target = self.root / 'zażółć 100% #?.txt'
        target.write_text('test')
        uri = QUrl.fromLocalFile(str(target)).toString()
        self.assertEqual(local_path(uri), str(target))
        self.assertEqual(local_path(str(target)), str(target))
        self.service.show([uri], select=True)
        QTest.qWait(500)
        self.assertEqual(self.service.windows[0].page.selected_paths(), [str(target)])

    def test_select_two_items_in_same_folder(self):
        files = [self.root / 'jeden.txt', self.root / 'dwa.txt']
        for path in files:
            path.write_text('test')
        window = self.service.show([str(p) for p in files], select=True)
        QTest.qWait(500)
        self.assertEqual(set(window.page.selected_paths()), {str(p) for p in files})
        self.assertEqual(window.pages.count(), 1)

    def test_reveal_hidden_item_ignores_type_filter_without_exposing_siblings(self):
        wanted, sibling = self.root / '.wanted.txt', self.root / '.other.txt'
        wanted.write_text('selected')
        sibling.write_text('hidden')
        window = self.service.show([str(self.root)])
        window.toggle_hidden(False)
        window.page.set_type_filter('*.png')
        self.service.show([str(wanted)], select=True, startup_id='test')
        QTest.qWait(500)
        self.assertEqual(window.page.selected_paths(), [str(wanted)])
        self.assertFalse(window.show_hidden)
        self.assertFalse(window.page.proxy.mapFromSource(window.model.index(str(sibling))).isValid())

    def test_properties_callback_uses_each_requested_item(self):
        first, second = self.root / 'one.txt', self.root / 'two.txt'
        first.write_text('first')
        second.write_text('second')
        with patch.object(main.Explorer, 'properties') as properties:
            self.service.show([str(first), str(second)], properties=True)
            QTest.qWait(500)
            self.assertEqual([call.kwargs['paths'] for call in properties.call_args_list], [[str(first)], [str(second)]])

    def test_properties_selects_directory_in_parent(self):
        directory = self.root / 'folder'
        directory.mkdir()
        with patch.object(main.Explorer, 'properties') as properties:
            window = self.service.show([str(directory)], properties=True)
            QTest.qWait(500)
            self.assertEqual(window.page.location, str(self.root))
            self.assertEqual(window.page.selected_paths(), [str(directory)])
            properties.assert_called_once()
            properties.assert_called_with(paths=[str(directory)])

    def test_unrecognized_url_reports_error(self):
        window = self.service.show(['https://example.com/folder'])
        self.app.processEvents()
        self.assertEqual(window.page.location, 'home')
        self.error_mock.assert_called_once()

    def test_missing_item_reports_error(self):
        self.service.show([str(self.root / 'nie-istnieje')])
        self.app.processEvents()
        self.error_mock.assert_called_once()

    def test_activation_token_does_not_leak_to_child_processes(self):
        with patch.dict(os.environ, {'XDG_ACTIVATION_TOKEN': 'previous'}):
            self.service.show([str(self.root)], startup_id='caller-token')
            self.assertEqual(os.environ['XDG_ACTIVATION_TOKEN'], 'previous')

    def test_localhost_and_remote_file_urls(self):
        self.assertEqual(local_path('file://localhost/tmp/test%20folder'), '/tmp/test folder')
        with self.assertRaises(ValueError):
            local_path('file://remote/tmp/folder')
        with self.assertRaises(ValueError):
            local_path('file:///tmp/test#fragment')

    def test_remote_mount_is_not_converted_to_local_uri(self):
        with patch('linux_integration.mount_remote', return_value=str(self.root)) as mount:
            self.assertEqual(normalize_location('smb://server/share'), str(self.root))
            mount.assert_called_once_with('smb://server/share')

    def test_busy_window_refuses_shutdown(self):
        window = self.service.show([str(self.root)])
        window.busy = True
        try:
            self.assertFalse(self.service.control.QuitIfIdle())
            self.assertTrue(window.isVisible())
        finally:
            window.busy = False

    def test_mime_change_preserves_other_defaults(self):
        path = self.root / 'mimeapps.list'
        path.write_text('[Default Applications]\ntext/plain=editor.desktop;\n'
                        'x-scheme-handler/file=fileexplorer.desktop;other.desktop;\n'
                        '[Removed Associations]\ninode/directory=fileexplorer.desktop;old.desktop;\n')
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(mime_configuration(path, set_default=True))
        self.assertEqual(parser['Default Applications']['text/plain'], 'editor.desktop;')
        self.assertEqual(parser['Default Applications']['inode/directory'], 'fileexplorer.desktop;')
        self.assertEqual(parser['Default Applications']['x-scheme-handler/file'], 'other.desktop;')
        self.assertEqual(parser['Removed Associations']['inode/directory'], 'old.desktop;')

    def test_entry_does_not_claim_every_local_file(self):
        entry = desktop_entry()
        self.assertNotIn('x-scheme-handler/file;', entry)
        self.assertIn('x-scheme-handler/trash;', entry)
        self.assertIn('Actions=Home;Computer;Trash;', entry)

    def test_exec_literal_percent_does_not_become_field_code(self):
        self.assertEqual(desktop_exec(['/tmp/50% done/app', '--dbus']), '"/tmp/50%% done/app" "--dbus"')
        self.assertEqual(desktop_exec(['/tmp/a$b']), '"/tmp/a\\\\$b"')


if __name__ == '__main__':
    unittest.main()
