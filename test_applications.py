"""Application discovery and launching regressions, without changing real MIME associations."""
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QDialog

import main


def application(name='Editor', **values):
    return {'Type': 'Application', 'Name': name, 'Exec': '/usr/bin/editor %F',
            'X-Entry-Path': '/applications/editor.desktop', **values}


def completed(stdout='', returncode=0, stderr=''):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


class DesktopEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        cache = patch.dict(main._DESKTOP_CACHE, signature=None, entries={})
        cache.start()
        self.addCleanup(cache.stop)

    def write_entry(self, path, name='Editor', extra=''):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[Desktop Entry]\nType=Application\nName={name}\nExec=editor %F\n{extra}', encoding='utf-8')

    def test_xdg_directories_use_defaults_for_empty_or_relative_values(self):
        with patch.dict(os.environ, {'XDG_DATA_HOME': 'relative', 'XDG_DATA_DIRS': ''}):
            self.assertEqual(main.application_dirs(), [main.HOME / '.local/share/applications',
                                                       Path('/usr/local/share/applications'), Path('/usr/share/applications')])

    def test_xdg_directories_ignore_relative_system_paths_and_duplicates(self):
        with patch.dict(os.environ, {'XDG_DATA_HOME': str(self.root),
                                    'XDG_DATA_DIRS': f'{self.root}:/other:relative:/other'}):
            self.assertEqual(main.application_dirs(), [self.root / 'applications', Path('/other/applications')])

    def test_parser_reads_only_main_section_and_decodes_desktop_escapes(self):
        entry = self.root / 'test.desktop'
        entry.write_text('[Desktop Entry]\nName=Edytor\\splików\\nTekst\\tTest\\\\\\s\nExec=editor %F\n'
                         '[Desktop Action Edit]\nName=Wrong name\nExec=wrong\n', encoding='utf-8')
        values = main.parse_desktop_entry(str(entry))
        self.assertEqual(values['Name'], 'Edytor plików\nTekst\tTest\\ ')
        self.assertEqual(values['Exec'], 'editor %F')

    def test_cache_notices_in_place_edit_with_unchanged_directory_timestamp(self):
        entry = self.root / 'editor.desktop'
        self.write_entry(entry)
        with patch('main.application_dirs', return_value=[self.root]):
            self.assertEqual(main.desktop_entries()['editor.desktop']['Name'], 'Editor')
            directory_mtime = self.root.stat().st_mtime_ns
            self.write_entry(entry, name='Changed Editor')
            self.assertEqual(self.root.stat().st_mtime_ns, directory_mtime)
            self.assertEqual(main.desktop_entries()['editor.desktop']['Name'], 'Changed Editor')

    def test_cache_notices_nested_addition_and_removal(self):
        directory = self.root / 'vendor'
        first = directory / 'first.desktop'
        second = directory / 'second.desktop'
        self.write_entry(first)
        with patch('main.application_dirs', return_value=[self.root]):
            self.assertIn('vendor-first.desktop', main.desktop_entries())
            root_mtime = self.root.stat().st_mtime_ns
            self.write_entry(second)
            self.assertEqual(self.root.stat().st_mtime_ns, root_mtime)
            self.assertIn('vendor-second.desktop', main.desktop_entries())
            first.unlink()
            self.assertNotIn('vendor-first.desktop', main.desktop_entries())

    def test_unchanged_entries_are_not_reparsed(self):
        self.write_entry(self.root / 'editor.desktop')
        with patch('main.application_dirs', return_value=[self.root]), \
             patch('main.parse_desktop_entry', wraps=main.parse_desktop_entry) as parse:
            self.assertIs(main.desktop_entries(), main.desktop_entries())
            self.assertEqual(parse.call_count, 1)

    def test_hidden_user_override_masks_installed_application(self):
        user, system = self.root / 'user', self.root / 'system'
        self.write_entry(user / 'editor.desktop', extra='Hidden=true\n')
        self.write_entry(system / 'editor.desktop')
        with patch('main.application_dirs', return_value=[user, system]):
            self.assertFalse(main.desktop_entry_available(main.desktop_entries()['editor.desktop']))

    def test_tryexec_must_exist_and_be_executable(self):
        executable = self.root / 'editor'
        executable.write_text('#!/bin/sh\nexit 0\n')
        values = application(TryExec=str(executable))
        self.assertFalse(main.desktop_entry_available(values))
        executable.chmod(0o755)
        self.assertTrue(main.desktop_entry_available(values))
        executable.unlink()
        self.assertFalse(main.desktop_entry_available(values))

    def test_nodisplay_can_launch_but_is_not_offered_in_dialog(self):
        values = application(NoDisplay='true')
        self.assertTrue(main.desktop_entry_available(values))
        self.assertFalse(main.desktop_entry_available(values, visible=True))
        self.assertFalse(main.desktop_entry_available(application(Exec='')))
        self.assertTrue(main.desktop_entry_available(application(Exec='', DBusActivatable='true')))

    def test_localized_name_uses_current_locale_and_language_fallback(self):
        with patch('main.QLocale', return_value=QLocale('pl_PL')):
            self.assertEqual(main.desktop_entry_name(application(**{'Name[pl]': 'Edytor'}), 'editor.desktop'), 'Edytor')
            self.assertEqual(main.desktop_entry_name(application(**{'Name[pl_PL]': 'Regionalny', 'Name[pl]': 'Edytor'}), 'editor.desktop'), 'Regionalny')


class ApplicationQueryTests(unittest.TestCase):
    def test_failed_gio_output_is_ignored_and_xdg_default_is_used(self):
        results = [completed('Default application for text/plain: wrong.desktop\n', 1), completed('editor.desktop\n')]
        with patch('main.subprocess.run', side_effect=results) as run, \
             patch('main.desktop_entries', return_value={'editor.desktop': application()}):
            result = main.Explorer.query_applications(SimpleNamespace(), 'text/plain')
        self.assertEqual(result, ('text/plain', 'editor.desktop', ['editor.desktop']))
        self.assertEqual(run.call_args_list[1].args[0][:3], ['/usr/bin/xdg-mime', 'query', 'default'])

    def test_recommended_apps_are_merged_and_unavailable_entries_are_removed(self):
        output = ('Default application for text/plain: editor.desktop\nRegistered applications:\n'
                  ' editor.desktop\n hidden.desktop\n absent.desktop\n noexec.desktop\n'
                  'Recommended applications:\n other.desktop\n editor.desktop\n')
        entries = {'editor.desktop': application(), 'hidden.desktop': application(Hidden='true'),
                   'noexec.desktop': application(TryExec='/missing/fileexplorer-regression-executable'),
                   'other.desktop': application('Other')}
        with patch('main.subprocess.run', return_value=completed(output)), patch('main.desktop_entries', return_value=entries):
            self.assertEqual(main.Explorer.query_applications(SimpleNamespace(), 'text/plain'),
                             ('text/plain', 'editor.desktop', ['editor.desktop', 'other.desktop']))

    def test_hidden_default_is_replaced_by_available_registered_app(self):
        output = ('Default application for text/plain: hidden.desktop\nRegistered applications:\n editor.desktop\n')
        with patch('main.subprocess.run', return_value=completed(output)), \
             patch('main.desktop_entries', return_value={'hidden.desktop': application(Hidden='true'), 'editor.desktop': application()}):
            self.assertEqual(main.Explorer.query_applications(SimpleNamespace(), 'text/plain')[1:],
                             ('editor.desktop', ['editor.desktop']))

    def test_file_manager_association_uses_other_registered_app(self):
        output = ('Default application for text/plain: fileexplorer.desktop\nRegistered applications:\n'
                  ' fileexplorer.desktop\n editor.desktop\n')
        with patch('main.subprocess.run', return_value=completed(output)), \
             patch('main.desktop_entries', return_value={'fileexplorer.desktop': application('Explorer'), 'editor.desktop': application()}):
            self.assertEqual(main.Explorer.query_applications(SimpleNamespace(), 'text/plain')[1:],
                             ('editor.desktop', ['editor.desktop']))


class ApplicationLaunchTests(unittest.TestCase):
    def state(self, default='', available=()):
        return SimpleNamespace(applications_for_paths=Mock(return_value=('text/plain', default, list(available))),
                               spawn=Mock(return_value=False), error=Mock())

    def test_spawn_detects_launcher_failure(self):
        process = Mock()
        process.wait.return_value = 1
        with patch('main.subprocess.Popen', return_value=process):
            self.assertFalse(main.Explorer.spawn(['/usr/bin/gtk-launch', 'missing']))
        process.wait.assert_called_once_with(timeout=0.5)

    def test_slow_launcher_is_left_running_without_retry_or_kill(self):
        process = Mock()
        process.wait.side_effect = subprocess.TimeoutExpired('xdg-open', 0.5)
        with patch('main.subprocess.Popen', return_value=process):
            self.assertTrue(main.Explorer.spawn(['/usr/bin/xdg-open', '/file']))
        process.kill.assert_not_called()
        process.terminate.assert_not_called()

    def test_explicit_app_tries_gio_launch_after_gtk_failure(self):
        state = self.state()
        state.spawn.side_effect = [False, True]
        with patch('main.desktop_entries', return_value={'editor.desktop': application()}):
            main.Explorer.launch_paths(state, ['-first.txt', 'a file.txt'], 'editor.desktop')
        gtk, gio = [call.args[0] for call in state.spawn.call_args_list]
        self.assertEqual(gtk[:2], ['/usr/bin/gtk-launch', 'editor'])
        self.assertTrue(all(value.startswith('file:///') for value in gtk[2:]))
        self.assertEqual(gio, ['/usr/bin/gio', 'launch', '/applications/editor.desktop',
                               os.path.abspath('-first.txt'), os.path.abspath('a file.txt')])
        state.error.assert_not_called()

    def test_failed_explicit_app_reports_error_instead_of_using_another_app(self):
        state = self.state()
        with patch('main.desktop_entries', return_value={'editor.desktop': application()}):
            main.Explorer.launch_paths(state, ['/file.txt'], 'editor.desktop')
        self.assertEqual(state.spawn.call_count, 2)
        state.error.assert_called_once()

    def test_hidden_explicit_app_is_not_launched(self):
        state = self.state()
        with patch('main.desktop_entries', return_value={'editor.desktop': application(Hidden='true')}):
            main.Explorer.launch_paths(state, ['/file.txt'], 'editor.desktop')
        state.spawn.assert_not_called()
        state.error.assert_called_once()

    def test_missing_launcher_fallback_opens_every_selected_file(self):
        state = self.state()
        with patch('main.desktop_entries', return_value={}), patch('main.QDesktopServices.openUrl', return_value=True) as opened:
            main.Explorer.launch_paths(state, ['/one.txt', '/two.txt'])
        self.assertEqual([call.args[0] for call in state.spawn.call_args_list],
                         [['/usr/bin/gio', 'open', '/one.txt', '/two.txt'],
                          ['/usr/bin/xdg-open', '/one.txt'], ['/usr/bin/xdg-open', '/two.txt']])
        self.assertEqual([call.args[0].toLocalFile() for call in opened.call_args_list], ['/one.txt', '/two.txt'])
        state.error.assert_not_called()

    def test_file_manager_default_without_alternative_cannot_recurse(self):
        state = self.state(default='fileexplorer.desktop')
        with patch('main.desktop_entries', return_value={'fileexplorer.desktop': application('Explorer')}):
            main.Explorer.launch_paths(state, ['/file.txt'])
        state.spawn.assert_not_called()
        state.error.assert_called_once()

    def test_file_manager_default_with_alternative_launches_editor(self):
        state = self.state(default='fileexplorer.desktop', available=['fileexplorer.desktop', 'editor.desktop'])
        state.spawn.return_value = True
        with patch('main.desktop_entries', return_value={'fileexplorer.desktop': application('Explorer'), 'editor.desktop': application()}):
            main.Explorer.launch_paths(state, ['/file.txt'])
        self.assertEqual(state.spawn.call_args.args[0][1], 'editor')
        state.error.assert_not_called()


class DefaultApplicationTests(unittest.TestCase):
    def state(self):
        return SimpleNamespace(error=Mock(), app_cache={'text/plain': (0, 'old')},
                               desktop_application=lambda _: ('Editor', ''), status_text=SimpleNamespace(setText=Mock()))

    def test_xdg_mime_fallback_updates_default_and_invalidates_cache(self):
        state = self.state()
        with patch('main.desktop_entries', return_value={'editor.desktop': application()}), \
             patch('main.subprocess.run', side_effect=[FileNotFoundError('gio unavailable'), completed()]) as run:
            main.Explorer.set_default_application(state, 'text/plain', 'editor.desktop')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/xdg-mime', 'default', 'editor.desktop', 'text/plain'])
        self.assertNotIn('text/plain', state.app_cache)
        state.status_text.setText.assert_called_once_with('Domyślna aplikacja: Editor')
        state.error.assert_not_called()

    def test_failed_default_change_preserves_cache(self):
        state = self.state()
        with patch('main.desktop_entries', return_value={'editor.desktop': application()}), \
             patch('main.subprocess.run', return_value=completed(returncode=1, stderr='failure')):
            main.Explorer.set_default_application(state, 'text/plain', 'editor.desktop')
        self.assertIn('text/plain', state.app_cache)
        state.status_text.setText.assert_not_called()
        state.error.assert_called_once()

    def test_file_manager_cannot_be_assigned_to_regular_files(self):
        state = self.state()
        with patch('main.desktop_entries', return_value={'fileexplorer.desktop': application('Explorer')}), \
             patch('main.subprocess.run') as run:
            main.Explorer.set_default_application(state, 'text/plain', 'fileexplorer.desktop')
        run.assert_not_called()
        state.error.assert_called_once()


class OpenWithDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        entries = {'editor.desktop': application(), 'hidden.desktop': application(Hidden='true'),
                   'noexec.desktop': application(Exec=''), 'private.desktop': application(NoDisplay='true'),
                   'missing.desktop': application(TryExec='/missing/fileexplorer-regression-executable'),
                   'fileexplorer.desktop': application('Explorer')}
        with patch('main.desktop_entries', return_value=entries):
            self.dialog = main.OpenWithDialog(None, 'text/plain')
        self.addCleanup(self.dialog.deleteLater)

    def test_unavailable_and_hidden_apps_are_not_offered(self):
        self.assertEqual(self.dialog.tree.topLevelItemCount(), 1)
        self.assertEqual(self.dialog.tree.topLevelItem(0).text(0), 'Editor')

    def test_accept_requires_selection_or_command(self):
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.Rejected)
        self.assertIsNone(self.dialog.chosen_id)

    def test_filter_removes_hidden_selection(self):
        self.dialog.tree.topLevelItem(0).setSelected(True)
        self.assertTrue(self.dialog.open_button.isEnabled())
        self.dialog.search.setText('no match')
        self.assertFalse(self.dialog.open_button.isEnabled())
        self.assertEqual(self.dialog.tree.selectedItems(), [])

    def test_valid_selection_accepts_and_keeps_default_choice(self):
        self.dialog.tree.topLevelItem(0).setSelected(True)
        self.dialog.default_check.setChecked(True)
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.Accepted)
        self.assertEqual(self.dialog.chosen_id, 'editor.desktop')
        self.assertTrue(self.dialog.set_default)

    def test_unclosed_command_quote_does_not_dismiss_dialog(self):
        self.dialog.command.setText('editor "unterminated')
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.Rejected)
        self.dialog.command.setText('editor --new-window')
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.Accepted)
        self.assertEqual(self.dialog.chosen_command, 'editor --new-window')
        self.assertFalse(self.dialog.set_default)


if __name__ == '__main__':
    unittest.main()
