"""KDE .desktop service menus with direct argv expansion and selection filtering.

References: develop.kde.org/docs/apps/dolphin/service-menus/ and the
freedesktop Desktop Entry Exec specification. No implicit shell is used.
"""
import configparser
from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from urllib.parse import urlparse


def unescape(value):
    return re.sub(r'\\([sntr\\])', lambda match: {'s': ' ', 'n': '\n', 't': '\t', 'r': '\r', '\\': '\\'}[match[1]], value)


def localized(values, key, language=None):
    language = (language or os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES')
                or os.environ.get('LANG') or 'pl').split('.')[0]
    for variant in (language, language.split('_')[0], ''):
        value = values.get(f'{key}[{variant}]' if variant else key)
        if value:
            return unescape(value)
    return ''


def items(value):
    return tuple(part.strip() for part in re.split('[;,]', value or '') if part.strip())


def read_groups(path):
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    with Path(path).open(encoding='utf-8', errors='replace') as stream:
        config.read_file(stream)
    return {section: dict(config[section]) for section in config.sections()}


def menu_locations():
    home = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share')))
    roots = [home] + [Path(path) for path in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if path]
    return tuple((root / folder, root == home) for root in roots
                 for folder in ('kio/servicemenus', 'kservices5/ServiceMenus', 'kservices5'))


@dataclass(frozen=True)
class ServiceAction:
    source: str
    action_id: str
    name: str
    icon: str
    command: str
    submenu: str
    priority: str
    mimetypes: tuple
    excluded: tuple
    required: tuple
    minimum: int
    maximum: int
    protocols: tuple
    excluded_protocol: str
    try_exec: str
    terminal: bool
    working_directory: str
    fingerprint: tuple


def fingerprint(path):
    info = os.stat(path)
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _bool(value):
    return str(value).lower() == 'true'


def read_menu(path, user=False, language=None, disabled=(), restrictions=()):
    path = Path(path)
    if user and not os.access(path, os.X_OK):
        return ()
    groups = read_groups(path)
    main = groups.get('Desktop Entry', {})
    if main.get('Type') != 'Service' or _bool(main.get('Hidden', False)) or _bool(main.get('NoDisplay', False)):
        return ()
    if any(value in restrictions for value in items(main.get('X-KDE-AuthorizeAction'))):
        return ()
    mimetypes = items(main.get('MimeType')) or tuple(value for value in items(
        main.get('ServiceTypes') or main.get('X-KDE-ServiceTypes')) if value != 'KonqPopupMenu/Plugin')
    if not mimetypes:
        return ()
    protocol = main.get('X-KDE-Protocol', '')
    protocols = (protocol,) if protocol and not protocol.startswith('!') else items(main.get('X-KDE-Protocols'))
    required = tuple(int(value) for value in items(main.get('X-KDE-RequiredNumberOfUrls')))
    minimum, maximum = int(main.get('X-KDE-MinNumberOfUrls', 1)), int(main.get('X-KDE-MaxNumberOfUrls', 1000000))
    result = []
    for action_id in items(main.get('Actions')):
        if action_id in disabled or action_id == '_SEPARATOR_':
            continue
        values = groups.get('Desktop Action ' + action_id, {})
        command, name = values.get('Exec', ''), localized(values, 'Name', language)
        if not command or not name:
            continue
        action = ServiceAction(str(path), action_id, name, values.get('Icon', main.get('Icon', '')),
                               command, localized(main, 'X-KDE-Submenu', language), main.get('X-KDE-Priority', ''),
                               mimetypes, items(main.get('ExcludeServiceTypes')), required, minimum, maximum,
                               protocols, protocol[1:] if protocol.startswith('!') else '',
                               values.get('TryExec', main.get('TryExec', '')), _bool(values.get('Terminal', main.get('Terminal', False))),
                               unescape(values.get('Path', main.get('Path', ''))), fingerprint(path))
        try:
            tokens, _ = parse_exec(action.command)
        except ValueError:
            continue
        if not shutil.which(tokens[0]):
            continue
        if action.try_exec and not shutil.which(unescape(action.try_exec)):
            continue
        result.append(action)
    return tuple(result)


def load(locations=None, language=None):
    config_root = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    disabled, restrictions = (), ()
    try:
        disabled = tuple(key for key, value in read_groups(config_root / 'kservicemenurc').get('Show', {}).items() if value == 'false')
    except (OSError, configparser.Error):
        pass
    try:
        restrictions = tuple(key for key, value in read_groups(config_root / 'kdeglobals').get('KDE Action Restrictions', {}).items() if value == 'false')
    except (OSError, configparser.Error):
        pass
    result, seen = [], set()
    for directory, user in menu_locations() if locations is None else locations:
        try:
            paths = sorted(Path(directory).glob('*.desktop'))
        except OSError:
            continue
        for path in paths:
            if path.name in seen:
                continue
            seen.add(path.name)  # a hidden user override suppresses the system copy
            try:
                result.extend(read_menu(path, user, language, disabled, restrictions))
            except (OSError, ValueError, configparser.Error):
                continue
    return tuple(result)


def parse_exec(command):
    tokens = shlex.split(unescape(command), posix=True)
    if not tokens or '=' in tokens[0] or '%' in tokens[0] or '\x00' in command:
        raise ValueError('Nieprawidłowe pole Exec menu KDE.')
    files = []
    for token in tokens:
        for match in re.finditer(r'%(.)|%$', token):
            code = match[1]
            if code is None or code not in 'fFuUickdDnNvm%':
                raise ValueError('Nieznany kod pola w menu KDE.')
            if code in 'fFuU':
                files.append(code)
            if code in 'FUi' and token != '%' + code:
                raise ValueError('Lista plików musi stanowić osobny argument.')
    if len(files) > 1:
        raise ValueError('Exec może zawierać tylko jedno pole plików.')
    # Do not interpolate filenames into a shell/interpreter program. Positional
    # arguments following a constant script remain ordinary, safe argv entries.
    interpreters = {'sh', 'bash', 'dash', 'zsh', 'ksh', 'python', 'python3', 'perl', 'ruby', 'node'}
    has_interpreter = any(os.path.basename(token) in interpreters or re.fullmatch(r'python\d+(?:\.\d+)?', os.path.basename(token))
                          for token in tokens)
    for index, token in enumerate(tokens[:-1]):
        if has_interpreter and token in {'-c', '--command', '-e', '--eval'} and re.search(r'%[fFuU]', tokens[index + 1]):
            raise ValueError('Nie można wstawiać ścieżek do kodu powłoki lub interpretera.')
    return tokens, files[0] if files else None


def commands(action, paths, terminal='konsole'):
    paths = tuple(os.path.abspath(os.fspath(path)) for path in paths)
    tokens, field = parse_exec(action.command)
    if action.try_exec and not shutil.which(unescape(action.try_exec)):
        raise OSError('Program wymagany przez menu KDE jest niedostępny.')
    if not shutil.which(tokens[0]):
        raise OSError(f'Program {tokens[0]} jest niedostępny.')
    batches = [(path,) for path in paths] if field in {'f', 'u'} else [paths]
    result = []
    for batch in batches:
        argv = []
        for token in tokens:
            if token in {'%F', '%U'}:
                argv.extend(Path(path).as_uri() if token == '%U' else path for path in batch)
                continue
            if token == '%i':
                if action.icon:
                    argv.extend(['--icon', action.icon])
                continue
            values = {'f': batch[0] if batch else '', 'u': Path(batch[0]).as_uri() if batch else '',
                      'c': action.name, 'k': action.source, '%': '%'}
            expanded = re.sub(r'%(.)', lambda match: values.get(match[1], ''), token)
            if expanded or not token:
                argv.append(expanded)
        if action.terminal:
            if not shutil.which(terminal):
                raise OSError('Terminal wymagany przez menu KDE jest niedostępny.')
            argv = [terminal, '--hold', '-e', *argv]
        result.append(argv)
    return result


def applicable(action, paths, mimetypes, inherits=None, protocols=None):
    count = len(paths)
    if not count or len(mimetypes) != count or count < action.minimum or count > action.maximum:
        return False
    if action.required and count not in action.required:
        return False
    protocols = protocols or tuple(urlparse(path).scheme or 'file' for path in paths)
    if action.protocols and any(protocol not in action.protocols for protocol in protocols):
        return False
    if action.excluded_protocol and action.excluded_protocol in protocols:
        return False
    if action.try_exec and not shutil.which(unescape(action.try_exec)):
        return False

    def matches(mime, wanted):
        return any(value == 'all/all' or (mime != 'inode/directory' and value in {'allfiles', 'all/allfiles', 'application/octet-stream'})
                   or fnmatch.fnmatchcase(mime, value) or bool(inherits and inherits(mime, value)) for value in wanted)
    return all(matches(mime, action.mimetypes) and not matches(mime, action.excluded) for mime in mimetypes)


def launch(action, paths, popen=None):
    if fingerprint(action.source) != action.fingerprint:
        raise OSError('Menu KDE zmieniło się. Otwórz menu kontekstowe ponownie za kilka sekund.')
    popen = popen or subprocess.Popen
    return tuple(popen(argv, cwd=action.working_directory or None, start_new_session=True)
                 for argv in commands(action, paths))
