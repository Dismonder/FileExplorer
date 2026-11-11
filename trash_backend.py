"""Freedesktop trash locations and metadata, including mounted volumes.

Specification: https://specifications.freedesktop.org/trash/latest/
This module does not delete files or follow a trashed symbolic link.
"""
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import stat
from urllib.parse import quote_from_bytes, unquote_to_bytes


@dataclass(frozen=True)
class TrashRoot:
    path: Path
    topdir: Path | None = None

    @property
    def files(self):
        return self.path / 'files'

    @property
    def info(self):
        return self.path / 'info'

    @property
    def base(self):
        return self.topdir if self.topdir is not None else self.path.parent


def mount_points():
    """Read mount points without querying hardware or mounting any volume."""
    try:
        lines = Path('/proc/self/mountinfo').read_text().splitlines()
    except OSError:
        return [Path('/')]
    result = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        decoded = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        result.add(Path(decoded))
    return sorted(result, key=lambda p: len(p.parts), reverse=True)


def canonical_entry(path):
    """Resolve the parent, preserving the final component even for broken links."""
    path = Path(os.path.abspath(path))
    return path.parent.resolve() / path.name


def containing_mount(path, mounts=None):
    path = canonical_entry(path)
    candidates = mount_points() if mounts is None else mounts
    for top in sorted(candidates, key=lambda p: len(Path(p).parts), reverse=True):
        top = Path(top)
        if path.is_relative_to(top):
            return top
    return Path(path.anchor)


def _private(path, create=False):
    path = Path(path)
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PermissionError(f'Niebezpieczny katalog kosza: {path}')
    return True


def _shared(top):
    try:
        info = (Path(top) / '.Trash').lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and bool(info.st_mode & stat.S_ISVTX)


def ensure(root):
    root.path.parent.mkdir(parents=True, exist_ok=True)
    _private(root.path, create=True)
    _private(root.files, create=True)
    _private(root.info, create=True)
    return root


def _existing_device(path):
    path = Path(path)
    while not path.exists():
        if path.parent == path:
            raise FileNotFoundError(str(path))
        path = path.parent
    return path.stat().st_dev


def choose(source, home_root, mounts=None):
    """Prefer same-volume trash; safely fall back to the user's home trash."""
    source = canonical_entry(source)
    home = TrashRoot(Path(home_root))
    if source.lstat().st_dev == _existing_device(home.path):
        return ensure(home)
    top = containing_mount(source, mounts)
    candidates = []
    if _shared(top):
        candidates.append(TrashRoot(top / '.Trash' / str(os.getuid()), top))
    candidates.append(TrashRoot(top / f'.Trash-{os.getuid()}', top))
    for root in candidates:
        try:
            if source == root.path or source.is_relative_to(root.path):
                raise ValueError('Nie można przenieść kosza do niego samego.')
            return ensure(root)
        except OSError:
            continue
    return ensure(home)


def roots(home_root, mounts=None):
    """Aggregate existing safe locations; scanning never creates directories."""
    home = TrashRoot(Path(home_root))
    result = []
    try:
        if _private(home.path) and _private(home.files) and _private(home.info):
            result.append(home)
    except OSError:
        pass
    for top in mount_points() if mounts is None else mounts:
        top = Path(top)
        candidates = [top / f'.Trash-{os.getuid()}']
        if _shared(top):
            candidates.append(top / '.Trash' / str(os.getuid()))
        for path in candidates:
            if path == home.path:
                continue
            try:
                if _private(path) and _private(path / 'files') and _private(path / 'info'):
                    result.append(TrashRoot(path, top))
            except OSError:
                pass
    return list(dict.fromkeys(result))


def for_storage(stored, home_root):
    stored = Path(stored)
    if stored.parent.name != 'files':
        raise ValueError(f'Element nie znajduje się w katalogu files kosza: {stored}')
    path = stored.parent.parent
    if path == Path(home_root):
        return TrashRoot(path)
    if path.name == f'.Trash-{os.getuid()}':
        return TrashRoot(path, path.parent)
    if path.name == str(os.getuid()) and path.parent.name == '.Trash' and _shared(path.parent.parent):
        return TrashRoot(path, path.parent.parent)
    raise ValueError(f'Nieznany katalog kosza: {path}')


def info_path(stored, home_root):
    return for_storage(stored, home_root).info / f'{Path(stored).name}.trashinfo'


def write_info(source, target, home_root):
    source = canonical_entry(source)
    root = ensure(for_storage(target, home_root))
    original = source
    if root.topdir is not None:
        original = source.relative_to(root.topdir)
    encoded = quote_from_bytes(os.fsencode(original), safe='/')
    metadata = root.info / f'{Path(target).name}.trashinfo'
    descriptor = os.open(metadata, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(f'[Trash Info]\nPath={encoded}\nDeletionDate={datetime.now():%Y-%m-%dT%H:%M:%S}\n')
            stream.flush()
    except Exception:
        metadata.unlink(missing_ok=True)
        raise
    return metadata


def reserve(source, home_root, mounts=None):
    source = canonical_entry(source)
    root = choose(source, home_root, mounts)
    number = 1
    while True:
        name = source.name if number == 1 else f'{source.name}.{number}'
        target = root.files / name
        if not os.path.lexists(target):
            try:
                return target, write_info(source, target, home_root)
            except FileExistsError:
                pass
        number += 1


def read_info(stored, root):
    """Read first values only, bounded and without following metadata symlinks."""
    metadata = root.info / f'{Path(stored).name}.trashinfo'
    try:
        descriptor = os.open(metadata, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return '', ''
            lines = stream.read(65536).decode('utf-8', errors='replace').splitlines()
    except OSError:
        return '', ''
    if not lines or lines[0] != '[Trash Info]':
        return '', ''
    values = {}
    for line in lines[1:]:
        if '=' in line:
            key, value = line.split('=', 1)
            if key in ('Path', 'DeletionDate'):
                values.setdefault(key, value)
    original = os.fsdecode(unquote_to_bytes(values.get('Path', '')))
    if not original or '\x00' in original:
        return '', values.get('DeletionDate', '')
    path = Path(original)
    if not path.is_absolute():
        if '..' in path.parts:
            return '', values.get('DeletionDate', '')
        path = root.base / path
    elif root.topdir is not None and not path.is_relative_to(root.topdir):
        return '', values.get('DeletionDate', '')
    return str(path), values.get('DeletionDate', '')


def entries(home_root, mounts=None):
    for root in roots(home_root, mounts):
        try:
            stored = list(root.files.iterdir())
        except OSError:
            continue
        for path in stored:
            original, deleted = read_info(path, root)
            yield path, original, deleted
