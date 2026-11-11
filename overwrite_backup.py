"""Recoverable, same-filesystem versions for an explicit overwrite.

Payloads stay beside the target, in a private directory. A small state index
lets users recover the old version after a window closes or the app crashes.
No saved payload is automatically removed on exit.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


def registry_dir():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'FileExplorer/overwrite-backups'


def digest(version):
    return hashlib.sha256(repr(version).encode('utf-8', errors='surrogateescape')).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass
class Backup:
    directory: Path
    target: Path
    source: Path | None = None
    operation: str = 'copy'
    state: str = 'prepared'
    previous_version: str = ''
    current_version: str = ''
    warning: str = ''

    @property
    def previous(self):
        return self.directory / 'previous'

    @property
    def current(self):
        return self.directory / 'current'

    @property
    def index(self):
        return registry_dir() / f'{self.directory.name}.json'

    @classmethod
    def create(cls, target, source=None, operation='copy'):
        target = Path(target).absolute()
        directory = Path(tempfile.mkdtemp(prefix='.fileexplorer-undo-', dir=target.parent))
        backup = cls(directory, target, Path(source).absolute() if source is not None else None, operation)
        try:
            backup.save()
        except Exception:
            directory.rmdir()
            raise
        return backup

    def save(self):
        data = {key: str(value) if isinstance(value, Path) else value for key, value in vars(self).items()}
        atomic_json(self.directory / 'state.json', data)
        atomic_json(self.index, data)

    def capture(self, version):
        self.previous_version = digest(version(self.previous))
        self.current_version = digest(version(self.target))
        self.state = 'applied'
        self.save_completed()

    def save_completed(self):
        # The data transition has already succeeded. Keep its in-memory history
        # usable even if the filesystem becomes full while updating the journal.
        try:
            self.save()
            self.warning = ''
        except OSError as exc:
            self.warning = f'Wersja jest zachowana w {self.directory}, ale zapis stanu nie powiódł się: {exc}'

    def check(self, path, expected, version):
        if not expected or not os.path.lexists(path) or digest(version(path)) != expected:
            raise OSError(f'Nie można odtworzyć wersji: element został zmieniony: {path}')

    def undo(self, *, version, rename, move):
        if self.state != 'applied':
            raise OSError(f'Nieprawidłowy stan zachowanej wersji: {self.directory}')
        self.check(self.target, self.current_version, version)
        self.check(self.previous, self.previous_version, version)
        destination = self.source if self.operation == 'move' else self.current
        if destination is None or os.path.lexists(destination):
            raise FileExistsError(f'Element docelowy już istnieje: {destination}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        move(self.target, destination) if self.operation == 'move' else rename(self.target, destination)
        try:
            rename(self.previous, self.target)
        except Exception as exc:
            try:
                move(destination, self.target) if self.operation == 'move' else rename(destination, self.target)
                self.current_version = digest(version(self.target))
                self.save()
            except Exception as rollback:
                raise OSError(f'Nie udało się dokończyć cofania. Wersje pozostają w {destination} '
                              f'oraz {self.previous}: {rollback}') from exc
            raise
        self.previous_version = digest(version(self.target))
        self.current_version = digest(version(destination))
        self.state = 'undone'
        self.save_completed()

    def redo(self, *, version, rename, move):
        if self.state != 'undone':
            raise OSError(f'Nieprawidłowy stan zachowanej wersji: {self.directory}')
        self.check(self.target, self.previous_version, version)
        source = self.source if self.operation == 'move' else self.current
        self.check(source, self.current_version, version)
        if os.path.lexists(self.previous):
            raise FileExistsError(f'Zachowana wersja już istnieje: {self.previous}')
        rename(self.target, self.previous)
        try:
            move(source, self.target) if self.operation == 'move' else rename(source, self.target)
        except Exception as exc:
            try:
                rename(self.previous, self.target)
                self.previous_version = digest(version(self.target))
                self.save()
            except Exception as rollback:
                raise OSError(f'Nie udało się ponowić nadpisania. Poprzednia wersja pozostaje '
                              f'w {self.previous}: {rollback}') from exc
            raise
        self.capture(version)

    def discard_empty(self):
        """Clean a failed preparation only when it holds no file version."""
        if os.path.lexists(self.previous) or os.path.lexists(self.current):
            return
        (self.directory / 'state.json').unlink(missing_ok=True)
        self.directory.rmdir()
        self.index.unlink(missing_ok=True)


def list_backups():
    result = []
    for index in registry_dir().glob('.fileexplorer-undo-*.json'):
        try:
            metadata = index.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_size > 65536:
                continue
            data = json.loads(index.read_text())
            directory = Path(data['directory'])
            target = Path(data['target'])
            info = directory.lstat()
            if (not directory.is_absolute() or not target.is_absolute() or directory.parent != target.parent
                    or directory.name != index.stem or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                continue
            local_state = directory / 'state.json'
            try:
                local_info = local_state.lstat()
                if (stat.S_ISREG(local_info.st_mode) and local_info.st_uid == os.getuid()
                        and local_info.st_size <= 65536):
                    latest = json.loads(local_state.read_text())
                    if latest.get('directory') == str(directory) and latest.get('target') == str(target):
                        data = latest
            except (OSError, ValueError):
                pass
            source = Path(data['source']) if data.get('source') else None
            backup = Backup(directory, target, source, data.get('operation', 'copy'),
                            data.get('state', 'prepared'), data.get('previous_version', ''), data.get('current_version', ''))
            if os.path.lexists(backup.previous) or os.path.lexists(backup.current):
                result.append(backup)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result
