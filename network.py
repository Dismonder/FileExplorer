"""Remote locations backed by KIOFuse, with mounts performed outside the GUI thread."""
import os
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, QUrl
from shiboken6 import isValid

from linux_integration import mount_remote

REMOTE_SCHEMES = frozenset({'smb', 'sftp', 'ftp', 'mtp', 'remote', 'recentlyused', 'admin'})
_mounts = {}
_jobs = set()


def validate_remote_url(value):
    """Return a canonical URL; never interpret an unsupported protocol as a local path."""
    value = str(value).strip()
    if not value or any(ord(character) < 32 for character in value):
        raise ValueError('Podaj poprawny adres serwera.')
    url = QUrl(value.replace(' ', '%20'), QUrl.StrictMode)
    if not url.isValid() or url.scheme() not in REMOTE_SCHEMES:
        raise ValueError('Obsługiwane adresy: smb://, sftp://, ftp://, mtp:/ oraz remote:/.')
    if url.scheme() in ('smb', 'sftp', 'ftp') and not url.host():
        raise ValueError('Adres musi zawierać nazwę lub adres IP serwera.')
    if url.scheme() == 'admin' and (url.host() or url.userInfo() or url.port() != -1 or not url.path().startswith('/')):
        raise ValueError('Tryb administratora wymaga lokalnego adresu admin:///ścieżka.')
    if url.hasQuery() or url.hasFragment():
        raise ValueError('Adres zawiera zapytanie lub fragment. Zakoduj znaki ? i # w nazwie folderu.')
    if not url.path():
        url.setPath('/')
    return url.toString(QUrl.FullyEncoded)


def administrator_location(path):
    """Translate a local path to KIO admin without running the GUI as root."""
    remote = uri_for_path(path)
    if remote:
        if QUrl(remote).scheme() == 'admin':
            return remote
        raise ValueError('Tryb administratora jest dostępny dla lokalnych folderów.')
    url = QUrl.fromLocalFile(os.path.abspath(os.fspath(path)))
    url.setScheme('admin')
    return validate_remote_url(url.toString(QUrl.FullyEncoded))


def _public_url(uri):
    url = QUrl(uri)
    return url.toString(QUrl.FullyEncoded | QUrl.RemovePassword)


def remember_mount(uri, path, is_file=False):
    uri = _public_url(validate_remote_url(uri))
    path = os.path.abspath(os.fspath(path))
    _mounts[uri] = path
    if is_file:
        parent_url = QUrl(uri)
        parent_url.setPath(str(Path(parent_url.path()).parent))
        _mounts[parent_url.toString(QUrl.FullyEncoded)] = str(Path(path).parent)


def _mount_for_path(path):
    path = Path(os.path.abspath(os.fspath(path)))
    matches = [(uri, Path(root)) for uri, root in _mounts.items() if path.is_relative_to(root)]
    return max(matches, key=lambda item: len(item[1].parts)) if matches else None


def uri_for_path(path):
    """Translate a mounted folder or child back to its original URI, preserving encoded filenames."""
    if not path:
        return None
    mounted = _mount_for_path(path)
    if not mounted:
        return None
    uri, root = mounted
    relative = Path(os.path.abspath(os.fspath(path))).relative_to(root)
    if relative == Path('.'):
        return uri
    url = QUrl(uri)
    url.setPath(url.path().rstrip('/') + '/' + relative.as_posix())
    return url.toString(QUrl.FullyEncoded)


def display_location(path):
    if QUrl(str(path)).scheme() in REMOTE_SCHEMES:
        return _public_url(validate_remote_url(path))
    return uri_for_path(path) or str(path)


def remote_title(path):
    uri = uri_for_path(path)
    if not uri:
        return None
    url = QUrl(uri)
    return url.fileName() or url.host() or ('Sieć' if url.scheme() == 'remote' else 'Urządzenia')


def breadcrumbs(path):
    mounted = _mount_for_path(path)
    if not mounted:
        return None
    uri, root = mounted
    url = QUrl(uri)
    name = url.host() or ('Sieć' if url.scheme() == 'remote' else 'Urządzenia')
    if url.path().strip('/'):
        name += ' · ' + url.fileName()
    parts = [(name, str(root))]
    base = root
    for part in Path(os.path.abspath(os.fspath(path))).relative_to(root).parts:
        base /= part
        parts.append((part, str(base)))
    return parts


def parent_location(path):
    uri = uri_for_path(path)
    if not uri:
        return str(Path(path).parent)
    url = QUrl(uri)
    parent = str(Path(url.path().rstrip('/') or '/').parent)
    if url.path().rstrip('/'):
        url.setPath(parent)
        return url.toString(QUrl.FullyEncoded)
    return 'remote:/' if url.host() else 'computer'


def mount_async(uri, on_ready, on_error, *, job_factory, submit=None, owner=None, timeout_ms=30000, allow_file=False):
    """Return a FileJob and deliver one result on the GUI thread, with a bounded waiting state.

    job_factory is supplied by the caller to avoid importing the main program. A timed-out
    D-Bus operation may still finish in its worker; its late result never changes the UI.
    """
    uri = validate_remote_url(uri)
    key = _public_url(uri)
    holder = {'done': False}

    def available(path):
        if not path or not os.path.isabs(path):
            return False
        if os.path.isdir(path):
            return os.access(path, os.R_OK | os.X_OK)
        return allow_file and os.path.lexists(path) and os.access(os.path.dirname(path), os.R_OK | os.X_OK)

    def operation():
        cached = _mounts.get(key)
        if available(cached):
            path = cached
        else:
            path = mount_remote(uri)
        if not available(path):
            raise ValueError('Usługa KIOFuse nie zwróciła dostępnego folderu lub brak uprawnień do jego odczytu.'
                             if not allow_file else 'Usługa KIOFuse nie zwróciła dostępnego pliku lub folderu.')
        holder['path'] = path
        holder['is_file'] = not os.path.isdir(path)

    job = job_factory(operation)
    _jobs.add(job)
    timer = QTimer()
    timer.setSingleShot(True)

    def owner_alive():
        return not isinstance(owner, QObject) or isValid(owner)

    def timed_out():
        if holder['done']:
            return
        holder['done'] = True
        if owner_alive():
            on_error('Przekroczono czas łączenia z serwerem. Sprawdź adres i dostępność sieci.')

    def finished(error):
        _jobs.discard(job)
        if hasattr(owner, 'background_jobs'):
            owner.background_jobs.discard(job)
        timer.stop()
        timer.deleteLater()
        if holder['done']:
            return
        holder['done'] = True
        if not owner_alive():
            return
        if error:
            on_error(error)
        else:
            remember_mount(key, holder['path'], is_file=holder['is_file'])
            on_ready(holder['path'])

    job.signals.finished.connect(finished)
    timer.timeout.connect(timed_out)
    timer.start(max(1, timeout_ms))
    try:
        (submit or QThreadPool.globalInstance().start)(job)
    except Exception:
        timer.stop()
        timer.deleteLater()
        _jobs.discard(job)
        raise
    return job
