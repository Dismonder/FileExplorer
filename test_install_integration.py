import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_integration as integration


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config, self.data, self.state = [self.root / name for name in ('config', 'data', 'state')]

    def environment(self):
        return patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': '', 'DBUS_SESSION_BUS_ADDRESS': ''})

    def test_empty_gvariant_arrays(self):
        self.assertEqual(integration.dbus_values('(@ai [],)'), ([],))
        self.assertEqual(integration.dbus_values('(@as [],)'), ([],))
        self.assertEqual(integration.dbus_values('([268435525],)'), ([268435525],))
        self.assertEqual(integration.dbus_values('(uint32 1,)'), (1,))
        self.assertEqual(integration.dbus_values('(true,)'), (True,))

    def test_failed_install_restores_existing_defaults(self):
        self.config.mkdir()
        mime = self.config / 'mimeapps.list'
        original = '[Default Applications]\ninode/directory=old.desktop\ntext/plain=editor.desktop\n'
        mime.write_text(original)
        with self.environment(), patch.object(integration, 'xdg_paths', return_value=(self.config, self.data, self.state)), \
             patch.object(Path, 'home', return_value=self.root), patch.object(integration.shutil, 'which', return_value=None), \
             patch.object(integration, 'verify', side_effect=RuntimeError('simulated verification failure')):
            with self.assertRaisesRegex(RuntimeError, 'simulated'):
                integration.install()
        self.assertEqual(mime.read_text(), original)
        self.assertFalse((self.data / 'applications/fileexplorer.desktop').exists())
        self.assertFalse((self.root / '.local/bin/fileexplorer').exists())

    def test_shortcut_conflict_is_rejected_before_mutations(self):
        with patch.dict(os.environ, {'XDG_CURRENT_DESKTOP': 'KDE'}), \
             patch.object(integration, 'xdg_paths', return_value=(self.config, self.data, self.state)), \
             patch.object(integration.shutil, 'which', return_value='/usr/bin/gdbus'), \
             patch.object(integration, 'shortcut_preflight', side_effect=RuntimeError('shortcut conflict')):
            with self.assertRaisesRegex(RuntimeError, 'shortcut conflict'):
                integration.install()
        self.assertFalse(self.config.exists())
        self.assertFalse(self.data.exists())
        self.assertFalse(self.state.exists())

    def test_snapshot_restores_binary_launcher_and_new_files(self):
        launcher = self.root / 'launcher'
        launcher.write_bytes(b'\xff\x00old-binary')
        launcher.chmod(0o700)
        added = self.root / 'new.desktop'
        with self.environment(), patch.object(integration.shutil, 'which', return_value=None):
            backup = integration.snapshot([launcher, added], self.state)
            integration.atomic_write(launcher, 'new launcher')
            integration.atomic_write(added, 'new entry')
            with patch.object(integration, 'refresh_desktop'):
                integration.restore(backup)
        self.assertEqual(launcher.read_bytes(), b'\xff\x00old-binary')
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o700)
        self.assertFalse(added.exists())

    def test_atomic_write_does_not_follow_old_symlink(self):
        valuable = self.root / 'valuable'
        valuable.write_text('keep')
        target = self.root / 'symlink'
        target.symlink_to(valuable)
        integration.atomic_write(target, 'new')
        self.assertFalse(target.is_symlink())
        self.assertEqual(valuable.read_text(), 'keep')
        self.assertEqual(target.read_text(), 'new')

    def test_generated_entry_validates(self):
        if not integration.shutil.which('desktop-file-validate'):
            self.skipTest('desktop-file-validate unavailable')
        path = self.root / 'fileexplorer.desktop'
        path.write_text(integration.desktop_entry())
        result = subprocess.run(['desktop-file-validate', str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_remote_folder_defaults_and_associations_preserve_file_handlers(self):
        path = self.root / 'mimeapps.list'
        path.write_text('[Default Applications]\ntext/plain=editor.desktop;\nx-scheme-handler/file=editor.desktop;fileexplorer.desktop;\n'
                        '[Added Associations]\nx-scheme-handler/smb=org.kde.dolphin.desktop;\n')
        parsed_path = self.root / 'updated.list'
        parsed_path.write_text(integration.mime_configuration(path, True))
        config = integration.read_ini(parsed_path)
        for mime in integration.FILE_MANAGER_MIMES:
            self.assertEqual(config['Default Applications'][mime], 'fileexplorer.desktop;')
        self.assertEqual(config['Default Applications']['text/plain'], 'editor.desktop;')
        self.assertEqual(config['Default Applications']['x-scheme-handler/file'], 'editor.desktop;')
        self.assertIn('org.kde.dolphin.desktop', config['Added Associations']['x-scheme-handler/smb'])


if __name__ == '__main__':
    unittest.main()
