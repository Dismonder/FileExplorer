"""Desktop entry and FileManager1 integration for the local KDE session."""
import os
import subprocess
import json
import sys
from pathlib import Path

from PySide6.QtCore import ClassInfo, QObject, QTimer, QUrl, Slot, QThreadPool
from PySide6.QtWidgets import QApplication
from PySide6.QtDBus import QDBus, QDBusArgument, QDBusConnection, QDBusConnectionInterface, QDBusMessage

SERVICE = 'org.damian.FileExplorer'
INTERFACE = 'org.freedesktop.FileManager1'
OBJECT = '/org/freedesktop/FileManager1'
CONTROL_OBJECT = '/org/damian/FileExplorer'
VIRTUAL_LOCATIONS = frozenset({'home', 'computer', 'gallery', 'trash'})
REMOTE_SCHEMES = frozenset({'remote', 'smb', 'sftp', 'ftp', 'mtp', 'recentlyused', 'admin'})


def local_path(value):
    """Decode file URLs, preserving literal percent signs in filesystem paths."""
    value = str(value)
    if value.startswith('file:'):
        url = QUrl(value)
        if not url.isLocalFile() or url.host() not in ('', 'localhost'):
            raise ValueError('Ten adres nie wskazuje lokalnego folderu.')
        if url.hasQuery() or url.hasFragment():
            raise ValueError('Adres pliku zawiera zapytanie lub fragment. Zakoduj znaki ? i # w adresie.')
        value = url.path() if url.host() == 'localhost' else url.toLocalFile()
    elif '://' in value:
        raise ValueError(f'Nieobsługiwany adres: {value}')
    return os.path.abspath(os.path.expanduser(value))


def mount_remote(url):
    """Mount a remote URL using KIOFuse via D-Bus."""
    url = str(url)
    bus = QDBusConnection.sessionBus()
    msg = QDBusMessage.createMethodCall('org.kde.KIOFuse', '/org/kde/KIOFuse', 'org.kde.KIOFuse.VFS', 'mountUrl')
    msg.setArguments([url])
    reply = bus.call(msg)
    if reply.type() == QDBusMessage.ReplyMessage and reply.arguments():
        path = str(reply.arguments()[0])
        if os.path.isabs(path) and os.path.lexists(path):
            return path
        raise ValueError(f'Usługa KIOFuse nie zwróciła zamontowanego folderu: {path}')
    raise ValueError(f'Nie można zamontować adresu: {reply.errorMessage()}')


def desktop_exec(command):
    """Quote literal command arguments using Desktop Entry string and Exec rules."""
    parts = []
    for argument in command:
        value = str(argument).replace('%', '%%')
        value = ''.join('\\' + ch if ch in '\\"`$' else ch for ch in value)
        value = value.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        parts.append('"' + value + '"')
    return ' '.join(parts)


def normalize_location(value):
    value = str(value)
    if value in VIRTUAL_LOCATIONS:
        return value
    scheme = QUrl(value).scheme()
    if scheme == 'trash':
        return 'trash'
    if scheme in REMOTE_SCHEMES:
        return mount_remote(value)
    return local_path(value)


@ClassInfo(**{'D-Bus Interface': SERVICE})
class ExplorerControl(QObject):
    """Private diagnostics and safe shutdown for upgrades; never interrupts a file job."""
    def __init__(self, service):
        super().__init__(service)
        self.service = service

    @Slot(result=str)
    def GetState(self):
        windows = [w for w in self.service.windows if w.isVisible()]
        return json.dumps({'pid': os.getpid(), 'busy': any(w.busy for w in windows),
                           'jobs': QThreadPool.globalInstance().activeThreadCount(),
                           'windows': [[w.pages.widget(i).location for i in range(w.pages.count())]
                                       for w in windows],
                           'selections': [w.page.selected_paths() for w in windows]})

    @Slot(result=bool)
    def QuitIfIdle(self):
        if any(w.busy for w in self.service.windows) or QThreadPool.globalInstance().activeThreadCount():
            return False
        app = QApplication.instance()
        if app.activeModalWidget() is not None:
            return False
        for window in self.service.windows:
            if getattr(window, 'share_server', None) is not None:
                return False
            terminal = getattr(window, 'terminal', None)
            if terminal is not None and terminal.process.state().value != 0:
                return False
        for window in list(self.service.windows):
            if not window.close():
                return False
        QTimer.singleShot(0, app.quit)
        return True


@ClassInfo(**{'D-Bus Interface': INTERFACE})
class FileManagerService(QObject):
    def __init__(self, window_class, parent=None, restore_session=False, job_factory=None):
        super().__init__(parent)
        self.window_class = window_class
        self.restore_session = restore_session
        self.windows = []
        self.control = ExplorerControl(self)
        self.job_factory = job_factory

    def show(self, locations, select=False, properties=False, startup_id=''):
        remote = [(i, str(value)) for i, value in enumerate(locations)
                  if QUrl(str(value)).scheme() in REMOTE_SCHEMES]
        if remote:
            import network
            factory = self.job_factory
            if factory is None:
                from main import FileJob
                factory = FileJob
            resolved = list(locations)
            pending = len(remote)
            failures = []

            def completed(index, path=None, error=None):
                nonlocal pending
                resolved[index] = path
                if error:
                    failures.append(str(error))
                pending -= 1
                if pending:
                    return
                window = self.show([path for path in resolved if path is not None] or ['home'],
                                   select=select, properties=properties, startup_id=startup_id)
                if failures and window:
                    QTimer.singleShot(0, window, lambda w=window: w.error('\n'.join(failures)))

            for index, uri in remote:
                try:
                    network.mount_async(uri, lambda path, i=index: completed(i, path=path),
                                        lambda error, i=index: completed(i, error=error),
                                        job_factory=factory, owner=self, allow_file=True)
                except (ValueError, OSError) as error:
                    completed(index, error=error)
            return None
        window = next((w for w in self.windows if w.isVisible()), None)
        used_pages = {}
        errors = []
        restore = self.restore_session and not locations and window is None
        if window is not None and not startup_id and not window.isActiveWindow():
            # KWin requires a caller's activation token to raise an existing Wayland window.
            if QApplication.platformName() == 'wayland':
                window = None
        for value in locations or ['home']:
            try:
                path = normalize_location(value)
                virtual = path in VIRTUAL_LOCATIONS
                target = Path(path)
                if not virtual and not os.path.lexists(target):
                    raise ValueError(f'Element nie istnieje: {path}')
                selected = not virtual and (select or properties or not target.is_dir())
                directory = str(target.parent) if selected else path
                if not virtual and not os.access(directory, os.R_OK | os.X_OK):
                    raise ValueError(f'Brak uprawnień do folderu: {directory}')
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
                continue
            if window is None:
                window = self.window_class(directory, restore_session=restore)
                self.windows.append(window)
                window.destroyed.connect(lambda obj=None, w=window: self.windows.remove(w) if w in self.windows else None)
                window.show()
                if restore:
                    break
            page = used_pages.get(directory)
            if page is None:
                container = next((window.pages.widget(i) for i in range(window.pages.count())
                                  if window.pages.widget(i).location == directory), None)
                if container is None:
                    window.add_tab(directory)
                    container = window.pages.currentWidget()
                window.tabs.setCurrentIndex(window.pages.indexOf(container))
                page = container.active_page
                page.set_search('')
                page.tree.clearSelection()
                used_pages[directory] = page
            if selected:
                callback = None
                if properties:
                    def callback(w=window, requested=path):
                        w.properties(paths=[requested])
                window.reveal_file(path, page, after=callback)
        if errors:
            print('FileExplorer: ' + '\n'.join(errors), file=sys.stderr)
            if window is None:
                window = self.window_class('home', restore_session=False)
                self.windows.append(window)
                window.destroyed.connect(lambda obj=None, w=window: self.windows.remove(w) if w in self.windows else None)
                window.show()
            QTimer.singleShot(0, window, lambda w=window, text='\n'.join(errors): w.error(text))
        if window:
            if window.isMinimized():
                window.showNormal()
            previous_token = os.environ.get('XDG_ACTIVATION_TOKEN')
            if startup_id:
                os.environ['XDG_ACTIVATION_TOKEN'] = startup_id
            window.raise_()
            window.activateWindow()
            if startup_id:
                if previous_token is None:
                    os.environ.pop('XDG_ACTIVATION_TOKEN', None)
                else:
                    os.environ['XDG_ACTIVATION_TOKEN'] = previous_token
            QApplication.alert(window)
        return window

    @Slot('QStringList', str)
    def ShowFolders(self, uris, startup_id):
        self.show(uris, startup_id=startup_id)

    @Slot('QStringList', str)
    def ShowItems(self, uris, startup_id):
        self.show(uris, select=True, startup_id=startup_id)

    @Slot('QStringList', str)
    def ShowItemProperties(self, uris, startup_id):
        self.show(uris, select=True, properties=True, startup_id=startup_id)

    def register(self, desktop_service=False):
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected() or not bus.registerService(SERVICE):
            return False
        if not bus.registerObject(OBJECT, self, QDBusConnection.ExportAllSlots):
            bus.unregisterService(SERVICE)
            raise RuntimeError(bus.lastError().message())
        if not bus.registerObject(CONTROL_OBJECT, self.control, QDBusConnection.ExportAllSlots):
            bus.unregisterObject(OBJECT)
            bus.unregisterService(SERVICE)
            raise RuntimeError(bus.lastError().message())
        if desktop_service:
            # Dolphin does not allow replacement, so queue for the name: it becomes ours as soon as Dolphin quits.
            reply = bus.interface().registerService(
                INTERFACE, QDBusConnectionInterface.QueueService, QDBusConnectionInterface.AllowReplacement)
            if not reply.isValid() or reply.value() != QDBusConnectionInterface.ServiceRegistered.value:
                print('FileExplorer: FileManager1 is currently owned by another application (queued).', flush=True)
        return True


def forward_to_running(locations, select=False):
    bus = QDBusConnection.sessionBus()
    message = QDBusMessage.createMethodCall(SERVICE, OBJECT, INTERFACE, 'ShowItems' if select else 'ShowFolders')
    uris = QDBusArgument()
    uris << list(locations or ['home'])
    token = os.environ.get('XDG_ACTIVATION_TOKEN') or os.environ.get('DESKTOP_STARTUP_ID', '')
    message.setArguments([uris, token])
    reply = bus.call(message, QDBus.Block, 2000)
    return reply.type() != QDBusMessage.ErrorMessage


def is_default():
    try:
        result = subprocess.run(['xdg-mime', 'query', 'default', 'inode/directory'],
                                capture_output=True, text=True, timeout=3)
        return result.stdout.strip() == 'fileexplorer.desktop'
    except (OSError, subprocess.TimeoutExpired):
        return False
