"""KDE menu fixtures and argv tests; no real service command is executed."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import service_menus


MENU = '''[Desktop Entry]
Type=Service
MimeType=text/*;application/json;
Actions=view;
X-KDE-Submenu=Tools
X-KDE-Submenu[pl]=Narzędzia

[Desktop Action view]
Name=View
Name[pl]=Zobacz
Icon=document-open
Exec=/bin/echo %F
'''


class ServiceMenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='fileexplorer-service-menu-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'menu.desktop'
        self.path.write_text(MENU)
        self.action, = service_menus.read_menu(self.path, language='pl_PL')
        self.config = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root / 'config')})
        self.config.start()
        self.addCleanup(self.config.stop)

    def test_localized_labels_mime_globs_and_mixed_selection(self):
        self.assertEqual((self.action.name, self.action.submenu), ('Zobacz', 'Narzędzia'))
        self.assertTrue(service_menus.applicable(self.action, ['a', 'b'], ['text/plain', 'application/json']))
        self.assertFalse(service_menus.applicable(self.action, ['a', 'b'], ['text/plain', 'image/png']))
        self.assertFalse(service_menus.applicable(self.action, ['a'], ['inode/directory']))

    def test_directory_count_protocol_and_exclude_filters(self):
        action = replace(self.action, mimetypes=('all/all',), excluded=('image/*',),
                         required=(2, 4), maximum=4, protocols=('file',))
        self.assertTrue(service_menus.applicable(action, ['a', 'b'], ['inode/directory', 'text/plain']))
        self.assertFalse(service_menus.applicable(action, ['a'], ['text/plain']))
        self.assertFalse(service_menus.applicable(action, ['a', 'b'], ['image/png', 'text/plain']))
        self.assertFalse(service_menus.applicable(action, ['a', 'b'], ['text/plain'] * 2, protocols=('file', 'smb')))

    def test_mime_inheritance_and_octet_stream_apply_to_files(self):
        action = replace(self.action, mimetypes=('application/octet-stream',))
        self.assertTrue(service_menus.applicable(action, ['a'], ['application/pdf']))
        self.assertFalse(service_menus.applicable(action, ['a'], ['inode/directory']))
        action = replace(self.action, mimetypes=('text/plain',))
        self.assertTrue(service_menus.applicable(action, ['a'], ['text/x-python'],
                                                inherits=lambda actual, wanted: wanted == 'text/plain'))

    def test_hostile_names_stay_separate_literal_argv_and_are_expanded_once(self):
        paths = [self.root / '$(touch hacked); %u zażółć.txt', self.root / 'a\nb "quote".txt']
        argv, = service_menus.commands(self.action, paths)
        self.assertEqual(argv, ['/bin/echo', *map(str, paths)])
        action = replace(self.action, command='/bin/echo %% %c %k %i %d %n %F')
        argv, = service_menus.commands(action, paths)
        self.assertEqual(argv, ['/bin/echo', '%', 'Zobacz', str(self.path), '--icon', 'document-open', *map(str, paths)])

    def test_single_file_code_launches_one_command_per_selected_file(self):
        paths = [self.root / 'first file', self.root / 'second']
        commands = service_menus.commands(replace(self.action, command='/bin/echo %f'), paths)
        self.assertEqual(commands, [['/bin/echo', str(path)] for path in paths])
        commands = service_menus.commands(replace(self.action, command='/bin/echo %U'), paths)
        self.assertEqual(commands, [['/bin/echo', *(path.as_uri() for path in paths)]])

    def test_invalid_unknown_or_embedded_list_field_codes_are_refused(self):
        for command in ('/bin/echo %Z', '/bin/echo %', '/bin/echo a%F', '/bin/echo %f %U', '%f --help'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                service_menus.parse_exec(command)

    def test_interpreter_code_cannot_receive_interpolated_paths(self):
        with self.assertRaises(ValueError):
            service_menus.parse_exec('/bin/sh -c "echo %f"')
        action = replace(self.action, command='/bin/sh -c \'printf "%%s\\\\n" "$@"\' -- %F')
        argv, = service_menus.commands(action, [self.root / 'a; touch HACKED'])
        self.assertEqual(argv[-2:], ['--', str(self.root / 'a; touch HACKED')])
        self.assertIn('$@', argv[2])

    def test_tryexec_user_authorization_and_hidden_override(self):
        self.assertEqual(service_menus.read_menu(self.path, user=True), ())
        self.path.chmod(0o700)
        self.assertTrue(service_menus.read_menu(self.path, user=True))
        self.path.write_text(MENU.replace('Actions=view;', 'Actions=view;\nTryExec=nonexistent-fileexplorer-test-tool'))
        self.assertEqual(service_menus.read_menu(self.path), ())
        user, system = self.root / 'user', self.root / 'system'
        user.mkdir(); system.mkdir()
        (user / 'override.desktop').write_text(MENU.replace('Type=Service', 'Type=Service\nHidden=true'))
        (user / 'override.desktop').chmod(0o700)
        (system / 'override.desktop').write_text(MENU)
        self.assertEqual(service_menus.load([(user, True), (system, False)]), ())

    def test_disabled_kde_actions_and_kiosk_restrictions_are_respected(self):
        config = self.root / 'config'
        config.mkdir()
        (config / 'kservicemenurc').write_text('[Show]\nview=false\n')
        self.assertEqual(service_menus.load([(self.root, False)]), ())
        (config / 'kservicemenurc').unlink()
        (config / 'kdeglobals').write_text('[KDE Action Restrictions]\nshell_access=false\n')
        self.path.write_text(MENU.replace('Actions=view;', 'Actions=view;\nX-KDE-AuthorizeAction=shell_access'))
        self.assertEqual(service_menus.load([(self.root, False)]), ())

    def test_legacy_kde_service_types_are_loaded(self):
        self.path.write_text(MENU.replace('MimeType=text/*;application/json;',
                                         'X-KDE-ServiceTypes=KonqPopupMenu/Plugin,font/ttf,font/otf'))
        action, = service_menus.read_menu(self.path)
        self.assertEqual(action.mimetypes, ('font/ttf', 'font/otf'))

    def test_launch_is_direct_argv_and_refuses_desktop_changed_since_menu_opened(self):
        launch = Mock()
        service_menus.launch(self.action, [self.root / 'file'], launch)
        self.assertEqual(launch.call_args.args[0], ['/bin/echo', str(self.root / 'file')])
        self.assertNotIn('shell', launch.call_args.kwargs)
        self.path.write_text(MENU + '\n# changed\n')
        with self.assertRaisesRegex(OSError, 'zmieniło'):
            service_menus.launch(self.action, [self.root / 'file'], launch)
        self.assertEqual(launch.call_count, 1)


if __name__ == '__main__':
    unittest.main()
