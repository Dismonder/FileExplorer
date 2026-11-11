#!/usr/bin/env python3
"""Install the current checkout as the user's default file manager, without root."""
import argparse
import ast
import configparser
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from linux_integration import CONTROL_OBJECT, INTERFACE, SERVICE, REMOTE_SCHEMES, desktop_exec

ROOT = Path(__file__).resolve().parent
DESKTOP_ID = 'fileexplorer.desktop'
META_E = 268435525
ACTION = [DESKTOP_ID, '_launch', 'Eksplorator plików', 'Uruchom Eksplorator plików']
FILE_MANAGER_MIMES = ('inode/directory', 'x-scheme-handler/trash',
                      *(f'x-scheme-handler/{scheme}' for scheme in sorted(REMOTE_SCHEMES)))


def xdg_paths():
    home = Path.home()
    return (Path(os.environ.get('XDG_CONFIG_HOME', home / '.config')),
            Path(os.environ.get('XDG_DATA_HOME', home / '.local/share')),
            Path(os.environ.get('XDG_STATE_HOME', home / '.local/state')))


def desktop_entry():
    # xdg-utils identifies the executable by splitting at spaces and does not unquote it.
    # env gives it an unquoted executable while keeping repository paths safely quoted.
    command = '/usr/bin/env ' + desktop_exec([ROOT / 'fileexplorer'])
    return ('[Desktop Entry]\nVersion=1.0\nType=Application\n'
            'Name=Eksplorator plików\nGenericName=Menedżer plików\n'
            'Comment=Menedżer plików dla KDE Plasma\n'
            f'Exec={command} %U\nTryExec={ROOT / "fileexplorer"}\n'
            f'Icon={ROOT / "assets/fileexplorer.png"}\n'
            'Terminal=false\nCategories=Qt;System;FileTools;FileManager;\n'
            f'MimeType={";".join(FILE_MANAGER_MIMES)};\n'
            'StartupNotify=true\nStartupWMClass=FileExplorer\nX-KDE-Shortcuts=Meta+E\n'
            'Actions=Home;Computer;Trash;\n\n[Desktop Action Home]\n'
            f'Name=Folder osobisty\nExec={command} {desktop_exec([Path.home()])}\n'
            'Icon=user-home\n\n[Desktop Action Computer]\nName=Ten komputer\n'
            f'Exec={command} computer\nIcon=computer\n\n[Desktop Action Trash]\n'
            f'Name=Kosz\nExec={command} trash:/\nIcon=user-trash\n')


def autostart_entry():
    return ('[Desktop Entry]\nType=Application\nName=Eksplorator plików (w tle)\n'
            f'Exec={desktop_exec([ROOT / "fileexplorer", "--dbus"])}\n'
            'Icon=folder\nNoDisplay=true\nX-KDE-autostart-phase=2\n'
            'X-GNOME-Autostart-enabled=true\n')


def read_ini(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    if path.is_file():
        parser.read(path, encoding='utf-8')
    return parser


def ini_text(parser):
    output = io.StringIO()
    parser.write(output, space_around_delimiters=False)
    return output.getvalue()


def mime_configuration(path, set_default=False):
    parser = read_ini(path)
    # The old registration hijacked local file URLs, which should use each file's MIME type.
    for section in ('Default Applications', 'Added Associations', 'Removed Associations'):
        if parser.has_option(section, 'x-scheme-handler/file'):
            values = [v for v in parser[section]['x-scheme-handler/file'].split(';')
                      if v and v != DESKTOP_ID]
            if values:
                parser[section]['x-scheme-handler/file'] = ';'.join(values) + ';'
            else:
                parser.remove_option(section, 'x-scheme-handler/file')
    if set_default:
        for section in ('Default Applications', 'Added Associations'):
            if not parser.has_section(section):
                parser.add_section(section)
            for mime in FILE_MANAGER_MIMES:
                previous = parser[section].get(mime, '').split(';')
                values = [DESKTOP_ID] if section == 'Default Applications' else [DESKTOP_ID, *previous]
                parser[section][mime] = ';'.join(dict.fromkeys(v for v in values if v)) + ';'
        if parser.has_section('Removed Associations'):
            for mime in FILE_MANAGER_MIMES:
                values = [v for v in parser['Removed Associations'].get(mime, '').split(';')
                          if v and v != DESKTOP_ID]
                if values:
                    parser['Removed Associations'][mime] = ';'.join(values) + ';'
                else:
                    parser.remove_option('Removed Associations', mime)
    return ini_text(parser)


def atomic_write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.fileexplorer-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(text.encode('utf-8') if isinstance(text, str) else text)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(command, required=True):
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    if required and result.returncode:
        raise RuntimeError(f'{command[0]}: {result.stderr.strip() or result.stdout.strip()}')
    return result


def dbus_call(destination, path, method, *args, required=True):
    return run(['gdbus', 'call', '--session', '--dest', destination, '--object-path', path,
                '--method', method, *args], required=required)


def shortcut_call(method, *args, required=True):
    return dbus_call('org.kde.kglobalaccel', '/kglobalaccel',
                     'org.kde.KGlobalAccel.' + method, *args, required=required)


def dbus_values(output):
    # GVariant adds type annotations to empty arrays, e.g. (@ai [],).
    output = output.strip()
    if output in ('(true,)', '(false,)'):
        return (output == '(true,)',)
    output = re.sub(r'^\((?:@ai|@as|uint32|int32|uint64|int64) ', '(', output)
    return ast.literal_eval(output)


def configure_shortcut():
    owner = shortcut_call('action', str(META_E))
    actions = dbus_values(owner.stdout)[0]
    if actions and actions[0] not in ('org.kde.dolphin.desktop', DESKTOP_ID):
        raise RuntimeError(f'Meta+E należy do innej aplikacji: {actions[0]}')
    if actions and actions[0] == 'org.kde.dolphin.desktop':
        existing = shortcut_call('shortcut', repr(actions))
        keys = dbus_values(existing.stdout)[0]
        shortcut_call('setForeignShortcut', repr(actions), repr([k for k in keys if k != META_E]))
    shortcut_call('doRegister', repr(ACTION))
    # NoAutoloading | SetPresent: save this assignment and make it active now.
    result = shortcut_call('setShortcut', repr(ACTION), repr([META_E]), '6')
    if META_E not in dbus_values(result.stdout)[0]:
        raise RuntimeError('KDE nie przydzieliło skrótu Meta+E Eksploratorowi.')


def shortcut_preflight():
    owner = shortcut_call('action', str(META_E))
    actions = dbus_values(owner.stdout)[0]
    if actions and actions[0] not in ('org.kde.dolphin.desktop', DESKTOP_ID):
        raise RuntimeError(f'Meta+E należy do innej aplikacji: {actions[0]}')


def snapshot(paths, state):
    directory = state / 'FileExplorer/integration-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    directory.mkdir(parents=True, mode=0o700)
    entries = []
    for index, path in enumerate(paths):
        item = {'path': str(path), 'exists': os.path.lexists(path)}
        if path.is_symlink():
            item['symlink'] = os.readlink(path)
        elif path.is_file():
            item['copy'] = str(index)
            shutil.copy2(path, directory / str(index))
        entries.append(item)
    if shutil.which('gdbus') and os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        for name in ('org.kde.dolphin.desktop', DESKTOP_ID):
            result = shortcut_call('shortcut', repr([name, '_launch', '', '']), required=False)
            if not result.returncode:
                entries.append({'shortcut': name, 'keys': dbus_values(result.stdout)[0]})
        result = shortcut_call('action', str(META_E), required=False)
        if not result.returncode:
            entries.append({'meta_e_owner': dbus_values(result.stdout)[0]})
    atomic_write(directory / 'manifest.json', json.dumps(entries, ensure_ascii=False, indent=2), 0o600)
    return directory


def refresh_desktop(data):
    for command in (['update-desktop-database', str(data / 'applications')], ['kbuildsycoca6', '--noincremental']):
        if shutil.which(command[0]):
            result = run(command, required=False)
            if result.returncode:
                print(f'Uwaga: {command[0]}: {result.stderr.strip()}', file=sys.stderr)


def install():
    config, data, state = xdg_paths()
    if 'KDE' in os.environ.get('XDG_CURRENT_DESKTOP', '') and shutil.which('gdbus'):
        shortcut_preflight()
    application = data / 'applications' / DESKTOP_ID
    shortcut = data / 'kglobalaccel' / DESKTOP_ID
    entries = {
        application: desktop_entry(), shortcut: desktop_entry(),
        config / 'autostart/fileexplorer-background.desktop': autostart_entry(),
    }
    for name in (SERVICE, INTERFACE):
        entries[data / 'dbus-1/services' / f'{name}.service'] = (
            f'[D-BUS Service]\nName={name}\nExec={shlex.join([str(ROOT / "fileexplorer"), "--dbus"])}\n')
    primary_mime = config / 'mimeapps.list'
    entries[primary_mime] = mime_configuration(primary_mime, set_default=True)
    for path in (config / 'kde-mimeapps.list', data / 'applications/mimeapps.list'):
        if path.exists():
            entries[path] = mime_configuration(path, set_default=True)
    settings = config / 'Damian/FileExplorer.conf'
    parser = read_ini(settings)
    if not parser.has_section('General'):
        parser.add_section('General')
    parser['General']['keep_running'] = 'true'
    entries[settings] = ini_text(parser)
    launcher = Path.home() / '.local/bin/fileexplorer'
    # Validate generated entry before changing any desktop configuration.
    if shutil.which('desktop-file-validate'):
        with tempfile.TemporaryDirectory(prefix='fileexplorer-entry-') as directory:
            check = Path(directory) / DESKTOP_ID
            check.write_text(entries[application])
            run(['desktop-file-validate', str(check)])
    backup = snapshot([*entries, launcher], state)
    print(f'Kopia ustawień: {backup}')
    try:
        for path, content in entries.items():
            mode = path.stat().st_mode & 0o777 if path.is_file() else (0o600 if path == settings else 0o644)
            atomic_write(path, content, mode)
        # A shell wrapper also works if the repository path contains spaces.
        atomic_write(launcher, '#!/bin/sh\nexec ' + shlex.quote(str(ROOT / 'fileexplorer')) + ' "$@"\n', 0o755)
        refresh_desktop(data)
        if 'KDE' in os.environ.get('XDG_CURRENT_DESKTOP', '') and shutil.which('gdbus'):
            configure_shortcut()
        if shutil.which('gdbus') and os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            dbus_call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus.ReloadConfig')
        verify()
    except Exception:
        try:
            restore(backup)
        except Exception as rollback_error:
            print(f'Nie można automatycznie przywrócić konfiguracji: {rollback_error}. Kopia: {backup}', file=sys.stderr)
        raise


def restore(directory):
    directory = Path(directory).resolve()
    entries = json.loads((directory / 'manifest.json').read_text())
    meta_e_owner = None
    for item in entries:
        if 'meta_e_owner' in item:
            meta_e_owner = item['meta_e_owner']
            continue
        if 'shortcut' in item:
            shortcut_call('setForeignShortcut', repr([item['shortcut'], '_launch', '', '']), repr(item['keys']))
            continue
        path = Path(item['path'])
        if 'symlink' in item:
            path.unlink(missing_ok=True)
            path.symlink_to(item['symlink'])
        elif item['exists']:
            saved = directory / item['copy']
            atomic_write(path, saved.read_bytes(), saved.stat().st_mode & 0o777)
        else:
            path.unlink(missing_ok=True)
    if meta_e_owner:
        for item in entries:
            if 'shortcut' in item and item['shortcut'] != meta_e_owner[0]:
                shortcut_call('setForeignShortcut', repr([item['shortcut'], '_launch', '', '']),
                              repr([key for key in item['keys'] if key != META_E]))
        saved_keys = next((item['keys'] for item in entries if item.get('shortcut') == meta_e_owner[0]), [META_E])
        shortcut_call('setForeignShortcut', repr(meta_e_owner), repr(saved_keys))
    refresh_desktop(xdg_paths()[1])
    if shutil.which('gdbus') and os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        dbus_call('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus.ReloadConfig')
    print('Przywrócono konfigurację z kopii. Otwarte procesy pozostają uruchomione.')


def verify():
    for mime in FILE_MANAGER_MIMES:
        result = run(['xdg-mime', 'query', 'default', mime])
        name = result.stdout.strip()
        print(f'{mime}: {name}')
        if name != DESKTOP_ID:
            raise RuntimeError(f'Niewłaściwa aplikacja domyślna dla {mime}: {name}')
    if shutil.which('gdbus') and os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        result = dbus_call(SERVICE, CONTROL_OBJECT, SERVICE + '.GetState', required=False)
        if result.returncode:
            print('Działająca instancja wymaga ponownego uruchomienia, aby wczytać poprawki.')
        else:
            print('Działająca instancja: ' + result.stdout.strip())
        owner = dbus_call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                          'org.freedesktop.DBus.GetNameOwner', INTERFACE, required=False)
        ours = dbus_call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                         'org.freedesktop.DBus.GetNameOwner', SERVICE, required=False)
        if owner.returncode or owner.stdout != ours.stdout:
            print('Uwaga: FileManager1 jest zajęte; Eksplorator przejmie usługę po zakończeniu obecnego właściciela.')
        else:
            print('FileManager1: Eksplorator plików')
        if 'KDE' in os.environ.get('XDG_CURRENT_DESKTOP', ''):
            result = shortcut_call('action', str(META_E))
            action = dbus_values(result.stdout)[0]
            print('Meta+E: ' + (action[0] if action else 'brak'))
            if not action or action[0] != DESKTOP_ID:
                raise RuntimeError('Meta+E nie wskazuje Eksploratora.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='sprawdź konfigurację bez jej zmiany')
    group.add_argument('--restore', metavar='BACKUP', help='przywróć ustawienia z podanej kopii')
    args = parser.parse_args()
    try:
        if args.check:
            verify()
        elif args.restore:
            restore(args.restore)
        else:
            install()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f'Integracja: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
