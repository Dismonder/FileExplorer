#!/usr/bin/env python3
"""A Windows 11 styled, local Qt file explorer."""
import argparse
import ctypes
import errno
import grp
import json
import os
import pwd
import fnmatch
from collections import OrderedDict, deque
import re
import shlex
import shutil
import stat as stat_module
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from string import Template
from urllib.parse import quote, unquote

from PySide6.QtCore import (
    QCollator, QDir, QFileInfo, QItemSelectionModel, QLibraryInfo, QLocale, QMimeData, QMimeDatabase, QObject,
    QTranslator,
    QPoint, QRect, QRunnable, QSettings, QSize, QSortFilterProxyModel, QStandardPaths,
    QStorageInfo, Qt, QThreadPool, QTimer, QUrl, Signal, QProcess, QEvent,
)
from PySide6.QtGui import (
    QActionGroup, QDesktopServices, QFont, QFontDatabase, QIcon, QImageReader, QKeySequence,
    QColor, QCursor, QDrag, QPalette, QPixmap, QShortcut, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileIconProvider,
    QFileSystemModel, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QListView, QMainWindow, QMenu, QMessageBox, QProgressBar, QSlider,
    QPushButton, QScrollArea, QSizeGrip, QSizePolicy, QSplitter, QStackedWidget, QStyle, QStyleOptionViewItem, QStyledItemDelegate,
    QSystemTrayIcon, QTabBar, QTabWidget, QToolButton, QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QWidgetAction,
    QPlainTextEdit, QButtonGroup,
)

from icons import icon
import devices
import service_menus
import search_features
from linux_integration import FileManagerService, forward_to_running, is_default, mount_remote, local_path, desktop_exec
import share
import network
import trash_backend
import overwrite_backup
from settings_panel import SettingsDialog
from preview_panel import PreviewPanel, load_preview_async
from terminal_panel import TerminalWidget, TerminalView
from settings_theme import THEMES, apply_palette

ROOT = Path(__file__).resolve().parent
HOME = Path.home()
START_TIME = time.perf_counter()
TIMING = bool(os.environ.get('FILEEXPLORER_TIMING'))


def mark(stage):
    """Print a startup milestone when FILEEXPLORER_TIMING=1 is set."""
    if TIMING:
        print(f'[{time.perf_counter() - START_TIME:7.3f}s] {stage}', file=sys.stderr, flush=True)
SPECIAL_PATHS = [
    ('Pulpit', 'desktop', QStandardPaths.DesktopLocation),
    ('Pobrane', 'downloads', QStandardPaths.DownloadLocation),
    ('Dokumenty', 'documents', QStandardPaths.DocumentsLocation),
    ('Obrazy', 'pictures', QStandardPaths.PicturesLocation),
    ('Muzyka', 'music', QStandardPaths.MusicLocation),
    ('Wideo', 'videos', QStandardPaths.MoviesLocation),
]
FOLDERS = [(name, kind, Path(QStandardPaths.writableLocation(location)))
           for name, kind, location in SPECIAL_PATHS]
FOLDER_ICONS = {str(path): kind for _, kind, path in FOLDERS}
VIRTUAL = {'home': ('Główna', 'home'), 'computer': ('Ten komputer', 'pc'),
           'gallery': ('Galeria', 'gallery'), 'trash': ('Kosz', 'trash')}
TRASH_ROOT = Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)) / 'Trash'
IMAGE_SUFFIXES = frozenset({'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'svg', 'tif', 'tiff', 'avif', 'heic', 'ico'})
ARCHIVE_SUFFIXES = frozenset({'.zip', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.txz', '.tbz2', '.7z', '.rar'})
TEXT_SUFFIXES = frozenset({'.txt', '.md', '.py', '.json', '.html', '.css', '.js', '.csv', '.sh', '.ini', '.cfg',
                           '.toml', '.yaml', '.yml', '.xml', '.log', '.qss', '.desktop', '.conf', '.rs', '.c', '.h', '.cpp'})
REMOTE_SCHEMES = ('remote', 'smb', 'sftp', 'ftp', 'mtp', 'recentlyused', 'admin')
DATE_FORMAT = '%d.%m.%Y %H:%M'
MIME_DB = QMimeDatabase()


def is_image(path):
    return Path(path).suffix.lower().lstrip('.') in IMAGE_SUFFIXES


def alive(obj):
    """True while the Qt object behind a Python wrapper still exists."""
    try:
        from shiboken6 import isValid
        return isValid(obj)
    except ImportError:
        return True


def unique_path(directory, name, suffix_text='kopia', reserved=()):
    """Return a path inside `directory` that does not exist yet, Windows style: 'name — kopia (2).ext'."""
    directory = Path(directory)
    candidate = directory / name
    source = Path(name)
    suffix = source.suffix if source.suffix and not name.startswith('.') else ''
    stem = name[:-len(suffix)] if suffix else name
    number = 1
    while os.path.lexists(candidate) or candidate in reserved:
        candidate = directory / f'{stem} — {suffix_text}{f" ({number})" if number > 1 else ""}{suffix}'
        number += 1
    return candidate


def numbered_path(directory, name):
    """'Nowy folder', 'Nowy folder (2)', … — the way Windows names new items."""
    directory = Path(directory)
    source = Path(name)
    suffix = source.suffix if source.suffix and not name.startswith('.') else ''
    stem = name[:-len(suffix)] if suffix else name
    candidate = directory / name
    number = 2
    while os.path.lexists(candidate):
        candidate = directory / f'{stem} ({number}){suffix}'
        number += 1
    return candidate


def rename_noreplace(source, target):
    """Atomic rename that fails instead of overwriting an existing target."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (OSError, AttributeError):
        if os.path.lexists(target):
            raise FileExistsError(17, 'Element o tej nazwie już istnieje', str(target))
        os.rename(source, target)
        return
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def format_date(timestamp):
    try:
        return datetime.fromtimestamp(timestamp).strftime(DATE_FORMAT)
    except (OverflowError, OSError, ValueError):
        return 'Nieznana'


def mounted_volumes():
    return [v for v in QStorageInfo.mountedVolumes()
            if v.isValid() and v.isReady() and v.bytesTotal() > 0
            and not bytes(v.device()).startswith((b'/dev/loop', b'/dev/zram'))
            and (v.rootPath() in ('/', '/home') or v.rootPath().startswith(('/mnt/', '/media/', '/run/media/')))]


def volume_name(volume):
    path = volume.rootPath()
    return {'/': 'CachyOS', '/home': 'Katalogi użytkowników', '/mnt/windows': 'Windows'}.get(
        path, volume.name() or Path(path).name)

def apply_theme(name):
    app = QApplication.instance()
    apply_palette(app, name, ROOT / 'style.qss')
    for widget in app.topLevelWidgets():
        if isinstance(widget, Explorer):
            widget.sync()


def size_text(size):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'.replace('.', ',')
        size /= 1024


def clipboard_has_files():
    mime = QApplication.clipboard().mimeData()
    return mime is not None and mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())


def title_for(location):
    return VIRTUAL[location][0] if location in VIRTUAL else network.remote_title(location) or Path(location).name or 'System plików'


def icon_for(location):
    if location in VIRTUAL:
        return icon(VIRTUAL[location][1])
    if network.uri_for_path(location):
        return icon('network')
    return icon(FOLDER_ICONS.get(str(location), 'folder'))


def label(text, name='', word_wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setObjectName(name)
    widget.setWordWrap(word_wrap)
    return widget


def box(parent=None, vertical=False, margins=(0, 0, 0, 0), spacing=0):
    layout = QVBoxLayout(parent) if vertical else QHBoxLayout(parent)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return layout


def tool(name, tip, callback=None, text='', arrow=False):
    button = QToolButton()
    button.setIcon(icon(name))
    button.setIconSize(QSize(18, 18))
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    button.setCursor(Qt.ArrowCursor)
    button.setFixedHeight(34)
    if text:
        button.setText(text + ('  ⌄' if arrow else ''))
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    else:
        button.setFixedWidth(36)
    if callback:
        button.clicked.connect(callback)
    return button


class Icons(QFileIconProvider):
    """Icon provider with a per-suffix cache, so QMimeDatabase and the theme are hit once per file type."""

    def __init__(self):
        super().__init__()
        self.cache = {}

    def icon(self, info):
        if not isinstance(info, QFileInfo):
            return icon('folder' if info == QFileIconProvider.Folder else 'file')
        if info.isDir():
            return icon_for(info.absoluteFilePath())
        return self.file_icon(info.suffix().lower(), info.absoluteFilePath())

    def file_icon(self, suffix, path):
        if suffix in IMAGE_SUFFIXES:
            return icon('pictures')
        result = self.cache.get(suffix)
        if result is None:
            mime = MIME_DB.mimeTypeForFile(path, QMimeDatabase.MatchExtension)
            result = QIcon.fromTheme(mime.iconName(), icon('file'))
            if len(self.cache) > 512:
                self.cache.clear()
            self.cache[suffix] = result
        return result

    def for_path(self, path, is_dir):
        """Icon for a path whose type is already known, without touching the disk."""
        if is_dir:
            return icon_for(path)
        name = os.path.basename(path)
        return self.file_icon(name.rpartition('.')[2].lower() if '.' in name[1:] else '', path)


class FileModel(QFileSystemModel):
    HEADERS = ['Nazwa', 'Rozmiar', 'Typ', 'Data modyfikacji']

    def removeRows(self, row, count, parent=None):
        # QAbstractItemView calls this after a drag that ended in MoveAction; QFileSystemModel would
        # delete the files, racing with the receiver's own move. Transfers are done by TransferJob only.
        return False


class NameDelegate(QStyledItemDelegate):
    """Inline rename like Windows: the editor preselects the name without its extension."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page

    def setEditorData(self, editor, index):
        super().setEditorData(editor, index)
        if isinstance(editor, QLineEdit):
            name = editor.text()
            source = self.page.proxy.mapToSource(index)
            stem = name if self.page.window.model.isDir(source) or name.startswith('.') else Path(name).stem
            editor.setSelection(0, len(stem))
            QTimer.singleShot(0, lambda: alive(editor) and editor.setSelection(0, len(stem)))

    def setModelData(self, editor, model, index):
        if not isinstance(editor, QLineEdit):
            super().setModelData(editor, model, index)
            return
        name = editor.text().strip()
        old = Path(self.page.window.model.filePath(self.page.proxy.mapToSource(index)))
        if not name or name == old.name:
            return
        if name in ('.', '..') or '/' in name or '\x00' in name:
            self.page.window.error('Podaj prawidłową nazwę bez znaku /.')
            return
        if os.path.lexists(old.with_name(name)):
            self.page.window.error(f'Element „{name}” już istnieje w tym folderze.')
            return
        super().setModelData(editor, model, index)

    def updateEditorGeometry(self, editor, option, index):
        # Qt sizes the editor to the painted text rectangle, which in icon views is a clipped sliver
        # under the thumbnail; give it the full cell width and a real line-edit height instead.
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        height = max(editor.sizeHint().height(), text_rect.height())
        rect = option.rect
        if isinstance(opt.widget, QListView) and opt.widget.viewMode() == QListView.IconMode:
            editor.setGeometry(QRect(rect.left() + 2, text_rect.top() - 2, rect.width() - 4, height))
        else:
            editor.setGeometry(QRect(text_rect.left() - 2, text_rect.center().y() - height // 2, rect.right() - text_rect.left() + 1, height))

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole and section < len(self.HEADERS):
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            column = index.column()
            if column == 2:
                if self.isDir(index):
                    return 'Folder plików'
                name = self.fileName(index)
                suffix = name.rpartition('.')[2] if '.' in name[1:] else ''
                return 'Plik ' + suffix.upper() if suffix else 'Plik'
            if column == 1:
                return '' if self.isDir(index) else size_text(self.size(index))
            if column == 3:
                return self.lastModified(index).toString('dd.MM.yyyy HH:mm')
        return super().data(index, role)


THUMBNAILS = OrderedDict()  # (path, mtime) -> QIcon, shared by every tab and pane
THUMBNAIL_LIMIT = 192
THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT = 256, 192


class ThumbnailSignals(QObject):
    ready = Signal(object, object)


class ThumbnailJob(QRunnable):
    """Decode a scaled image off the GUI thread; result is delivered through a queued signal."""

    def __init__(self, key, path, signals):
        super().__init__()
        self.key, self.path, self.signals = key, path, signals
        self.setAutoDelete(False)

    def run(self):
        reader = QImageReader(self.path)
        reader.setAutoTransform(True)
        dimensions = reader.size()
        if dimensions.isValid() and (dimensions.width() > THUMBNAIL_WIDTH or dimensions.height() > THUMBNAIL_HEIGHT):
            reader.setScaledSize(dimensions.scaled(THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT, Qt.KeepAspectRatio))
        image = reader.read()
        self.signals.ready.emit(self.key, None if image.isNull() else image)


class SearchSignals(QObject):
    found = Signal(list)
    done = Signal(int, bool)


class SearchJob(QRunnable):
    """Stream local and optional indexed search results from a worker thread."""
    LIMIT = 20000
    SKIPPED_ROOTS = frozenset({'/proc', '/sys', '/dev', '/run'})

    def __init__(self, root, query, show_hidden, mode='name'):
        super().__init__()
        self.setAutoDelete(False)
        self.root = root
        self.show_hidden = show_hidden
        self.signals = SearchSignals()
        self.cancelled = False
        self.note = ''
        self.engine = search_features.SearchEngine(root, query, mode, show_hidden,
                                                   cancelled=lambda: self.cancelled)

    def matches(self, name):
        return self.engine.name_matches(name)

    def run(self):
        batch, count, truncated = [], 0, False
        last = time.monotonic()
        try:
            for result in self.engine.results(self.LIMIT):
                if self.cancelled:
                    break
                batch.append(result)
                count += 1
                if count >= self.LIMIT:
                    truncated = True
                    break
                now = time.monotonic()
                if batch and (len(batch) >= 500 or now - last > 0.15):
                    self.signals.found.emit(batch)
                    batch, last = [], now
            self.note = self.engine.note
        except Exception as exc:
            self.note = f'Nie udało się przeszukać wszystkich elementów: {exc}'
        if batch:
            self.signals.found.emit(batch)
        self.signals.done.emit(count, truncated)


class FileFilter(QSortFilterProxyModel):
    PENDING_LIMIT = 48  # decodes in flight per view; the rest is requested again after a repaint

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(0)
        self.setDynamicSortFilter(True)
        self.root_path = ''
        self.revealed_paths = set()
        self.query = ''
        self.query_folded = ''
        self.patterns = []  # compiled name filters such as *.jpg; folders are always shown
        self.images_only = False
        self.show_thumbnails = False
        self.thumbnails = THUMBNAILS
        self.pending = {}  # key -> running ThumbnailJob (kept alive until its result arrives)
        self.overflow = False
        self.collator = QCollator()
        self.collator.setNumericMode(True)
        self.collator.setCaseSensitivity(Qt.CaseInsensitive)
        self.thumbnail_signals = ThumbnailSignals()
        self.thumbnail_signals.ready.connect(self.thumbnail_ready)

    def set_query(self, text):
        self.beginFilterChange()
        self.query = text
        self.query_folded = text.casefold()
        self.endFilterChange()

    def set_root(self, path, images_only):
        self.beginFilterChange()
        self.root_path = path
        self.revealed_paths.clear()
        self.images_only = images_only
        self.query = self.query_folded = ''
        self.endFilterChange()

    def set_name_filters(self, text):
        patterns = []
        for pattern in text.split():
            if '*' not in pattern and '?' not in pattern and '[' not in pattern:
                pattern = f'*{pattern}*'
            patterns.append(re.compile(fnmatch.translate(pattern), re.IGNORECASE))
        self.beginFilterChange()
        self.patterns = patterns
        self.endFilterChange()

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DecorationRole and index.column() == 0 and (self.show_thumbnails or self.images_only):
            source = self.mapToSource(index)
            model = self.sourceModel()
            if not model.isDir(source) and is_image(model.fileName(source)):
                path = model.filePath(source)
                key = (path, model.lastModified(source).toMSecsSinceEpoch())
                cached = THUMBNAILS.get(key)
                if cached is not None:
                    THUMBNAILS.move_to_end(key)
                    return cached
                if key not in self.pending:
                    if len(self.pending) >= self.PENDING_LIMIT:
                        self.overflow = True
                    else:
                        job = self.pending[key] = ThumbnailJob(key, path, self.thumbnail_signals)
                        QThreadPool.globalInstance().start(job)
                return icon('pictures')
        return super().data(index, role)

    def thumbnail_ready(self, key, image):
        self.pending.pop(key, None)
        while len(THUMBNAILS) >= THUMBNAIL_LIMIT:
            THUMBNAILS.popitem(last=False)
        THUMBNAILS[key] = QIcon(QPixmap.fromImage(image)) if image is not None else icon('pictures')
        model = self.sourceModel()
        if self.overflow and len(self.pending) <= self.PENDING_LIMIT // 2:
            # Skipped requests get another chance: repaint the whole folder column (views only paint visible rows).
            self.overflow = False
            root = self.mapFromSource(model.index(self.root_path))
            rows = self.rowCount(root)
            if rows:
                self.dataChanged.emit(self.index(0, 0, root), self.index(rows - 1, 0, root), [Qt.DecorationRole])
            return
        source = model.index(key[0])
        if source.isValid():
            index = self.mapFromSource(source)
            if index.isValid():
                self.dataChanged.emit(index, index, [Qt.DecorationRole])

    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        index = model.index(row, 0, parent)
        path = model.filePath(index)
        # Keep every ancestor so mapFromSource(root) remains valid during search.
        if path == self.root_path or self.root_path.startswith(path.rstrip('/') + '/'):
            return True
        if path in self.revealed_paths:
            return True
        name = model.fileName(index)
        if name.startswith('.') and not getattr(model, 'show_hidden', False):
            return False
        if not self.query_folded and not self.images_only and not self.patterns:
            return True
        is_dir = model.isDir(index)
        if self.images_only and (is_dir or not is_image(name)):
            return False
        if self.patterns and not is_dir and not any(pattern.match(name) for pattern in self.patterns):
            return False
        return self.query_folded in name.casefold()

    def lessThan(self, left, right):
        model = self.sourceModel()
        ld, rd = model.isDir(left), model.isDir(right)
        if ld != rd:
            return ld if self.sortOrder() == Qt.AscendingOrder else not ld
        column = left.column()
        if column == 1:
            return model.size(left) < model.size(right)
        if column == 3:
            return model.lastModified(left) < model.lastModified(right)
        if column == 2:
            lt, rt = model.data(left, Qt.DisplayRole), model.data(right, Qt.DisplayRole)
            if lt != rt:
                return self.collator.compare(lt, rt) < 0
        return self.collator.compare(model.fileName(left), model.fileName(right)) < 0


VIEW_MODES = {  # in Windows order: Ctrl+Shift+1 … Ctrl+Shift+8
    'xlarge': ('Bardzo duże ikony', 'xlarge', 192),
    'large': ('Duże ikony', 'grid', 96),
    'medium': ('Średnie ikony', 'medium', 48),
    'small': ('Małe ikony', 'small', 16),
    'list': ('Lista', 'list', 16),
    'details': ('Szczegóły', 'view', 16),
    'tiles': ('Kafelki', 'tiles', 48),
    'content': ('Zawartość', 'content', 32),
}


class TileDelegate(NameDelegate):
    """Windows 'Kafelki': icon on the left, name with type and size on the right."""
    ICON_MARGIN = 6

    def draw_frame(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        icon, text = QIcon(opt.icon), str(opt.text)  # copies: the option's own members are cleared next
        opt.icon, opt.text = QIcon(), ''
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        return opt, icon, text

    def draw_icon(self, painter, opt, icon, rect):
        size = self.page.grid.iconSize().width()
        icon_rect = QRect(rect.left(), rect.top() + (rect.height() - size) // 2, size, size)
        icon.paint(painter, icon_rect, Qt.AlignCenter, QIcon.Normal if opt.state & QStyle.State_Enabled else QIcon.Disabled)
        return icon_rect

    def muted(self, opt):
        color = QColor(opt.palette.color(QPalette.Text))
        color.setAlphaF(0.65)
        return color

    def details(self, index):
        return [value for value in (index.siblingAtColumn(2).data() or '', index.siblingAtColumn(1).data() or '') if value]

    def paint(self, painter, option, index):
        opt, icon, text = self.draw_frame(painter, option, index)
        rect = option.rect.adjusted(self.ICON_MARGIN, 4, -self.ICON_MARGIN, -4)
        icon_rect = self.draw_icon(painter, opt, icon, rect)
        left = icon_rect.right() + 10
        width = rect.right() - left
        painter.save()
        metrics = painter.fontMetrics()
        line = metrics.height()
        top = rect.top() + max(0, (rect.height() - line * (1 + len(self.details(index)))) // 2)
        painter.setPen(opt.palette.color(QPalette.Text))
        painter.drawText(QRect(left, top, width, line), Qt.AlignLeft | Qt.AlignVCenter, metrics.elidedText(text, Qt.ElideMiddle, width))
        painter.setPen(self.muted(opt))
        for row, value in enumerate(self.details(index), 1):
            painter.drawText(QRect(left, top + row * line, width, line), Qt.AlignLeft | Qt.AlignVCenter, metrics.elidedText(value, Qt.ElideRight, width))
        painter.restore()

    def updateEditorGeometry(self, editor, option, index):
        left = option.rect.left() + self.ICON_MARGIN + self.page.grid.iconSize().width() + 10
        editor.setGeometry(QRect(left, option.rect.top() + 4, option.rect.right() - left - self.ICON_MARGIN, editor.sizeHint().height()))


class ContentDelegate(TileDelegate):
    """Windows 'Zawartość': full-width rows, name and type on the left, date and size on the right."""
    RIGHT_WIDTH = 190

    def sizeHint(self, option, index):
        return QSize(max(240, self.page.grid.viewport().width() - 2), 2 * option.fontMetrics.height() + 14)

    def paint(self, painter, option, index):
        opt, icon, text = self.draw_frame(painter, option, index)
        rect = option.rect.adjusted(self.ICON_MARGIN, 4, -self.ICON_MARGIN, -4)
        icon_rect = self.draw_icon(painter, opt, icon, rect)
        left = icon_rect.right() + 10
        right_width = self.RIGHT_WIDTH if rect.width() > 420 else 0
        width = rect.right() - left - right_width - 8
        painter.save()
        metrics = painter.fontMetrics()
        line = metrics.height()
        top = rect.top() + max(0, (rect.height() - 2 * line) // 2)
        painter.setPen(opt.palette.color(QPalette.Text))
        painter.drawText(QRect(left, top, width, line), Qt.AlignLeft | Qt.AlignVCenter, metrics.elidedText(text, Qt.ElideMiddle, width))
        painter.setPen(self.muted(opt))
        painter.drawText(QRect(left, top + line, width, line), Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(index.siblingAtColumn(2).data() or '', Qt.ElideRight, width))
        if right_width:
            column = QRect(rect.right() - right_width, top, right_width, line)
            painter.drawText(column, Qt.AlignRight | Qt.AlignVCenter, index.siblingAtColumn(3).data() or '')
            painter.drawText(column.translated(0, line), Qt.AlignRight | Qt.AlignVCenter, index.siblingAtColumn(1).data() or '')
        painter.restore()


class DropHint(QLabel):
    """Windows-style label following the cursor during a drag: 'Przenieś do Obrazy'."""
    TEXTS = {'move': 'Przenieś do', 'copy': 'Kopiuj do', 'link': 'Utwórz dowiązanie w', 'menu': 'Wybierz działanie w'}

    def __init__(self, window):
        super().__init__(window, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setObjectName('dropHint')
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def show_hint(self, action, directory, global_pos):
        name = Path(directory).name or directory
        self.setText(f'{self.TEXTS.get(action, self.TEXTS["copy"])} {name}')
        self.adjustSize()
        self.move(global_pos + QPoint(20, 24))
        if not self.isVisible():
            self.show()


class FilePathTree(QTreeWidget):
    """Virtual file lists export the same URLs as a filesystem view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSupportedDragActions(Qt.CopyAction)

    def mimeTypes(self):
        return ['text/uri-list']

    def mimeData(self, items):
        mime = QMimeData()
        paths = dict.fromkeys(item.data(0, Qt.UserRole) for item in items)
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths if path])
        return mime


class Page(QWidget):
    changed = Signal()
    selection_changed = Signal()

    def __init__(self, window, location='home'):
        super().__init__()
        self.window = window
        self.setObjectName('page')
        self.history = []
        self.history_index = -1
        self.location = ''
        self.navigation_generation = 0
        self.pending_remote = ''
        self.mode = 'details'
        self.search = ''
        self.search_mode = window.settings.value('search_mode', 'name')
        if self.search_mode not in search_features.MODES:
            self.search_mode = 'name'
        self.home_filter = 'recent'
        self.proxy = FileFilter(self)
        self.proxy.setSourceModel(window.model)
        layout = box(self, vertical=True)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.tree = QTreeView()
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.setModel(self.proxy)
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 180)
        self.tree.setColumnWidth(3, 180)
        self.tree.header().moveSection(3, 1)
        self.tree.header().moveSection(self.tree.header().visualIndex(2), 2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        header_state = window.settings.value('header_state')
        if header_state:
            self.tree.header().restoreState(header_state)
            self.tree.header().setSectionHidden(0, False)
        self.tree.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self.header_menu)
        self.grid = QListView()
        self.grid.setModel(self.proxy)
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(8)
        self.grid.setSelectionModel(self.tree.selectionModel())
        for view in (self.tree, self.grid):
            self.configure_view(view)
        self.tree.selectionModel().selectionChanged.connect(self.selection_changed)
        self.tree.header().sortIndicatorChanged.connect(lambda *_: self.grid.doItemsLayout())
        self.tree.header().sortIndicatorChanged.connect(lambda column, order: self.window.remember_sort(column, order))
        saved_column = window.settings.value('sort_column', 0, type=int)
        saved_order = Qt.DescendingOrder if window.settings.value('sort_order', 0, type=int) else Qt.AscendingOrder
        if 0 <= saved_column <= 3:
            self.tree.sortByColumn(saved_column, saved_order)
        self.delegates = {'name': NameDelegate(self), 'tiles': TileDelegate(self), 'content': ContentDelegate(self)}
        self.mode = window.settings.value('view_mode', 'details')
        if self.mode == 'icons':  # setting name from older versions
            self.mode = 'large'
        if self.mode not in VIEW_MODES:
            self.mode = 'details'
        self.configure_grid(self.mode)
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.grid)
        # Rarely used pages are built on first use: each widget added to the stack costs a stylesheet polish.
        self.gallery = self.gallery_grid = None
        self.home = self.home_body = self.home_layout = None
        self.computer = None
        self.trash = self.trash_tree = self.trash_summary = self.restore_trash_button = self.empty_trash_button = None
        self.results_page = self.results = self.search_summary = None
        self.search_job = None
        self.searching = False
        self.recent_tree = None
        self.type_filter = ''
        self.loaded = set()
        self.empty_label = label('', 'empty')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setParent(self)
        self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.empty_label.hide()
        window.model.directoryLoaded.connect(self.directory_loaded)
        for signal in (self.proxy.rowsInserted, self.proxy.rowsRemoved, self.proxy.modelReset, self.proxy.layoutChanged):
            signal.connect(lambda *_: self.update_empty())
        if QUrl(str(location)).scheme() in REMOTE_SCHEMES:
            self.navigate('home')
        if not self.navigate(location):
            self.navigate('home')

    def configure_view(self, view):
        view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        view.setEditTriggers(QAbstractItemView.SelectedClicked)  # click on an already selected name renames (Windows)
        view.setItemDelegateForColumn(0, NameDelegate(self))
        if isinstance(view, QListView):
            view.setSelectionRectVisible(True)  # rubber band selection in icon views
        view.setDragEnabled(True)
        view.setDragDropMode(QAbstractItemView.DragDrop)
        view.setAcceptDrops(True)
        view.setDefaultDropAction(Qt.CopyAction)
        view.viewport().setAcceptDrops(True)
        view.viewport().installEventFilter(self)
        view.setContextMenuPolicy(Qt.CustomContextMenu)
        view.customContextMenuRequested.connect(lambda pos, v=view: self.context(v, pos))
        view.doubleClicked.connect(self.open_index)

    def register_filters(self):
        parent = self.parent()
        if parent is not None and hasattr(parent, 'install_filters'):
            parent.install_filters(self)

    def ensure_gallery(self):
        if self.gallery is not None:
            return
        self.gallery_grid = QListView()
        self.gallery_grid.setModel(self.proxy)
        self.gallery_grid.setViewMode(QListView.IconMode)
        self.gallery_grid.setResizeMode(QListView.Adjust)
        self.gallery_grid.setMovement(QListView.Static)
        self.gallery_grid.setIconSize(QSize(180, 130))
        self.gallery_grid.setGridSize(QSize(200, 184))  # room for one label line under the 130 px thumbnail
        self.gallery_grid.setSpacing(6)
        self.gallery_grid.setSelectionModel(self.tree.selectionModel())
        self.configure_view(self.gallery_grid)
        self.gallery = QWidget()
        self.gallery.setObjectName('gallery')
        gallery_layout = box(self.gallery, vertical=True, margins=(24, 22, 24, 12), spacing=16)
        gallery_layout.addWidget(label('Galeria', 'detailsTitle'))
        gallery_layout.addWidget(label(f'Kolekcja: {FOLDERS[3][2]}', 'muted'))
        gallery_layout.addWidget(self.gallery_grid, 1)
        self.stack.addWidget(self.gallery)
        self.register_filters()

    def ensure_home(self):
        if self.home is not None:
            return
        self.home = QScrollArea()
        self.home.setWidgetResizable(True)
        self.home_body = QWidget()
        self.home_body.setObjectName('home')
        self.home_layout = box(self.home_body, vertical=True, margins=(30, 20, 30, 16), spacing=16)
        self.home.setWidget(self.home_body)
        self.stack.addWidget(self.home)

    def ensure_computer(self):
        if self.computer is not None:
            return
        self.computer = QScrollArea()
        self.computer.setWidgetResizable(True)
        self.stack.addWidget(self.computer)

    def ensure_trash(self):
        if self.trash is not None:
            return
        self.trash = QWidget()
        self.trash.setObjectName('trashPage')
        trash_layout = box(self.trash, vertical=True, margins=(28, 22, 28, 18), spacing=14)
        title_row = QWidget()
        title_layout = box(title_row, margins=(0, 0, 0, 0), spacing=10)
        picture = QLabel()
        picture.setPixmap(icon('trash').pixmap(28, 28))
        title_layout.addWidget(picture)
        title_layout.addWidget(label('Kosz', 'detailsTitle'))
        title_layout.addStretch()
        self.restore_trash_button = QPushButton(icon('restore'), 'Przywróć zaznaczone')
        self.restore_trash_button.clicked.connect(self.window.restore_from_trash)
        title_layout.addWidget(self.restore_trash_button)
        self.empty_trash_button = QPushButton(icon('trash'), 'Opróżnij Kosz')
        self.empty_trash_button.clicked.connect(self.window.empty_trash)
        title_layout.addWidget(self.empty_trash_button)
        trash_layout.addWidget(title_row)
        self.trash_summary = label('', 'muted')
        trash_layout.addWidget(self.trash_summary)
        self.trash_tree = QTreeWidget()
        self.trash_tree.setObjectName('trashTree')
        self.trash_tree.setHeaderLabels(['Nazwa', 'Oryginalna lokalizacja', 'Data usunięcia', 'Rozmiar', 'Typ'])
        self.trash_tree.setRootIsDecorated(False)
        self.trash_tree.setUniformRowHeights(True)
        self.trash_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.trash_tree.setAlternatingRowColors(False)
        self.trash_tree.setColumnWidth(0, 280)
        self.trash_tree.setColumnWidth(1, 420)
        self.trash_tree.setColumnWidth(2, 165)
        self.trash_tree.setColumnWidth(3, 105)
        self.trash_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.trash_tree.customContextMenuRequested.connect(
            lambda pos: self.window.trash_context_menu(self.trash_tree.viewport().mapToGlobal(pos)))
        self.trash_tree.itemDoubleClicked.connect(lambda item, column: self.window.open_trash_item(item))
        self.trash_tree.itemSelectionChanged.connect(self.selection_changed)
        self.trash_tree.itemSelectionChanged.connect(
            lambda: self.restore_trash_button.setEnabled(bool(self.trash_tree.selectedItems())))
        trash_layout.addWidget(self.trash_tree, 1)
        self.stack.addWidget(self.trash)
        self.register_filters()

    def ensure_results(self):
        if self.results is not None:
            return
        self.results_page = QWidget()
        self.results_page.setObjectName('resultsPage')
        results_layout = box(self.results_page, vertical=True, margins=(0, 0, 0, 0), spacing=0)
        self.search_summary = label('', 'searchSummary')
        self.search_summary.setContentsMargins(14, 8, 14, 8)
        results_layout.addWidget(self.search_summary)
        self.results = FilePathTree()
        self.results.setObjectName('searchResults')
        self.results.setHeaderLabels(['Nazwa', 'Folder', 'Data modyfikacji', 'Rozmiar', 'Typ'])
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results.setColumnWidth(0, 300)
        self.results.setColumnWidth(1, 320)
        self.results.setColumnWidth(2, 150)
        self.results.setColumnWidth(3, 90)
        self.results.setDragEnabled(True)
        self.results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results.customContextMenuRequested.connect(
            lambda pos: self.window.context_menu(self.results.viewport().mapToGlobal(pos)))
        self.results.itemDoubleClicked.connect(lambda item, column: self.open_path(item.data(0, Qt.UserRole)))
        self.results.itemSelectionChanged.connect(self.selection_changed)
        results_layout.addWidget(self.results, 1)
        self.stack.addWidget(self.results_page)
        self.register_filters()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.empty_label.setGeometry(self.stack.geometry())

    def directory_loaded(self, path):
        self.loaded.add(path)
        if path == self.current_dir():
            self.update_empty()

    def update_empty(self):
        directory = self.current_dir()
        if not directory or self.searching or directory not in self.loaded or self.proxy.rowCount(self.tree.rootIndex()):
            self.empty_label.hide()
            return
        if self.search or self.type_filter:
            self.empty_label.setText('Brak elementów pasujących do wyszukiwania.')
        else:
            self.empty_label.setText('Ten folder jest pusty.')
        self.empty_label.setGeometry(self.stack.geometry())
        self.empty_label.show()
        self.empty_label.raise_()

    def configure_grid(self, mode):
        """Lay the icon view out for one of the Windows view modes (everything except 'details')."""
        grid = self.grid
        size = VIEW_MODES[mode][2]
        self.icon_size = size
        self.proxy.show_thumbnails = size >= 48
        grid.setItemDelegateForColumn(0, self.delegates.get(mode, self.delegates['name']))
        grid.setIconSize(QSize(size, size))
        icons = mode in ('xlarge', 'large', 'medium', 'tiles')
        grid.setViewMode(QListView.IconMode if icons else QListView.ListMode)
        # QListView.setViewMode() re-derives dragEnabled/acceptDrops from Movement.Static, which turns both off.
        grid.setDragEnabled(True)
        grid.setAcceptDrops(True)
        grid.viewport().setAcceptDrops(True)
        grid.setFlow(QListView.TopToBottom if mode in ('list', 'content') else QListView.LeftToRight)
        grid.setWrapping(mode != 'content')
        grid.setWordWrap(mode in ('xlarge', 'large', 'medium'))
        grid.setSpacing({'xlarge': 12, 'large': 8, 'medium': 6, 'tiles': 4}.get(mode, 1))
        grid.setUniformItemSizes(mode in ('tiles', 'content'))  # thumbnails vary in height, so icon modes must not be uniform
        if mode == 'tiles':
            grid.setGridSize(QSize(270, size + 18))
        elif icons:
            grid.setGridSize(QSize(max(size + 44, int(size * 1.5)), size + 48))
        else:
            grid.setGridSize(QSize())
        grid.doItemsLayout()

    def directory_view(self):
        return self.tree if self.mode == 'details' else self.grid

    def header_menu(self, pos):
        header = self.tree.header()
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_DeleteOnClose)
        for column in range(1, self.window.model.columnCount()):
            action = menu.addAction(FileModel.HEADERS[column])
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(column))
            action.toggled.connect(lambda checked, c=column: header.setSectionHidden(c, not checked))
        menu.popup(header.mapToGlobal(pos))

    def set_type_filter(self, text):
        self.type_filter = text.strip()
        self.proxy.set_name_filters(self.type_filter)
        self.update_empty()

    def current_dir(self):
        if self.location == 'gallery':
            return str(FOLDERS[3][2])
        return self.location if self.location not in VIRTUAL else None

    def trash_entries(self):
        entries = []
        for stored, original, deleted in trash_backend.entries(TRASH_ROOT):
            try:
                deleted_at = datetime.fromisoformat(deleted)
                deleted_text, sort_date = deleted_at.strftime(DATE_FORMAT), deleted_at.timestamp()
            except ValueError:
                deleted_text, sort_date = 'Nieznana', 0
            try:
                info = stored.stat()
                size = '—' if stored.is_dir() else size_text(info.st_size)
            except OSError:
                size = '—'
            kind = 'Folder plików' if stored.is_dir() else ('Plik ' + stored.suffix[1:].upper() if stored.suffix else 'Plik')
            entries.append((sort_date, stored, original, deleted_text, size, kind))
        return sorted(entries, reverse=True, key=lambda entry: entry[0])

    def build_trash(self):
        self.ensure_trash()
        entries = self.trash_entries()
        self.trash_tree.clear()
        for _, stored, original, deleted, size, kind in entries:
            item = QTreeWidgetItem([stored.name, original or 'Nieznana lokalizacja', deleted, size, kind])
            item.setIcon(0, self.window.provider.icon(QFileInfo(str(stored))))
            item.setData(0, Qt.UserRole, str(stored))
            item.setData(0, Qt.UserRole + 1, original)
            item.setToolTip(0, str(stored))
            item.setToolTip(1, original)
            self.trash_tree.addTopLevelItem(item)
        count = len(entries)
        self.trash_summary.setText('Kosz jest pusty.' if not count else f'Elementy w Koszu: {count}')
        self.restore_trash_button.setEnabled(False)
        self.empty_trash_button.setEnabled(bool(count))

    def navigate(self, location, record=True, on_ready=None, _mounted=False):
        location = str(location)
        self.navigation_generation += 1
        generation = self.navigation_generation
        self.pending_remote = ''
        if not location:
            return False
        if QUrl(location).scheme() == 'trash':
            location = 'trash'
        elif not _mounted and QUrl(location).scheme() in REMOTE_SCHEMES:
            try:
                uri = network.validate_remote_url(location)
            except ValueError as exc:
                self.window.error(exc)
                return False
            self.pending_remote = network.display_location(uri)
            self.window.status_text.setText(f'Łączenie: {self.pending_remote}…')

            def mounted(path):
                if alive(self) and self.navigation_generation == generation:
                    self.navigate(path, record=record, on_ready=on_ready, _mounted=True)

            def failed(error):
                if alive(self) and self.navigation_generation == generation:
                    self.pending_remote = ''
                    self.window.error(error)
                    self.window.update_status()

            try:
                network.mount_async(uri, mounted, failed, job_factory=FileJob,
                                    submit=self.window.start_job, owner=self.window)
            except Exception as exc:
                failed(str(exc))
                return False
            return True
        if location == 'gallery' and not FOLDERS[3][2].is_dir():
            self.window.error(f'Folder kolekcji nie istnieje:\n{FOLDERS[3][2]}')
            return False
        if location not in VIRTUAL:
            try:
                location = local_path(location)
            except ValueError as exc:
                self.window.error(exc)
                return False
            if not _mounted and not os.path.isdir(location):
                self.window.error(f'Nie można otworzyć folderu:\n{location}')
                return False
            if not _mounted and not os.access(location, os.R_OK | os.X_OK):
                self.window.error(f'Brak uprawnień do folderu:\n{location}')
                return False
        if record:
            self.history = self.history[:self.history_index + 1]
            self.history.append(network.display_location(location))
            self.history_index += 1
        self.stop_search(show_view=False)
        self.location = location
        self.search = ''
        self.tree.clearSelection()
        if location == 'home':
            self.build_home()
            self.stack.setCurrentWidget(self.home)
        elif location == 'computer':
            self.build_computer()
            self.stack.setCurrentWidget(self.computer)
        elif location == 'trash':
            self.build_trash()
            self.stack.setCurrentWidget(self.trash)
        else:
            directory = self.current_dir()
            self.proxy.set_root(directory, location == 'gallery')
            root = self.proxy.mapFromSource(self.window.model.index(directory))
            self.tree.setRootIndex(root)
            self.grid.setRootIndex(root)
            if location == 'gallery':
                self.ensure_gallery()
            if self.gallery_grid is not None:
                self.gallery_grid.setRootIndex(root)
            self.stack.setCurrentWidget(self.gallery if location == 'gallery' else self.directory_view())
        self.update_empty()
        self.changed.emit()
        if on_ready is not None:
            on_ready(location)
        return True

    def travel(self, delta):
        index = self.history_index + delta
        if 0 <= index < len(self.history):
            def finished(_location):
                self.history_index = index
                self.changed.emit()
            self.navigate(self.history[index], record=False, on_ready=finished)

    def set_search(self, text):
        self.search = text
        query = text.casefold()
        if self.location == 'home' and self.recent_tree:
            for i in range(self.recent_tree.topLevelItemCount()):
                item = self.recent_tree.topLevelItem(i)
                item.setHidden(query not in item.text(0).casefold())
        elif self.location == 'trash':
            for i in range(self.trash_tree.topLevelItemCount()):
                item = self.trash_tree.topLevelItem(i)
                item.setHidden(query not in ' '.join(item.text(column) for column in range(5)).casefold())
        elif self.location == 'gallery' or not self.current_dir():
            if self.proxy.query != text:
                self.proxy.set_query(text)
        elif text.strip():
            self.start_search(text)
        else:
            self.stop_search()
        self.update_empty()
        self.selection_changed.emit()

    def start_search(self, text):
        self.stop_search(show_view=False)
        directory = self.current_dir()
        self.ensure_results()
        self.searching = True
        self.results.setSortingEnabled(False)
        self.results.clear()
        self.search_summary.setText(f'Wyszukiwanie „{text.strip()}” w {title_for(directory)}…')
        self.stack.setCurrentWidget(self.results_page)
        job = self.search_job = SearchJob(directory, text, self.window.show_hidden, self.search_mode)

        def found(batch):
            if alive(self) and job is self.search_job:
                self.add_results(batch)

        def done(count, truncated):
            self.window.background_jobs.discard(job)
            if alive(self) and job is self.search_job:
                self.finish_search(count, truncated, job.note)

        job.signals.found.connect(found)
        job.signals.done.connect(done)
        self.window.start_job(job)

    def stop_search(self, show_view=True):
        if self.search_job is not None:
            self.search_job.cancelled = True
            self.search_job = None
        if self.searching:
            self.searching = False
            self.results.clear()
        if show_view and self.current_dir():
            self.stack.setCurrentWidget(self.gallery if self.location == 'gallery' else self.directory_view())

    def add_results(self, batch):
        root = self.current_dir()
        provider = self.window.provider
        self.results.setUpdatesEnabled(False)
        for path, is_dir, size, mtime in batch:
            folder = os.path.dirname(path)
            relative = os.path.relpath(folder, root) if folder != root else '.'
            name = os.path.basename(path)
            if is_dir:
                kind = 'Folder plików'
            else:
                suffix = name.rpartition('.')[2] if '.' in name[1:] else ''
                kind = 'Plik ' + suffix.upper() if suffix else 'Plik'
            item = QTreeWidgetItem([name, relative, format_date(mtime) if mtime else '', '' if is_dir else size_text(size), kind])
            item.setIcon(0, provider.for_path(path, is_dir))
            item.setData(0, Qt.UserRole, path)
            item.setData(3, Qt.UserRole, size)
            item.setToolTip(0, path)
            self.results.addTopLevelItem(item)
        self.results.setUpdatesEnabled(True)
        self.search_summary.setText(f'Wyszukiwanie „{self.search.strip()}” w {title_for(root)}… znaleziono: {self.results.topLevelItemCount()}')
        self.selection_changed.emit()

    def finish_search(self, count, truncated, backend_note=''):
        self.search_job = None
        note = ' (pokazano pierwsze wyniki, doprecyzuj zapytanie)' if truncated else ''
        if backend_note:
            note += '\n' + backend_note
        self.search_summary.setText(f'Wyniki wyszukiwania „{self.search.strip()}” w {title_for(self.current_dir())}: {count}{note}')
        self.results.setSortingEnabled(True)
        self.selection_changed.emit()

    def drop_target(self, view, pos):
        """Directory a drop at `pos` lands in: the folder row under the cursor, else the current directory."""
        directory = self.current_dir()
        if directory and isinstance(view, (QTreeView, QListView)):
            index = view.indexAt(pos)
            if index.isValid():
                source_index = self.proxy.mapToSource(index)
                if self.window.model.isDir(source_index):
                    return self.window.model.filePath(source_index)
        return directory

    def neighbour_of_selection(self):
        """What Windows selects after deleting the selection: the next unselected entry, else the previous one."""
        if not self.current_dir() or self.searching:
            return None
        rows = sorted({index.row() for index in self.tree.selectionModel().selectedIndexes() if index.column() == 0})
        if not rows:
            return None
        root = self.tree.rootIndex()
        chosen = set(rows)
        for row in list(range(rows[-1] + 1, self.proxy.rowCount(root))) + list(range(rows[0] - 1, -1, -1)):
            if row not in chosen:
                return self.window.model.filePath(self.proxy.mapToSource(self.proxy.index(row, 0, root)))
        return None

    def select_paths(self, paths, attempts=30):
        """Select freshly created entries (like Windows after a move); rows arrive asynchronously."""
        if not alive(self) or not self.current_dir() or self.searching:
            return
        model = self.window.model
        root = self.tree.rootIndex()
        indexes = [self.proxy.mapFromSource(model.index(str(path))) for path in paths]
        indexes = [index for index in indexes if index.isValid() and index.parent() == root]
        if len(indexes) < len(paths) and attempts > 0:
            QTimer.singleShot(100, lambda: self.select_paths(paths, attempts - 1))
            return
        if not indexes:
            return
        selection = self.tree.selectionModel()
        selection.clearSelection()
        for index in indexes:
            selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        selection.setCurrentIndex(indexes[0], QItemSelectionModel.NoUpdate)
        self.stack.currentWidget().scrollTo(indexes[0])

    def edit_name(self, path):
        """Open the inline name editor on `path` in the visible directory view."""
        if not self.current_dir() or self.searching:
            return False
        source = self.window.model.index(str(path))
        index = self.proxy.mapFromSource(source)
        view = self.stack.currentWidget()
        if not source.isValid() or not index.isValid() or view not in (self.tree, self.grid):
            return False
        view.setCurrentIndex(index)
        view.scrollTo(index)
        view.edit(index)
        return True

    def edit_when_available(self, path, attempts=30):
        """A just-created entry reaches the model asynchronously; retry until its row exists."""
        if not alive(self) or self.edit_name(path) or attempts <= 0:
            return
        QTimer.singleShot(100, lambda: self.edit_when_available(path, attempts - 1))

    def set_mode(self, mode):
        if mode == 'icons':
            mode = 'large'
        if mode not in VIEW_MODES:
            return
        self.mode = mode
        self.configure_grid(mode)
        self.window.settings.setValue('view_mode', mode)
        if self.current_dir() and not self.searching:
            self.stack.setCurrentWidget(self.gallery if self.location == 'gallery' else self.directory_view())
        self.changed.emit()

    def cycle_mode(self, step):
        order = list(VIEW_MODES)
        self.set_mode(order[(order.index(self.mode) + step) % len(order)])

    def selection_view(self):
        if self.location == 'home':
            return self.recent_tree
        if self.location == 'trash':
            return self.trash_tree
        if self.searching:
            return self.results
        if self.location == 'gallery':
            return self.gallery_grid
        return self.directory_view() if self.current_dir() else None

    def select_all(self):
        view = self.selection_view()
        if view is not None:
            view.selectAll()

    def invert_selection(self):
        view = self.selection_view()
        if view is None:
            return
        selection = view.selectionModel()
        root = view.rootIndex()
        for row in range(view.model().rowCount(root)):
            if isinstance(view, QTreeView) and view.isRowHidden(row, root):
                continue
            if isinstance(view, QListView) and view.isRowHidden(row):
                continue
            index = view.model().index(row, 0, root)
            selection.select(index, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)

    def open_index(self, index):
        if index.isValid():
            path = self.window.model.filePath(self.proxy.mapToSource(index))
            self.open_path(path)

    def open_path(self, path):
        if Path(path).is_dir():
            self.navigate(path)
        else:
            self.window.launch_paths([path])

    def selected_paths(self):
        if self.location == 'home':
            return [i.data(0, Qt.UserRole) for i in self.recent_tree.selectedItems()] if self.recent_tree else []
        if self.location == 'trash':
            return [i.data(0, Qt.UserRole) for i in self.trash_tree.selectedItems()]
        if not self.current_dir():
            return []
        if self.searching:
            return [i.data(0, Qt.UserRole) for i in self.results.selectedItems()]
        # selectedRows() needs every column selected; the icon grid selects only column 0.
        return [self.window.model.filePath(self.proxy.mapToSource(i))
                for i in self.tree.selectionModel().selectedIndexes() if i.column() == 0]

    def context(self, view, pos):
        index = view.indexAt(pos)
        if index.isValid() and not view.selectionModel().isSelected(index):
            view.setCurrentIndex(index)
        if not index.isValid():
            view.clearSelection()
        self.window.context_menu(view.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MiddleButton:
            view = obj.parent()
            if isinstance(view, (QTreeView, QListView)):
                index = view.indexAt(event.position().toPoint())
                if index.isValid():
                    source = self.proxy.mapToSource(index)
                    if self.window.model.isDir(source):
                        self.window.add_tab(self.window.model.filePath(source))
                        return True
        elif event.type() == QEvent.Type.Wheel and event.modifiers() & Qt.ControlModifier and obj in (self.grid.viewport(), self.tree.viewport()):
            self.cycle_mode(-1 if event.angleDelta().y() > 0 else 1)  # wheel up = bigger icons, like Windows
            return True
        elif event.type() == QEvent.Type.DragEnter:
            mime = event.mimeData()
            if mime and mime.hasUrls():
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Type.DragMove:
            mime = event.mimeData()
            if mime and mime.hasUrls():
                view = obj.parent()
                pos = event.position().toPoint()
                directory = self.drop_target(view, pos)
                sources = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
                if directory and sources:
                    if all(str(source.parent) == directory for source in sources) and event.source() is view:
                        self.window.hide_drop_hint()
                        event.ignore()  # hovering over its own folder: nothing to do here
                        return True
                    action = self.window.drop_action(sources, directory, event.modifiers())
                    event.setDropAction(Qt.LinkAction if action == 'link' else Qt.MoveAction if action == 'move' else Qt.CopyAction)
                    event.accept()
                    self.window.show_drop_hint(action, directory, obj.mapToGlobal(pos))
                else:
                    event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Type.DragLeave:
            self.window.hide_drop_hint()
        elif event.type() == QEvent.Type.Drop:
            self.window.hide_drop_hint()
            mime = event.mimeData()
            if not mime or not mime.hasUrls():
                return False
            view = obj.parent()
            directory = self.drop_target(view, event.position().toPoint())
            if not directory:
                return False
            sources = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
            if not sources:
                return False
            if all(str(source.parent) == directory for source in sources) and event.source() is view:
                event.ignore()  # dropped back onto its own folder
                return True
            self.window.drop_files(sources, directory, event, obj.mapToGlobal(event.position().toPoint()))
            # Report Copy to the drag source so no view tries to "remove" the moved rows itself.
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def card(self, name, kind, path, description='Zapisane lokalnie'):
        button = QPushButton()
        button.setObjectName('folderCard')
        button.setFixedHeight(76)
        button.setMinimumWidth(215)
        button.setToolTip(str(path))
        button.setAccessibleName(name)
        row = box(button, margins=(14, 8, 14, 8), spacing=14)
        picture = QLabel()
        picture.setPixmap(icon('folder-' + kind).pixmap(48, 48))
        row.addWidget(picture)
        texts = box(vertical=True, spacing=3)
        texts.addWidget(label(name))
        texts.addWidget(label(description, 'muted'))
        row.addLayout(texts, 1)
        pin = QLabel()
        pin.setPixmap(icon('pin', '#8b8b8b').pixmap(13, 13))
        row.addWidget(pin)
        for child in button.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        button.clicked.connect(lambda: self.navigate(str(path)))
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda pos: self.folder_context(button, pos, path))
        return button

    def folder_context(self, button, pos, path):
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_DeleteOnClose)
        menu.addAction(icon('folder'), 'Otwórz', lambda: self.navigate(str(path)))
        menu.addAction(icon('plus'), 'Otwórz w nowej karcie', lambda: self.window.add_tab(str(path)))
        menu.popup(button.mapToGlobal(pos))

    def build_home(self):
        self.ensure_home()
        while self.home_layout.count():
            item = self.home_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.home_layout.addWidget(label('⌄   Szybki dostęp', 'sectionTitle'))
        cards = QWidget()
        cards_layout = QGridLayout(cards)
        cards_layout.setContentsMargins(10, 0, 0, 8)
        cards_layout.setHorizontalSpacing(12)
        cards_layout.setVerticalSpacing(4)
        for i, (name, kind, path) in enumerate(FOLDERS):
            cards_layout.addWidget(self.card(name, kind, path), i // 3, i % 3)
        self.home_layout.addWidget(cards)
        tabs = QWidget()
        tabs.setObjectName('homeTabs')
        tabs_layout = box(tabs, spacing=4)
        for text, kind, value in [('Ostatnie', 'refresh', 'recent'), ('Ulubione', 'star', 'favorites')]:
            button = QPushButton(icon(kind), text)
            button.setCheckable(True)
            button.setChecked(self.home_filter == value)
            button.clicked.connect(lambda checked, v=value: self.change_home_filter(v))
            tabs_layout.addWidget(button)
        tabs_layout.addStretch()
        self.home_layout.addWidget(tabs)
        tree = FilePathTree()
        tree.setHeaderLabels(['Nazwa', 'Data modyfikacji', 'Lokalizacja'])
        tree.setRootIsDecorated(False)
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tree.setColumnWidth(0, 360)
        tree.setColumnWidth(1, 175)
        tree.setMinimumHeight(200)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(lambda pos: self.window.context_menu(tree.viewport().mapToGlobal(pos)))
        tree.itemDoubleClicked.connect(lambda item, col: self.open_path(item.data(0, Qt.UserRole)))
        tree.itemSelectionChanged.connect(self.selection_changed)
        self.recent_tree = tree
        paths = self.window.recent_files() if self.home_filter == 'recent' else self.window.favorites()
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            item = QTreeWidgetItem([path.name, format_date(stat.st_mtime), str(path.parent).replace(str(HOME), 'Folder osobisty', 1)])
            item.setIcon(0, self.window.provider.icon(QFileInfo(str(path))))
            item.setData(0, Qt.UserRole, str(path))
            item.setToolTip(0, str(path))
            tree.addTopLevelItem(item)
        self.home_layout.addWidget(tree, 1)
        if not paths:
            self.home_layout.addWidget(label('Ostatnio używane pliki pojawią się tutaj.' if self.home_filter == 'recent' else 'Dodaj pliki do ulubionych z menu pod prawym przyciskiem myszy.', 'empty'))
        self.home_layout.addStretch()
        self.register_filters()

    def change_home_filter(self, value):
        self.home_filter = value
        self.build_home()

    def build_computer(self):
        self.ensure_computer()
        body = QWidget()
        body.setObjectName('computer')
        layout = box(body, vertical=True, margins=(28, 24, 28, 24), spacing=20)
        layout.addWidget(label('⌄   Urządzenia i dyski', 'sectionTitle'))
        grid = QGridLayout()
        grid.setSpacing(20)
        volumes = mounted_volumes()
        cards = [(volume, self.window.device_entry(mountpoint=volume.rootPath())) for volume in volumes]
        known_mounts = {volume.rootPath() for volume in volumes}
        cards.extend((None, entry) for entry in self.window.device_cache
                     if not any(mount in known_mounts for mount in entry.mountpoints))
        for i, (volume, device) in enumerate(cards):
            card = QWidget()
            card_layout = box(card, vertical=True, spacing=6)
            button = QPushButton()
            button.setObjectName('folderCard')
            button.setMinimumSize(280, 100)
            card_layout.addWidget(button)
            row = box(button, margins=(14, 12, 14, 12), spacing=16)
            picture = QLabel()
            picture.setPixmap(icon('drive').pixmap(52, 52))
            row.addWidget(picture)
            texts = box(vertical=True, spacing=7)
            if volume:
                path = volume.rootPath()
                texts.addWidget(label(f'{volume_name(volume)} ({path})'))
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setValue(int(1000 * (1 - volume.bytesAvailable() / volume.bytesTotal())))
                bar.setTextVisible(False)
                bar.setFixedHeight(13)
                texts.addWidget(bar)
                texts.addWidget(label(f'{size_text(volume.bytesAvailable())} wolnych z {size_text(volume.bytesTotal())}', 'muted'))
                button.clicked.connect(lambda checked=False, p=path: self.navigate(p))
            else:
                texts.addWidget(label(device.title))
                status = device.mountpoint or 'Niezamontowany'
                texts.addWidget(label(status, 'muted'))
                texts.addWidget(label(f'{device.filesystem or "Brak systemu plików"} · {size_text(device.size)}', 'muted'))
                button.clicked.connect(lambda checked=False, p=device.path, b=button: self.window.activate_device(p, b))
            row.addLayout(texts, 1)
            for child in button.findChildren(QWidget):
                child.setAttribute(Qt.WA_TransparentForMouseEvents)
            if device:
                button.setToolTip(device.path)
                button.setContextMenuPolicy(Qt.CustomContextMenu)
                button.customContextMenuRequested.connect(
                    lambda pos, p=device.path, b=button: self.window.device_context(p, b.mapToGlobal(pos)))
                actions = box(spacing=6)
                choices = []
                if device.can_mount:
                    choices.append(('Zamontuj', 'mount'))
                if device.can_unmount:
                    choices.append(('Odmontuj', 'unmount'))
                if device.can_power_off:
                    choices.append(('Bezpiecznie usuń', 'power-off'))
                for caption, operation in choices:
                    action_button = QPushButton(caption)
                    action_button.setProperty('device_operation', operation)
                    action_button.setProperty('device_path', device.path)
                    action_button.setEnabled(not self.window.filesystem_operations_busy())
                    action_button.clicked.connect(
                        lambda checked=False, p=device.path, op=operation: self.window.device_operation(p, op))
                    actions.addWidget(action_button)
                if choices:
                    actions.addStretch()
                    card_layout.addLayout(actions)
            grid.addWidget(card, i // 2, i % 2)
        layout.addLayout(grid)
        if self.window.device_discovery_error:
            layout.addWidget(label('Nie udało się odczytać wszystkich nośników: ' + self.window.device_discovery_error, 'muted'))
        elif self.window.device_scan_pending and not self.window.device_cache:
            layout.addWidget(label('Sprawdzanie pozostałych nośników…', 'muted'))
        layout.addStretch()
        old = self.computer.takeWidget()
        if old:
            old.deleteLater()
        self.computer.setWidget(body)
        self.register_filters()


class WorkerSignals(QObject):
    finished = Signal(str)


class FileJob(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.setAutoDelete(False)
        self.operation = operation
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.operation()
            error = ''
        except Exception as exc:
            error = str(exc)
        try:
            self.signals.finished.emit(error)
        except RuntimeError:
            pass  # signal owner already destroyed during shutdown


class TransferSignals(QObject):
    progress = Signal(object, object, str)  # Python integers: byte counts can exceed 2 GiB
    finished = Signal(str, object)


def write_trashinfo(source, target):
    """Write the XDG .trashinfo companion for `target` and return its path."""
    return trash_backend.write_info(source, target, TRASH_ROOT)


def remove_entry(path):
    """Delete a file, symlink or whole directory tree without following links."""
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif os.path.lexists(path):
        path.unlink()


def entry_version(path):
    """Identity and metadata of an entry, without following symbolic links."""
    info = os.lstat(path)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def tree_version(path):
    """Metadata snapshot of an entry and its descendants, never following directory links."""
    path = Path(path)
    result = [('', entry_version(path))]
    if stat_module.S_ISDIR(result[0][1][2]):
        def fail(error):
            raise error
        for root, dirs, files in os.walk(path, onerror=fail, followlinks=False):
            for name in dirs + files:
                child = Path(root) / name
                result.append((str(child.relative_to(path)), entry_version(child)))
    return tuple(sorted(result))


class TransferHistory(list):
    """Versions and overwritten targets travel with history to prevent destructive undo."""
    def __init__(self, entries=(), versions=None, overwritten=(), backups=None):
        super().__init__(entries)
        self.versions = dict(versions) if versions is not None else {}
        self.overwritten = set(overwritten).intersection(entry[2] for entry in self)
        self.backups = {target: backup for target, backup in (backups or {}).items()
                        if target in {entry[2] for entry in self}}
        if versions is None:
            for entry in self:
                self.capture(entry)

    def capture(self, entry):
        operation, _, target = entry
        if operation == 'copy':
            try:
                self.versions[target] = tree_version(target)
            except OSError:
                self.versions[target] = None  # unreadable state must never authorize deletion



class TransferJob(QRunnable):
    """Copy or move a list of (source, target) pairs with throttled progress and cancellation."""
    CHUNK = 4 * 1024 * 1024

    def __init__(self, transfers, cut=False, overwrites=None):
        super().__init__()
        self.setAutoDelete(False)
        self.transfers = transfers
        self.overwrites = overwrites or {}
        self.backups = {}
        self.cut = cut
        self.signals = TransferSignals()
        self.cancel_requested = False
        self.copied = 0
        self.total = 0
        self.last_report = 0.0

    def report(self, name, force=False):
        now = time.monotonic()
        if force or now - self.last_report >= 0.1:
            self.last_report = now
            self.signals.progress.emit(self.copied, self.total, name)

    @staticmethod
    def count_bytes(src):
        if src.is_symlink():
            return 0
        if src.is_file():
            return src.stat().st_size
        total = 0
        if src.is_dir():
            for root, _, files in os.walk(src):
                for name in files:
                    try:
                        info = os.lstat(os.path.join(root, name))
                    except OSError:
                        continue
                    if stat_module.S_ISREG(info.st_mode):
                        total += info.st_size
        return total

    FALLBACK_ERRNOS = frozenset({errno.EXDEV, errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM, errno.EIO})

    def copy_file(self, src, tgt):
        """Copy with os.copy_file_range (in-kernel, reflink on btrfs/XFS) and a read/write fallback."""
        original_version = entry_version(src)
        with open(src, 'rb') as source, open(tgt, 'xb') as target:
            in_kernel = hasattr(os, 'copy_file_range')
            source_fd, target_fd = source.fileno(), target.fileno()
            while True:
                if self.cancel_requested:
                    return False
                if in_kernel:
                    try:
                        moved = os.copy_file_range(source_fd, target_fd, self.CHUNK)
                    except OSError as exc:
                        if exc.errno in self.FALLBACK_ERRNOS:
                            in_kernel = False  # file offsets stay consistent, so read/write carries on from here
                            continue
                        raise
                else:
                    chunk = source.read(self.CHUNK)
                    moved = len(chunk)
                    if moved:
                        target.write(chunk)
                if not moved:
                    if in_kernel:
                        # Some virtual/network filesystems return zero before EOF.
                        in_kernel = False
                        continue
                    break
                self.copied += moved
                self.report(src.name)
        if entry_version(src) != original_version:
            raise OSError(f'Plik źródłowy zmienił się podczas kopiowania: {src}')
        shutil.copystat(src, tgt)
        return True

    @staticmethod
    def walk_error(error):
        raise error

    def copy_entry(self, src, tgt):
        if src.is_symlink():
            os.symlink(os.readlink(src), tgt)
            return True
        if not src.is_dir():
            if not stat_module.S_ISREG(src.stat().st_mode):
                raise shutil.SpecialFileError(f'Nie można kopiować pliku specjalnego: {src}')
            return self.copy_file(src, tgt)
        tgt.mkdir(parents=True)
        directories = []
        for root, dirs, files in os.walk(src, onerror=self.walk_error):
            if self.cancel_requested:
                return False
            root = Path(root)
            current = tgt / root.relative_to(src)
            for name in dirs:
                child = root / name
                if child.is_symlink():
                    os.symlink(os.readlink(child), current / name)
                else:
                    (current / name).mkdir(exist_ok=True)
            for name in files:
                if not self.copy_entry(root / name, current / name):
                    return False
            directories.append((root, current))
        # Apply read-only permissions only after all descendants have been copied.
        for source_dir, target_dir in reversed(directories):
            shutil.copystat(source_dir, target_dir)
        return True

    def copy_atomic(self, src, tgt, expected=None):
        """Publish a complete copy; leave the previous destination intact on failure/cancellation."""
        if expected is None and os.path.lexists(tgt):
            raise FileExistsError(errno.EEXIST, 'Element docelowy już istnieje', str(tgt))
        original_source = tree_version(src)
        with tempfile.TemporaryDirectory(prefix='.fileexplorer-transfer-', dir=tgt.parent) as temporary:
            staged = Path(temporary) / 'payload'
            if not self.copy_entry(src, staged) or self.cancel_requested:
                return False
            # Per-file checks cannot detect additions, removals or late edits to
            # files already copied. Never publish such a tree or delete its source.
            if tree_version(src) != original_source:
                raise OSError(f'Element źródłowy zmienił się podczas kopiowania: {src}')
            saved = None
            if expected is not None:
                if entry_version(tgt) != expected:
                    raise FileExistsError(f'Element docelowy zmienił się podczas kopiowania: {tgt}')
                saved = overwrite_backup.Backup.create(tgt, src, 'move' if self.cut else 'copy')
                try:
                    rename_noreplace(tgt, saved.previous)
                except Exception:
                    saved.discard_empty()
                    raise
            try:
                rename_noreplace(staged, tgt)
            except Exception as exc:
                if saved is not None and os.path.lexists(saved.previous):
                    try:
                        rename_noreplace(saved.previous, tgt)
                        saved.discard_empty()
                    except OSError as restore_error:
                        raise OSError(f'Nie udało się przywrócić celu. Poprzednia wersja pozostaje w {saved.previous}: '
                                      f'{restore_error}') from exc
                raise
            if saved is not None:
                saved.capture(tree_version)
                self.backups[tgt] = saved
        return True

    def move_entry(self, src, tgt, size):
        try:
            rename_noreplace(src, tgt)
            self.copied += size
            return True
        except OSError as exc:
            if exc.errno != errno.EXDEV:  # EXDEV: different filesystem, fall back to copy + delete
                raise
        if not self.copy_atomic(src, tgt):
            return False
        remove_entry(src)
        return True

    def run(self):
        completed = TransferHistory()
        try:
            sizes = [self.count_bytes(src) for src, _ in self.transfers]
            self.total = sum(sizes)
            for (src, tgt), size in zip(self.transfers, sizes):
                if self.cancel_requested:
                    break
                self.report(src.name, force=True)
                before = self.copied
                expected = self.overwrites.get(tgt)
                if self.cut and expected is None:
                    done = self.move_entry(src, tgt, size)
                else:
                    done = self.copy_atomic(src, tgt, expected)
                    if done and self.cut:
                        try:
                            remove_entry(src)
                        except Exception:
                            completed.append(('copy', src, tgt))
                            completed.capture(completed[-1])
                            if tgt in self.backups:
                                self.backups[tgt].operation = 'copy'
                                self.backups[tgt].save_completed()
                                completed.backups[tgt] = self.backups[tgt]
                                completed.overwritten.add(tgt)
                            raise
                if not done:
                    break
                self.copied = before + size
                completed.append(('move' if self.cut else 'copy', src, tgt))
                completed.capture(completed[-1])
                if expected is not None:
                    completed.overwritten.add(tgt)
                    completed.backups[tgt] = self.backups[tgt]
            self.report('', force=True)
            self.signals.finished.emit('', completed)
        except Exception as exc:
            self.signals.finished.emit(str(exc), completed)


class ConflictDialog(QDialog):
    def __init__(self, parent, src, tgt):
        super().__init__(parent)
        self.setWindowTitle('Kolizja plików')
        self.resolution = 'skip'
        self.apply_all = False
        
        layout = box(self, vertical=True, margins=(20, 20, 20, 20), spacing=15)
        layout.addWidget(label(f'Plik docelowy już istnieje:\n{tgt.name}', 'sectionTitle'))
        
        try:
            src_stat = src.stat()
            tgt_stat = tgt.stat()
            info = f'Źródło: {size_text(src_stat.st_size)}, zmodyfikowano {format_date(src_stat.st_mtime)}\n'
            info += f'Cel: {size_text(tgt_stat.st_size)}, zmodyfikowano {format_date(tgt_stat.st_mtime)}'
            layout.addWidget(label(info))
        except OSError:
            pass
            
        self.check_all = QCheckBox('Zastosuj do wszystkich przyszłych konfliktów')
        layout.addWidget(self.check_all)
        
        buttons = QDialogButtonBox()
        btn_overwrite = buttons.addButton('Nadpisz', QDialogButtonBox.ActionRole)
        btn_skip = buttons.addButton('Pomiń', QDialogButtonBox.ActionRole)
        btn_rename = buttons.addButton('Zmień nazwę', QDialogButtonBox.ActionRole)
        
        btn_overwrite.clicked.connect(lambda: self._resolve('overwrite'))
        btn_skip.clicked.connect(lambda: self._resolve('skip'))
        btn_rename.clicked.connect(lambda: self._resolve('rename'))
        
        layout.addWidget(buttons)
        
    def _resolve(self, res):
        self.resolution = res
        self.apply_all = self.check_all.isChecked()
        self.accept()

class ProgressDialog(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(400, 150)
        self.job = None
        layout = box(self, vertical=True, margins=(20, 20, 20, 20))
        self.lbl_file = label('Przygotowywanie...')
        layout.addWidget(self.lbl_file)
        self.bar = QProgressBar()
        layout.addWidget(self.bar)
        self.lbl_stats = label('', 'muted')
        layout.addWidget(self.lbl_stats)
        
        btn_box = QDialogButtonBox(QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Cancel).setText('Anuluj')
        btn_box.rejected.connect(self.cancel)
        layout.addWidget(btn_box)
        self.start_time = time.monotonic()

    def update_progress(self, copied, total, current_file):
        if current_file:
            self.lbl_file.setText(f'Plik: {current_file}')
        if total > 0:
            self.bar.setValue(min(100, int(copied / total * 100)))
        elapsed = time.monotonic() - self.start_time
        speed = copied / elapsed if elapsed > 0.2 else 0
        remaining = f', pozostało ok. {int((total - copied) / speed)} s' if speed and total > copied else ''
        self.lbl_stats.setText(f'{size_text(copied)} / {size_text(total)} ({size_text(speed)}/s{remaining})')

    def cancel(self):
        if self.job is not None:
            self.job.cancel_requested = True
            self.lbl_file.setText('Anulowanie…')

    def closeEvent(self, event):
        self.cancel()
        event.ignore()
            
class BatchRenameDialog(QDialog):
    def __init__(self, parent, count):
        super().__init__(parent)
        self.setWindowTitle('Masowa zmiana nazwy')
        self.setMinimumWidth(350)
        layout = box(self, vertical=True, margins=(20, 20, 20, 20))
        layout.addWidget(label(f'Zmieniasz nazwę {count} elementów.', 'sectionTitle'))
        layout.addWidget(label('Podaj wzorzec nazwy (znak # zostanie zastąpiony numerem):'))
        self.input = QLineEdit('Element #')
        layout.addWidget(self.input)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def pattern(self):
        text = self.input.text()
        if not text:
            return None
        if '#' not in text:
            text += ' #'
        return text

def application_dirs():
    data_home = Path(os.environ.get('XDG_DATA_HOME') or HOME / '.local/share')
    if not data_home.is_absolute():
        data_home = HOME / '.local/share'
    dirs = [data_home / 'applications']
    for data_dir in (os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share').split(':'):
        if data_dir and Path(data_dir).is_absolute():
            dirs.append(Path(data_dir) / 'applications')
    return list(dict.fromkeys(dirs))


def parse_desktop_entry(path):
    values = {}
    in_section = False
    for raw in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw.strip()
        if line.startswith('['):
            in_section = line == '[Desktop Entry]'
            continue
        if in_section and '=' in line and not line.startswith(('#', ';')):
            key, value = line.split('=', 1)
            escapes = {'s': ' ', 'n': '\n', 't': '\t', 'r': '\r', '\\': '\\'}
            values.setdefault(key.strip(), re.sub(r'\\([sntr\\])', lambda match: escapes[match[1]], value.strip()))
    return values


def desktop_entry_available(values, visible=False):
    """Respect user overrides and TryExec before offering or launching an application."""
    if not values or values.get('Type') != 'Application' or values.get('Hidden', '').lower() == 'true':
        return False
    if visible and values.get('NoDisplay', '').lower() == 'true':
        return False
    if not values.get('Exec') and values.get('DBusActivatable', '').lower() != 'true':
        return False
    executable = values.get('TryExec', '')
    if executable and ('\x00' in executable or not shutil.which(executable)):
        return False
    return True


def desktop_entry_name(values, desktop_id):
    locale = QLocale().name()
    names = [f'Name[{locale}]', f'Name[{locale.split("_")[0]}]', 'Name[pl]', 'Name']
    return next((values[key] for key in names if values.get(key)), desktop_id.removesuffix('.desktop'))


_DESKTOP_CACHE = {'signature': None, 'entries': {}}


def desktop_entries():
    """Cache parsed entries while noticing edits, replacements and changes in nested directories."""
    dirs = application_dirs()
    signature = []
    files = []
    for directory in dirs:
        try:
            signature.append((str(directory), directory.stat().st_mtime_ns))
            for entry in sorted(directory.rglob('*.desktop')):
                try:
                    info = entry.stat()
                except OSError:
                    continue
                if not stat_module.S_ISREG(info.st_mode):
                    continue
                signature.append((str(entry), info.st_dev, info.st_ino, info.st_mtime_ns,
                                  info.st_ctime_ns, info.st_size))
                files.append((directory, entry))
        except OSError:
            signature.append((str(directory), 0))
    signature = tuple(signature)
    if signature == _DESKTOP_CACHE['signature']:
        return _DESKTOP_CACHE['entries']
    entries = {}
    for directory, entry in files:
        desktop_id = str(entry.relative_to(directory)).replace('/', '-')
        if desktop_id in entries:
            continue
        try:
            entries[desktop_id] = parse_desktop_entry(entry)
            entries[desktop_id]['X-Entry-Path'] = str(entry)
        except OSError:
            continue
    _DESKTOP_CACHE.update(signature=signature, entries=entries)
    return entries


class OpenWithDialog(QDialog):
    """Native 'Open with...' dialog replacing dolphin --select delegation."""

    def __init__(self, parent, mime_type=''):
        super().__init__(parent)
        self.setWindowTitle('Otwórz za pomocą…')
        self.setMinimumSize(480, 420)
        self.mime_type = mime_type
        self.chosen_id = None
        self.set_default = False
        layout = box(self, vertical=True, margins=(20, 18, 20, 18), spacing=12)
        layout.addWidget(label(f'Wybierz aplikację do otwarcia pliku ({mime_type})' if mime_type else 'Wybierz aplikację', 'sectionTitle'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Szukaj aplikacji…')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Aplikacja', 'Polecenie'])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 280)
        self.tree.itemDoubleClicked.connect(lambda: self.accept())
        layout.addWidget(self.tree, 1)
        self.command = QLineEdit()
        self.command.setPlaceholderText('…lub wpisz własne polecenie, np. vlc --fullscreen')
        self.command.setClearButtonEnabled(True)
        self.command.textChanged.connect(lambda text: self.default_check.setEnabled(not text.strip()))
        self.chosen_command = ''
        layout.addWidget(self.command)
        self.default_check = QCheckBox('Ustaw jako domyślną dla tego typu plików')
        self.default_check.setVisible(bool(mime_type))
        layout.addWidget(self.default_check)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.open_button = buttons.button(QDialogButtonBox.Ok)
        self.open_button.setText('Otwórz')
        self.open_button.setEnabled(False)
        self.tree.itemSelectionChanged.connect(self._update_open_button)
        self.command.textChanged.connect(self._update_open_button)
        buttons.button(QDialogButtonBox.Cancel).setText('Anuluj')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._populate()

    def _populate(self):
        rows = []
        for desktop_id, values in desktop_entries().items():
            if not desktop_entry_available(values, visible=True):
                continue
            if self.mime_type and desktop_id == 'fileexplorer.desktop':
                continue
            name = desktop_entry_name(values, desktop_id)
            rows.append((name.casefold(), name, values.get('Exec', '').split('%')[0].strip(), values.get('Icon', ''), desktop_id))
        self.tree.setUpdatesEnabled(False)
        for _, name, exe, icon_name, desktop_id in sorted(rows):
            item = QTreeWidgetItem([name, exe])
            item.setData(0, Qt.UserRole, desktop_id)
            item.setIcon(0, (QIcon(icon_name) if Path(icon_name).is_absolute() else
                            QIcon.fromTheme(icon_name, icon('file'))) if icon_name else icon('file'))
            self.tree.addTopLevelItem(item)
        self.tree.setUpdatesEnabled(True)

    def _filter(self, text):
        query = text.casefold()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setHidden(query not in item.text(0).casefold() and query not in item.text(1).casefold())
            if item.isHidden() and item.isSelected():
                item.setSelected(False)

    def _update_open_button(self):
        self.open_button.setEnabled(bool(self.command.text().strip() or self.tree.selectedItems()))

    def accept(self):
        items = self.tree.selectedItems()
        self.chosen_command = self.command.text().strip()
        self.chosen_id = None
        self.set_default = False
        if not items and not self.chosen_command:
            return
        if self.chosen_command:
            try:
                if not shlex.split(self.chosen_command):
                    return
            except ValueError:
                self.command.setFocus()
                self.command.setToolTip('Polecenie zawiera niedomknięty cudzysłów.')
                return
        if items and not self.chosen_command:
            self.chosen_id = items[0].data(0, Qt.UserRole)
            self.set_default = self.default_check.isChecked()
        super().accept()


class Menu(QMenu):
    """QMenu with a translucent frameless window so the rounded corners from style.qss are real."""

    def __init__(self, parent=None, title=''):
        super().__init__(title, parent)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setToolTipsVisible(True)

    def addMenu(self, *args):
        if args and isinstance(args[0], QMenu):
            return super().addMenu(*args)
        if len(args) == 2:
            submenu = Menu(self, args[1])
            submenu.setIcon(args[0])
        else:
            submenu = Menu(self, args[0])
        super().addMenu(submenu)
        return submenu


def share_device_name():
    """Name rquickshare broadcasts for this computer (its default is the host name)."""
    import socket
    return socket.gethostname()


class DragHandle(QLabel):
    """A label that starts a file drag (text/uri-list) so files can be dropped onto another app's window."""

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = [str(p) for p in paths]
        self.setObjectName('dragHandle')
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.OpenHandCursor)
        self.setMinimumHeight(72)
        self.setText(f'Przeciągnij stąd na okno Quick Share\n({len(self.paths)} {"element" if len(self.paths) == 1 else "elementów"})')
        self.press_position = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.press_position = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.press_position is None or (event.position().toPoint() - self.press_position).manhattanLength() < QApplication.startDragDistance():
            return
        from PySide6.QtGui import QDrag
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in self.paths])
        mime.setText('\n'.join(self.paths))
        drag.setMimeData(mime)
        drag.setPixmap(icon('share').pixmap(32, 32))
        self.setCursor(Qt.ClosedHandCursor)
        drag.exec(Qt.CopyAction)
        self.setCursor(Qt.OpenHandCursor)
        self.press_position = None

    def mouseReleaseEvent(self, event):
        self.press_position = None


class ShareDialog(QDialog):
    """Windows 11 style share sheet: nearby devices (KDE Connect, Quick Share), e-mail, Bluetooth, LAN link."""

    def __init__(self, window, paths):
        super().__init__(window)
        self.window = window
        self.paths = [Path(p) for p in paths]
        self.pack_job = None
        self.setObjectName('shareDialog')
        self.setWindowTitle('Udostępnij')
        self.setMinimumWidth(460)
        layout = box(self, vertical=True, margins=(24, 22, 24, 20), spacing=14)
        header = QWidget()
        header_layout = box(header, spacing=12)
        picture = QLabel()
        first = self.paths[0]
        picture.setPixmap(window.provider.for_path(str(first), first.is_dir()).pixmap(40, 40))
        header_layout.addWidget(picture)
        texts = box(vertical=True, spacing=2)
        count = len(self.paths)
        title = first.name if count == 1 else f'{count} elementów'
        texts.addWidget(label(title, 'detailsTitle'))
        total = 0
        for path in self.paths:
            try:
                total += path.stat().st_size if path.is_file() else 0
            except OSError:
                pass
        folders = sum(1 for p in self.paths if p.is_dir())
        details = ', '.join(part for part in [size_text(total) if total else '', f'foldery: {folders}' if folders else ''] if part)
        texts.addWidget(label(details or str(first.parent), 'muted'))
        header_layout.addLayout(texts, 1)
        layout.addWidget(header)
        layout.addWidget(label('Udostępnij za pomocą', 'sectionTitle'))
        self.targets = QWidget()
        self.targets_layout = box(self.targets, vertical=True, spacing=6)
        layout.addWidget(self.targets)
        self.status = label('', 'muted', True)
        layout.addWidget(self.status)
        self.quick_panel = QWidget()
        self.quick_panel.hide()
        quick_layout = box(self.quick_panel, vertical=True, spacing=8)
        quick_layout.addWidget(label('Quick Share nie przyjmuje plików z wiersza poleceń, ale przyjmuje upuszczone na swoje okno. '
                                     'Przeciągnij uchwyt poniżej (albo pliki prosto z listy) na okno Quick Share i wybierz tam urządzenie.', 'muted', True))
        self.drag_handle = DragHandle(self.paths)
        quick_layout.addWidget(self.drag_handle)
        self.nearby = label('Szukam urządzeń Quick Share w pobliżu…', 'muted', True)
        quick_layout.addWidget(self.nearby)
        layout.addWidget(self.quick_panel)
        self.scan_job = None
        self.lan_panel = QWidget()
        self.lan_panel.hide()
        lan_layout = box(self.lan_panel, vertical=True, spacing=8)
        lan_layout.addWidget(label('Otwórz ten adres na telefonie lub innym komputerze w tej samej sieci:', 'muted', True))
        self.address_box = QComboBox()
        self.address_box.setToolTip('Interfejs sieciowy, przez który telefon ma pobrać pliki')
        lan_layout.addWidget(self.address_box)
        self.address_box.currentIndexChanged.connect(self.change_address)
        url_row = box(spacing=6)
        self.url_edit = QLineEdit()
        self.url_edit.setObjectName('shareUrl')
        self.url_edit.setReadOnly(True)
        url_row.addWidget(self.url_edit, 1)
        copy_button = QPushButton(icon('copy'), 'Kopiuj')
        copy_button.clicked.connect(lambda: (QApplication.clipboard().setText(self.url_edit.text()), self.status.setText('Skopiowano adres.')))
        url_row.addWidget(copy_button)
        lan_layout.addLayout(url_row)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        lan_layout.addWidget(self.qr)
        self.lan_info = label('', 'muted')
        lan_layout.addWidget(self.lan_info)
        stop_button = QPushButton(icon('stop'), 'Zakończ udostępnianie')
        stop_button.clicked.connect(self.stop_lan)
        lan_layout.addWidget(stop_button)
        layout.addWidget(self.lan_panel)
        buttons = QDialogButtonBox()
        buttons.addButton('Zamknij', QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.lan_timer = QTimer(self)
        self.lan_timer.timeout.connect(self.update_lan_info)
        self.populate()
        if window.share_server is not None:
            self.show_lan()

    def populate(self):
        while self.targets_layout.count():
            item = self.targets_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for key, title, description, icon_name, needs_files in share.targets():
            button = QPushButton()
            button.setObjectName('shareTarget')
            button.setMinimumHeight(52)
            button.setAccessibleName(title)
            row = box(button, margins=(6, 4, 6, 4), spacing=12)
            picture = QLabel()
            picture.setPixmap(icon(icon_name).pixmap(24, 24))
            row.addWidget(picture)
            texts = box(vertical=True, spacing=1)
            texts.addWidget(label(title))
            texts.addWidget(label(description, 'muted'))
            row.addLayout(texts, 1)
            arrow = QLabel()
            arrow.setPixmap(icon('right', '#888').pixmap(12, 12))
            row.addWidget(arrow)
            for child in button.findChildren(QWidget):
                child.setAttribute(Qt.WA_TransparentForMouseEvents)
            enabled = not key.startswith('quickshare:missing')
            button.setEnabled(enabled)
            if not enabled:
                button.setToolTip(description)
            button.clicked.connect(lambda checked=False, k=key, n=needs_files: self.choose(k, n))
            self.targets_layout.addWidget(button)

    def choose(self, key, needs_files):
        if key == 'copy':
            QApplication.clipboard().setText('\n'.join(str(p) for p in self.paths))
            self.status.setText('Skopiowano ścieżki do schowka.')
            return
        if key == 'uri':
            QApplication.clipboard().setText('\n'.join(QUrl.fromLocalFile(str(p)).toString() for p in self.paths))
            self.status.setText('Skopiowano adresy URI do schowka.')
            return
        if needs_files and any(p.is_dir() for p in self.paths):
            self.pack_then(lambda files: self.send(key, files))
        else:
            self.send(key, self.paths)

    def pack_then(self, callback):
        if self.pack_job is not None:
            return
        self.status.setText('Pakowanie folderów do archiwum ZIP…')
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.CacheLocation)) / 'share'
        job = self.pack_job = FileJob(lambda: None)
        job.cancelled = False
        job.result = None

        def operation():
            job.result = share.pack_paths(self.paths, cache, self.paths[0].stem or 'udostępnione', lambda: job.cancelled)
        job.operation = operation

        def finished(error):
            self.window.background_jobs.discard(job)
            if not alive(self):
                return
            self.pack_job = None
            if error:
                self.status.setText(f'Nie udało się spakować: {error}')
            elif not job.cancelled:
                callback([job.result])
        job.signals.finished.connect(finished)
        self.finished.connect(lambda *_: setattr(job, 'cancelled', True))
        self.window.start_job(job)

    def send(self, key, files):
        try:
            if key.startswith('kdeconnect:'):
                share.send_kdeconnect(key.split(':', 1)[1], files)
                self.status.setText('Wysłano przez KDE Connect. Postęp zobaczysz w powiadomieniach.')
            elif key.startswith('quickshare:'):
                was_running = share.send_quickshare(files)
                self.drag_handle.paths = [str(p) for p in files]
                self.drag_handle.setText(f'Przeciągnij stąd na okno Quick Share\n({len(files)} {"element" if len(files) == 1 else "elementów"})')
                self.quick_panel.show()
                self.scan_nearby()
                self.status.setText('Quick Share jest już otwarty.' if was_running else 'Uruchamianie Quick Share… okno pojawi się za chwilę.')
            elif key == 'email':
                share.send_email(files)
                self.status.setText('Otwarto program pocztowy z załącznikami.')
            elif key == 'bluetooth':
                share.send_bluetooth(files)
                self.status.setText('Otwarto wysyłanie przez Bluetooth.')
            elif key == 'lan':
                self.start_lan(files)
        except OSError as exc:
            self.status.setText(f'Nie udało się uruchomić: {exc}')

    def scan_nearby(self):
        if self.scan_job is not None:
            return
        holder = {}
        job = self.scan_job = FileJob(lambda: holder.__setitem__('devices', share.nearby_quickshare()))

        def finished(error):
            self.window.background_jobs.discard(job)
            if not alive(self):
                return
            self.scan_job = None
            devices = holder.get('devices')
            if error or devices is None:
                self.nearby.setText('Nie można przeskanować sieci (brak avahi-browse).')
                return
            others = [d for d in devices if d['name'] != share_device_name() and d['kind'] != 'komputer']
            if not others:
                self.nearby.setText('W pobliżu nie widać żadnego urządzenia Quick Share. Na telefonie otwórz Quick Share, '
                                    'ustaw widoczność „Wszyscy” i zostaw ekran odbierania otwarty; oba urządzenia muszą być w tej samej sieci Wi‑Fi.')
                return
            lines = []
            for device in others:
                if device['name'] and not device['hidden']:
                    lines.append(f'✔ {device["name"]} ({device["kind"]}, {device["address"]}) — widoczny, powinien pojawić się w oknie Quick Share po upuszczeniu plików.')
                else:
                    lines.append(f'⚠ {device["host"]} ({device["kind"]}, {device["address"]}) rozgłasza się jako ukryty, bez nazwy. '
                                 'Na telefonie: Quick Share → „Kto może udostępniać Ci” → „Wszyscy” (albo otwórz panel odbierania), a potem upuść pliki ponownie.')
            self.nearby.setText('\n'.join(lines))
        job.signals.finished.connect(finished)
        self.window.start_job(job)

    def start_lan(self, files):
        self.window.stop_share_server()
        addresses = share.lan_addresses()
        self.address_box.blockSignals(True)
        self.address_box.clear()
        for iface, address in addresses:
            self.address_box.addItem(f'{address}  ({iface or "domyślny"})', address)
        self.address_box.blockSignals(False)
        self.address_box.setVisible(len(addresses) > 1)
        self.lan_files = files
        try:
            self.window.share_server = share.ShareServer(files, address=addresses[0][1])
        except OSError as exc:
            self.status.setText(f'Nie można uruchomić serwera: {exc}')
            return
        self.show_lan()

    def change_address(self, index):
        server = self.window.share_server
        address = self.address_box.itemData(index)
        if server is not None and address and address != server.address:
            server.address = address
            self.show_lan()

    def show_lan(self):
        server = self.window.share_server
        self.url_edit.setText(server.url)
        self.url_edit.setCursorPosition(0)
        png = share.qr_png(server.url)
        if png:
            pixmap = QPixmap()
            pixmap.loadFromData(png)
            self.qr.setPixmap(pixmap)
            self.qr.show()
        else:
            self.qr.hide()
        self.lan_panel.show()
        self.update_lan_info()
        self.lan_timer.start(1500)
        self.status.setText('Udostępnianie działa, dopóki nie klikniesz „Zakończ udostępnianie” albo nie zamkniesz okna eksploratora.')

    def update_lan_info(self):
        server = self.window.share_server
        if server is None:
            self.lan_timer.stop()
            self.lan_panel.hide()
            return
        self.lan_info.setText(f'Pliki: {len(server.files)}   |   Pobrania: {server.downloads}   |   Adres IP: {server.address}')

    def stop_lan(self):
        self.window.stop_share_server()
        self.lan_timer.stop()
        self.lan_panel.hide()
        self.status.setText('Udostępnianie zakończone.')


class TitleBar(QWidget):
    """Frameless window title row: drag to move, double-click to maximise, accepts tabs dropped anywhere on it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event):
        window = self.window()
        if event.mimeData().hasFormat(TAB_MIME) and hasattr(window, 'receive_tab'):
            window.receive_tab(parse_tab_drop(event.mimeData()), -1)
            event.setDropAction(Qt.MoveAction)
            event.accept()

    def mousePressEvent(self, event):
        window = self.window()
        if event.button() == Qt.LeftButton and window is not None and window.windowHandle():
            window.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, event):
        window = self.window()
        if event.button() == Qt.LeftButton and hasattr(window, 'toggle_maximize'):
            window.toggle_maximize()


TAB_MIME = 'application/x-fileexplorer-tab'


def parse_tab_drop(mime):
    try:
        return json.loads(bytes(mime.data(TAB_MIME)).decode())
    except ValueError:
        return {}


class TabBar(QTabBar):
    """Movable tabs that can also be dragged into another window, or out of the window into a new one (Windows 11)."""
    DETACH_DISTANCE = 36
    SIDE_SLACK = 140

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setAcceptDrops(True)
        self.press_pos = None
        self.press_index = -1

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.press_pos = event.position().toPoint()
            self.press_index = self.tabAt(self.press_pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self.press_index >= 0 and event.buttons() & Qt.LeftButton:
            # Windows detaches when the tab leaves the strip; sideways there is slack so a tab can be pushed to the ends.
            if not self.rect().adjusted(-self.SIDE_SLACK, -self.DETACH_DISTANCE, self.SIDE_SLACK, self.DETACH_DISTANCE).contains(pos):
                self.press_index = -1
                self.window.drag_tab(self.currentIndex())
                return
            # Reorder by slot instead of QTabBar's floating tab, which Qt does not clamp to the strip.
            if (pos - self.press_pos).manhattanLength() >= QApplication.startDragDistance():
                target = self.tabAt(QPoint(min(max(pos.x(), 0), self.width() - 1), self.height() // 2))
                current = self.currentIndex()
                if target >= 0 and target != current:
                    self.moveTab(current, target)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.press_index = -1
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not event.mimeData().hasFormat(TAB_MIME):
            return
        self.window.receive_tab(parse_tab_drop(event.mimeData()), self.tabAt(event.position().toPoint()))
        event.setDropAction(Qt.MoveAction)
        event.accept()


class TabContainer(QSplitter):
    changed = Signal()
    selection_changed = Signal()

    def __init__(self, window, location='home'):
        super().__init__(Qt.Horizontal)
        self.window = window
        self.setHandleWidth(1)
        self.left_page = Page(window, location)
        self.right_page = None
        self.addWidget(self.left_page)
        self._active_page = self.left_page
        
        self.left_page.changed.connect(self.changed)
        self.left_page.selection_changed.connect(self.selection_changed)
        self.install_filters(self.left_page)

    def install_filters(self, page):
        # FocusIn belongs to the view itself; clicks can belong to any control on a virtual page.
        for widget in (page, *page.findChildren(QWidget)):
            widget.removeEventFilter(self)
            widget.installEventFilter(self)
        
    @property
    def location(self):
        return self._active_page.location
        
    @property
    def active_page(self):
        return self._active_page

    @staticmethod
    def mark_active(page, active):
        page.setProperty('activePane', active)
        for view in (page.tree, page.grid, page.gallery_grid):
            if view is None:
                continue
            view.style().unpolish(view)
            view.style().polish(view)

    def set_active_page(self, page):
        if self._active_page != page:
            if self.right_page:
                self.mark_active(self.left_page, page is self.left_page)
                self.mark_active(self.right_page, page is self.right_page)
            self._active_page = page
            self.window.sync()

    def toggle_split(self):
        if self.right_page:
            self.right_page.stop_search(show_view=False)
            self.right_page.deleteLater()
            self.right_page = None
            self._active_page = self.left_page
            self.mark_active(self.left_page, False)
            self.window.sync()
        else:
            self.right_page = Page(self.window, self.left_page.location)
            self.addWidget(self.right_page)
            self.right_page.changed.connect(self.changed)
            self.right_page.selection_changed.connect(self.selection_changed)
            self.install_filters(self.right_page)
            self.set_active_page(self.right_page)
            self.right_page.tree.setFocus()
        self.window.split_button.setChecked(self.right_page is not None)

    def eventFilter(self, obj, event):
        if self.right_page and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            for page in (self.left_page, self.right_page):
                widget = obj
                while widget is not None and widget is not page:
                    widget = widget.parentWidget()
                if widget is page:
                    self.set_active_page(page)
                    break
        return super().eventFilter(obj, event)


class Explorer(QMainWindow):
    def __init__(self, initial='home', restore_session=False):
        super().__init__()
        self.setWindowTitle('Eksplorator plików')
        self.setWindowIcon(icon('folder'))
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumSize(960, 620)
        self.resize(1280, 820)
        self.settings = QSettings('Damian', 'FileExplorer')
        self.busy = False
        self.job = None
        self.undo_stack = []
        self.redo_stack = []
        self.background_jobs = set()
        self.service_menu_cache = ()
        self.service_menu_pending = False
        self.service_menu_last_scan = 0.0
        self.device_cache = ()
        self.device_scan_pending = False
        self.device_scan_retry = False
        self.device_scan_generation = 0
        self.device_last_scan = 0.0
        self.device_discovery_error = ''
        self.closed_tabs = []
        self.app_cache = {}
        self.app_pending = set()
        self.space_cache = {}
        self.recent_cache = (None, [])
        self.share_server = None
        self.last_crumbs = None
        self.details_key = None
        self.provider = Icons()
        self.model = FileModel(self)
        self.model.setIconProvider(self.provider)
        self.model.setReadOnly(False)  # inline rename through the view; drops are handled by TransferJob
        self.model.fileRenamed.connect(self.file_renamed)
        self.drop_cache = {}
        self.drop_hint = None
        self.show_hidden = self.settings.value('show_hidden', False, type=bool)
        self.update_filter()
        self.model.setRootPath('/')
        # Model signals fire for every watched directory of every tab, so coalesce the status refresh.
        self.status_timer = QTimer(self)
        self.status_timer.setSingleShot(True)
        self.status_timer.setInterval(60)
        self.status_timer.timeout.connect(self.update_status)
        self.model.directoryLoaded.connect(lambda *_: self.schedule_status())
        self.model.rowsInserted.connect(lambda *_: self.schedule_status())
        self.model.rowsRemoved.connect(lambda *_: self.schedule_status())
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(120)
        self.search_timer.timeout.connect(lambda: self.page.set_search(self.search_edit.text()) if self.page else None)
        shell = QWidget()
        shell.setObjectName('shell')
        self.setCentralWidget(shell)
        layout = box(shell, vertical=True, margins=(1, 1, 1, 1))
        titlebar = TitleBar()
        titlebar.setObjectName('titlebar')
        titlebar.setFixedHeight(44)
        title_layout = box(titlebar, margins=(10, 0, 0, 0), spacing=3)
        self.tabs = TabBar(self)
        self.tabs.setExpanding(False)
        self.tabs.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # exactly as wide as its tabs: nothing to park a dragged tab in
        self.tab_drop_internal = False
        self.tabs.setMovable(True)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setMaximumWidth(900)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.tabs.tabMoved.connect(self.tab_moved)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self.tab_context_menu)
        self.tabs.installEventFilter(self)
        QApplication.instance().installEventFilter(self)  # mouse buttons 4/5 anywhere in the window
        title_layout.addWidget(self.tabs)
        title_layout.addWidget(tool('plus', 'Nowa karta (Ctrl+T)', lambda: self.add_tab()))
        title_layout.addStretch()
        for name, tip, callback in [('minimize', 'Minimalizuj', self.showMinimized), ('maximize', 'Maksymalizuj', self.toggle_maximize), ('close', 'Zamknij', self.close)]:
            button = tool(name, tip, callback)
            button.setFixedSize(46, 42)
            if name == 'close':
                button.setObjectName('windowClose')
            elif name == 'maximize':
                self.max_button = button
            title_layout.addWidget(button)
        layout.addWidget(titlebar)
        navigation = QWidget()
        navigation.setObjectName('navigation')
        navigation.setFixedHeight(60)
        nav_layout = box(navigation, margins=(12, 8, 16, 8), spacing=8)
        self.back_button = tool('back', 'Wstecz (Alt+←)', lambda: self.page.travel(-1))
        self.forward_button = tool('forward', 'Dalej (Alt+→)', lambda: self.page.travel(1))
        for button, direction in ((self.back_button, -1), (self.forward_button, 1)):
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda pos, b=button, d=direction: self.history_menu(b, pos, d))
        self.up_button = tool('up', 'W górę (Alt+↑)', self.go_up)
        for button in (self.back_button, self.forward_button, self.up_button, tool('refresh', 'Odśwież (F5)', self.refresh)):
            nav_layout.addWidget(button)
        nav_layout.addSpacing(8)
        self.address = QWidget()
        self.address.setObjectName('addressBox')
        self.address.setFixedHeight(35)
        self.address_layout = box(self.address, margins=(8, 0, 5, 0), spacing=3)
        self.breadcrumbs = QWidget()
        self.crumb_layout = box(self.breadcrumbs, spacing=1)
        self.address_layout.addWidget(self.breadcrumbs, 1)
        self.address_edit = QLineEdit()
        self.address_edit.setObjectName('addressEdit')
        self.address_edit.setAccessibleName('Adres folderu')
        self.address_edit.returnPressed.connect(self.submit_address)
        self.address_edit.editingFinished.connect(self.finish_address)
        self.address_edit.hide()
        completer = QCompleter(self.model, self)  # shares the cache and watcher thread of the main model
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setMaxVisibleItems(12)
        self.address_edit.setCompleter(completer)
        self.address_layout.addWidget(self.address_edit, 1)
        edit = tool('down', 'Edytuj adres (Ctrl+L)', self.edit_address)
        edit.setFixedSize(25, 28)
        self.address_layout.addWidget(edit)
        nav_layout.addWidget(self.address, 1)
        nav_layout.addSpacing(8)
        search_box = QWidget()
        search_box.setObjectName('searchBox')
        search_box.setFixedSize(410, 35)
        search_layout = box(search_box, margins=(8, 0, 8, 0), spacing=5)
        self.search_mode_combo = QComboBox()
        self.search_mode_combo.setAccessibleName('Szukaj w')
        self.search_mode_combo.setMaximumWidth(108)
        for title, mode in [('Nazwy', 'name'), ('Treść', 'content'), ('Tagi', 'tags'), ('Metadane', 'metadata'), ('Wszystko', 'all')]:
            self.search_mode_combo.addItem(title, mode)
        self.search_mode_combo.currentIndexChanged.connect(self.change_search_mode)
        search_layout.addWidget(self.search_mode_combo)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName('searchEdit')
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName('Wyszukaj w bieżącym folderze')
        self.search_edit.setToolTip('Treść: pliki tekstowe i indeks Baloo. Metadane: tag:projekt, rating:>=5, mimetype:image/*, size:>10MB, modified:>=2026-01-01.')
        self.search_edit.textChanged.connect(lambda text: self.search_timer.start())
        search_layout.addWidget(self.search_edit, 1)
        search_icon = QLabel()
        search_icon.setPixmap(icon('search', '#777').pixmap(17, 17))
        search_layout.addWidget(search_icon)
        self.search_options_button = tool('down', 'Zapisane wyszukiwania i opcje', self.search_options_menu)
        self.search_options_button.setFixedSize(23, 28)
        search_layout.addWidget(self.search_options_button)
        nav_layout.addWidget(search_box)
        layout.addWidget(navigation)
        commandbar = QWidget()
        commandbar.setObjectName('commandbar')
        commandbar.setFixedHeight(53)
        commands = box(commandbar, margins=(16, 6, 16, 6), spacing=10)
        self.new_button = tool('plus', 'Nowy', text='Nowy', arrow=True)
        self.new_button.clicked.connect(self.new_menu)
        commands.addWidget(self.new_button)
        self.separator(commands)
        self.file_buttons = {}
        for kind, tip, callback in [('cut', 'Wytnij (Ctrl+X)', lambda: self.copy(True)), ('copy', 'Kopiuj (Ctrl+C)', self.copy), ('paste', 'Wklej (Ctrl+V)', self.paste), ('rename', 'Zmień nazwę (F2)', self.rename), ('share', 'Udostępnij (Ctrl+Shift+S)', self.share), ('path', 'Kopiuj ścieżkę (Ctrl+Alt+C)', self.copy_path), ('trash', 'Przenieś do kosza (Delete)', self.trash)]:
            button = tool(kind, tip, callback)
            self.file_buttons[kind] = button
            commands.addWidget(button)
        self.separator(commands)
        self.sort_button = tool('sort', 'Sortuj', text='Sortuj', arrow=True)
        self.sort_button.clicked.connect(self.sort_menu)
        commands.addWidget(self.sort_button)
        self.view_button = tool('view', 'Wyświetl', text='Wyświetl', arrow=True)
        self.view_button.clicked.connect(self.view_menu)
        commands.addWidget(self.view_button)
        self.more_button = tool('more', 'Więcej opcji', self.more_menu)
        commands.addWidget(self.more_button)
        commands.addStretch()
        self.filter_box = QWidget()
        filter_layout = box(self.filter_box, margins=(0, 0, 0, 0), spacing=5)
        filter_layout.addWidget(label('Filtr:'))
        self.filter_edit = QLineEdit()
        self.filter_edit.setObjectName('filterEdit')
        self.filter_edit.setPlaceholderText('np. *.jpg')
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.setFixedWidth(120)
        self.filter_edit.textChanged.connect(self.apply_type_filter)
        filter_layout.addWidget(self.filter_edit)
        self.filter_box.hide()
        commands.addWidget(self.filter_box)
        
        self.split_button = tool('split', 'Widok podzielony (F3)', self.toggle_split_view, text='Podziel')
        self.split_button.setCheckable(True)
        commands.addWidget(self.split_button)
        self.terminal_button = tool('terminal', 'Wbudowany terminal (F4)', self.toggle_terminal, text='Terminal')
        self.terminal_button.setCheckable(True)
        commands.addWidget(self.terminal_button)
        self.details_button = tool('details', 'Panel szczegółów (Alt+Shift+P)', self.toggle_details, text='Szczegóły')
        self.details_button.setCheckable(True)
        commands.addWidget(self.details_button)
        self.preview_button = tool('eye', 'Panel podglądu (Alt+P)', self.toggle_preview)
        self.preview_button.setCheckable(True)
        commands.addWidget(self.preview_button)
        layout.addWidget(commandbar)
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setMinimumWidth(175)
        sidebar.setMaximumWidth(300)
        self.sidebar_layout = box(sidebar, vertical=True, margins=(6, 10, 8, 8), spacing=3)
        self.nav_buttons = []
        self.build_sidebar()
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setObjectName('sidebarScroll')
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setMinimumWidth(190)
        sidebar_scroll.setMaximumWidth(320)
        sidebar_scroll.setWidget(sidebar)
        self.splitter.addWidget(sidebar_scroll)
        self.pages = QStackedWidget()
        self.splitter.addWidget(self.pages)
        self.details = QWidget()
        self.details.setObjectName('detailsPane')
        self.details.setMinimumWidth(240)
        self.details.setMaximumWidth(320)
        self.details_layout = box(self.details, vertical=True, margins=(24, 24, 24, 24), spacing=18)
        self.splitter.addWidget(self.details)
        self.details.hide()
        self.preview = PreviewPanel(TEXT_SUFFIXES, self)
        self.splitter.addWidget(self.preview)
        self.preview.hide()
        self.splitter.setSizes([205, 755, 260, 320])
        
        self.main_v_splitter = QSplitter(Qt.Vertical)
        self.main_v_splitter.addWidget(self.splitter)
        
        self.terminal = TerminalWidget(self)
        self.terminal.hide()
        self.main_v_splitter.addWidget(self.terminal)
        self.main_v_splitter.setSizes([800, 200])
        
        layout.addWidget(self.main_v_splitter, 1)
        
        status = QWidget()
        status.setObjectName('statusbar')
        status.setFixedHeight(29)
        status_layout = box(status, margins=(16, 0, 4, 0), spacing=1)
        self.status_text = label('', 'statusText')
        status_layout.addWidget(self.status_text)
        status_layout.addStretch()
        self.space_text = label('', 'statusText')
        status_layout.addWidget(self.space_text)
        status_layout.addSpacing(12)
        for name, tip, mode in [('view', 'Szczegóły', 'details'), ('grid', 'Duże ikony', 'large')]:
            button = tool(name, tip, lambda checked=False, m=mode: self.page.set_mode(m))
            button.setFixedSize(27, 25)
            button.setIconSize(QSize(16, 16))
            status_layout.addWidget(button)
        grip = QSizeGrip(self)
        grip.setFixedSize(14, 20)
        status_layout.addWidget(grip)
        layout.addWidget(status)
        restored = restore_session and self.restore_tabs()
        if not restored:
            self.add_tab(initial)
        self.refresh_mounts()
        self.refresh_service_menus()
        self.mount_timer = QTimer(self)
        self.mount_timer.timeout.connect(self.refresh_mounts)
        self.mount_timer.start(5000)
        shortcuts = {
            'F4': self.toggle_terminal, 'F3': self.toggle_split_view, 'Ctrl+H': lambda: self.toggle_hidden(not self.show_hidden),
            'Ctrl+T': lambda: self.add_tab(), 'Ctrl+W': lambda: self.close_tab(self.tabs.currentIndex()),
            'Ctrl+Shift+T': self.reopen_tab, 'Ctrl+Tab': lambda: self.cycle_tab(1), 'Ctrl+Shift+Tab': lambda: self.cycle_tab(-1),
            'Alt+Left': lambda: self.page.travel(-1), 'Alt+Right': lambda: self.page.travel(1), 'Alt+Up': self.go_up,
            'Backspace': self.backspace, 'Ctrl+L': self.edit_address, 'Alt+D': self.edit_address,
            'Ctrl+Shift+L': self.connect_to_server,
            'Ctrl+F': self.search_edit.setFocus, 'Ctrl+I': self.toggle_type_filter, 'F5': self.refresh, 'F2': self.rename,
            'Delete': self.trash, 'Shift+Delete': self.delete_permanently, 'Ctrl+C': self.copy, 'Ctrl+X': lambda: self.copy(True),
            'Ctrl+V': self.paste, 'Ctrl+Z': self.undo, 'Ctrl+Alt+C': self.copy_path, 'Ctrl+D': self.duplicate,
            'Ctrl+Shift+C': self.copy_path, 'Ctrl+E': self.search_edit.setFocus, 'Ctrl+N': self.open_new_window,
            'F11': self.toggle_fullscreen, 'Ctrl+Y': self.redo, 'Ctrl+Shift+Z': self.redo,
            'Ctrl+A': self.select_all, 'Ctrl+Shift+A': lambda: self.page.invert_selection(),
            'Ctrl+Shift+N': self.new_folder, 'Ctrl+Shift+S': self.share, 'Alt+Return': self.properties, 'Alt+Shift+P': self.toggle_details,
            'Alt+P': self.toggle_preview,
            'Return': self.open_selected, 'Escape': self.escape,
        }
        for number, mode in enumerate(VIEW_MODES, 1):
            shortcuts[f'Ctrl+Shift+{number}'] = lambda m=mode: self.page.set_mode(m)
        for key, callback in shortcuts.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
        self.clipboard_connection = QApplication.clipboard().dataChanged.connect(self.schedule_status)
        geometry = self.settings.value('geometry')
        if geometry:
            self.restoreGeometry(geometry)
        if self.settings.value('maximized', False, type=bool):
            self.showMaximized()
        sizes = self.settings.value('splitter_sizes')
        if sizes:
            try:
                self.splitter.setSizes([int(value) for value in sizes])
            except (TypeError, ValueError):
                pass
        if self.settings.value('details_visible', False, type=bool):
            self.toggle_details()
        if self.settings.value('preview_visible', False, type=bool):
            self.toggle_preview()

    @property
    def page(self):
        container = self.pages.currentWidget() if hasattr(self, 'pages') else None
        return container.active_page if container else None

    def separator(self, layout):
        widget = QFrame()
        widget.setObjectName('separator')
        layout.addWidget(widget)

    def side_divider(self, layout):
        layout.addSpacing(8)
        divider = QFrame()
        divider.setObjectName('navDivider')
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(8)

    def build_sidebar(self):
        while self.sidebar_layout.count():
            item = self.sidebar_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.nav_buttons = []
        for name, kind, destination in [('Główna', 'home', 'home'), ('Galeria', 'gallery', 'gallery')]:
            self.sidebar_layout.addWidget(self.nav_item(name, kind, destination))
        self.side_divider(self.sidebar_layout)
        self.sidebar_layout.addWidget(self.nav_item('Katalog domowy', 'folder', str(HOME), arrow=True))
        self.side_divider(self.sidebar_layout)
        for name, kind, path in FOLDERS:
            self.sidebar_layout.addWidget(self.nav_item(name, kind, str(path), pinned=True))
            
        favs = self.favorites()
        if favs:
            self.side_divider(self.sidebar_layout)
            for path in favs:
                self.sidebar_layout.addWidget(self.nav_item(path.name, 'folder', str(path), pinned=True, removable=True))
                
        self.side_divider(self.sidebar_layout)
        self.sidebar_layout.addWidget(self.nav_item('Ten komputer', 'pc', 'computer', arrow=True))
        self.mount_widget = QWidget()
        self.mount_layout = box(self.mount_widget, vertical=True, spacing=3)
        self.mount_signature = None
        self.sidebar_layout.addWidget(self.mount_widget)
        self.side_divider(self.sidebar_layout)
        self.sidebar_layout.addWidget(self.nav_item('Kosz', 'trash', 'trash'))
        self.sidebar_layout.addWidget(self.nav_item('Sieć', 'network', 'remote:/'))
        self.sidebar_layout.addStretch()

    def nav_item(self, name, kind, destination, pinned=False, arrow=False, indent=False, removable=False,
                 device_path=None):
        button = QPushButton()
        button.setObjectName('navItem')
        button.setFixedHeight(36)
        button.setAccessibleName(name)
        row = box(button, margins=(0, 0, 7, 0), spacing=10)
        mark = QWidget()
        mark.setObjectName('navMark')
        mark.setFixedSize(3, 16)
        row.addWidget(mark)
        chevron = QLabel()
        chevron.setFixedWidth(13 if not indent else 26)
        if arrow:
            chevron.setPixmap(icon('right', '#777').pixmap(10, 10))
        row.addWidget(chevron)
        picture = QLabel()
        picture.setPixmap(icon(kind).pixmap(18, 18))
        row.addWidget(picture)
        row.addWidget(label(name, 'navLabel'), 1)
        if pinned:
            pin = QLabel()
            pin.setPixmap(icon('pin', '#888').pixmap(11, 11))
            row.addWidget(pin)
        for child in button.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        if device_path and destination.startswith('device:'):
            button.clicked.connect(lambda: self.activate_device(device_path, button))
        else:
            button.clicked.connect(lambda: self.page.navigate(destination))
        if device_path:
            button.setProperty('device_path', device_path)
        button.setToolTip(destination if destination not in VIRTUAL else name)
        if destination not in VIRTUAL and QUrl(destination).scheme() == '':
            button.setProperty('destination', destination)
            button.setAcceptDrops(True)
            button.installEventFilter(self)
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda pos: self.nav_context(button, pos, destination))
        self.nav_buttons.append((button, destination))
        return button

    def menu(self):
        """A rounded, self-deleting Windows 11 style menu; show it with popup(), not exec()."""
        menu = Menu(self)
        menu.setAttribute(Qt.WA_DeleteOnClose)
        return menu

    def show_drop_hint(self, action, directory, global_pos):
        if self.drop_hint is None:
            self.drop_hint = DropHint(self)
        self.drop_hint.show_hint(action, directory, global_pos)

    def hide_drop_hint(self):
        if self.drop_hint is not None and self.drop_hint.isVisible():
            self.drop_hint.hide()

    def all_pages(self):
        for i in range(self.pages.count()):
            container = self.pages.widget(i)
            yield container.left_page
            if container.right_page:
                yield container.right_page

    def select_successor(self, page, path):
        """Runs from a job's `after`: refresh() follows and clears the selection, so select on the next event loop turn."""
        if path and alive(page):
            QTimer.singleShot(0, lambda: alive(page) and page.select_paths([path]))

    def select_results(self, targets):
        """After a transfer, select the new entries in every page showing their folder (Windows does this)."""
        by_dir = {}
        for target in targets:
            by_dir.setdefault(str(Path(target).parent), []).append(target)
        for page in self.all_pages():
            paths = by_dir.get(page.current_dir())
            if paths:
                page.select_paths(paths)

    def drop_action(self, sources, directory, modifiers):
        """Windows rules: Ctrl copies, Shift moves, Alt links, Ctrl+Shift asks; otherwise move within a volume, copy across."""
        if modifiers & Qt.ControlModifier and modifiers & Qt.ShiftModifier:
            return 'menu'
        if modifiers & Qt.AltModifier:
            return 'link'
        if modifiers & Qt.ControlModifier:
            return 'copy'
        if modifiers & Qt.ShiftModifier:
            return 'move'
        key = (directory, tuple(map(str, sources)))
        action = self.drop_cache.get(key)
        if action is None:
            try:
                device = os.stat(directory).st_dev
                action = 'move' if all(os.lstat(source).st_dev == device for source in sources) else 'copy'
            except OSError:
                action = 'copy'
            if len(self.drop_cache) > 32:
                self.drop_cache.clear()
            self.drop_cache[key] = action
        return action

    def drop_files(self, sources, directory, event, global_pos):
        action = self.drop_action(sources, directory, event.modifiers())
        if action == 'menu':
            self.drop_menu(sources, directory, global_pos)
        elif action == 'link':
            self.make_symlinks(sources, directory)
        else:
            self.run_transfer(sources, directory, cut=action == 'move')

    def file_renamed(self, directory, old_name, new_name):
        self.record(('rename', [(Path(directory) / old_name, Path(directory) / new_name)]))
        self.status_text.setText(f'Zmieniono nazwę: {new_name}')

    def drop_menu(self, sources, directory, global_pos):
        menu = self.menu()
        menu.addAction(icon('copy'), 'Kopiuj tutaj', lambda: self.run_transfer(sources, directory, cut=False))
        menu.addAction(icon('cut'), 'Przenieś tutaj', lambda: self.run_transfer(sources, directory, cut=True))
        menu.addSeparator()
        menu.addAction(icon('link'), 'Utwórz dowiązania symboliczne', lambda: self.make_symlinks(sources, directory))
        menu.addSeparator()
        menu.addAction('Anuluj')
        menu.popup(global_pos)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick) \
                and event.button() in (Qt.BackButton, Qt.ForwardButton):
            # Mouse buttons 4/5 navigate history like in Windows Explorer; swallow all phases so views do not react.
            if not isinstance(obj, QWidget) or obj.window() is not self:
                return False
            if kind == QEvent.Type.MouseButtonPress and self.page and not self.busy:
                self.page.travel(-1 if event.button() == Qt.BackButton else 1)
            return True
        if obj is self.tabs:
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MiddleButton:
                index = self.tabs.tabAt(event.position().toPoint())
                if index >= 0:
                    self.close_tab(index)
                    return True
            elif kind == QEvent.Type.MouseButtonDblClick and self.tabs.tabAt(event.position().toPoint()) < 0:
                self.add_tab()
                return True
        elif kind == QEvent.Type.DragLeave and obj.property('destination'):
            self.hide_drop_hint()
        elif kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            destination = obj.property('destination')
            mime = event.mimeData()
            if not destination or not mime or not mime.hasUrls() or not os.path.isdir(destination):
                return False
            sources = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
            if kind == QEvent.Type.Drop:
                self.hide_drop_hint()
                if sources:
                    self.drop_files(sources, destination, event, obj.mapToGlobal(event.position().toPoint()))
                event.setDropAction(Qt.CopyAction)
                event.accept()
                return True
            if sources:
                action = self.drop_action(sources, destination, event.modifiers())
                event.setDropAction(Qt.LinkAction if action == 'link' else Qt.MoveAction if action == 'move' else Qt.CopyAction)
                event.accept()
                if kind == QEvent.Type.DragMove:
                    self.show_drop_hint(action, destination, obj.mapToGlobal(event.position().toPoint()))
            else:
                event.acceptProposedAction()
            return True
        return super().eventFilter(obj, event)

    def nav_context(self, button, pos, destination):
        device = self.device_entry(button.property('device_path'), destination)
        if device:
            self.device_context(device.path, button.mapToGlobal(pos))
            return
        menu = self.menu()
        menu.addAction(icon('plus'), 'Otwórz w nowej karcie', lambda: self.add_tab(destination))
        if QUrl(destination).scheme() in REMOTE_SCHEMES:
            menu.addAction(icon('network'), 'Połącz z serwerem…\tCtrl+Shift+L', self.connect_to_server)
        if os.path.isdir(destination):
            menu.addAction(icon('terminal'), 'Otwórz w terminalu', lambda: self.open_terminal(destination))
            if destination in {str(f) for f in self.favorites()}:
                menu.addSeparator()
                menu.addAction(icon('star'), 'Usuń z ulubionych', lambda: self.set_favorites([destination], remove=True))
        menu.popup(button.mapToGlobal(pos))

    def history_menu(self, button, pos, direction):
        page = self.page
        if not page:
            return
        if direction < 0:
            entries = [(i, page.history[i]) for i in range(page.history_index - 1, -1, -1)]
        else:
            entries = [(i, page.history[i]) for i in range(page.history_index + 1, len(page.history))]
        if not entries:
            return
        menu = self.menu()
        for index, location in entries[:15]:
            menu.addAction(icon_for(location), title_for(location), lambda i=index: page.travel(i - page.history_index))
        menu.popup(button.mapToGlobal(pos))

    def device_entry(self, path=None, mountpoint=None):
        return next((entry for entry in self.device_cache
                     if entry.path == path or (mountpoint and mountpoint in entry.mountpoints)), None)

    def filesystem_operations_busy(self):
        app = QApplication.instance()
        return self.busy or bool(app and any(isinstance(window, Explorer) and window.busy
                                            for window in app.topLevelWidgets()))

    def activate_device(self, path, button):
        device = self.device_entry(path)
        if device and device.mountpoints:
            self.page.navigate(device.mountpoint)
        elif device and not device.can_mount:
            self.device_context(path, button.mapToGlobal(QPoint(0, button.height())))
        else:
            self.device_operation(path, 'mount')

    def update_device_controls(self):
        busy = self.filesystem_operations_busy()
        for button in self.findChildren(QPushButton):
            operation = button.property('device_operation')
            if not operation:
                continue
            device = self.device_entry(button.property('device_path'))
            allowed = bool(device and {'mount': device.can_mount, 'unmount': device.can_unmount,
                                       'power-off': device.can_power_off}.get(operation))
            button.setEnabled(allowed and not busy)

    def add_device_actions(self, menu, device):
        busy = self.filesystem_operations_busy()
        if not device.mountpoints:
            action = menu.addAction(icon('drive'), 'Zamontuj i otwórz',
                                    lambda: self.device_operation(device.path, 'mount'))
            action.setEnabled(device.can_mount and not busy)
        elif not device.protected:
            action = menu.addAction(icon('drive'), 'Odmontuj',
                                    lambda: self.device_operation(device.path, 'unmount'))
            action.setEnabled(device.can_unmount and not busy)
        if device.can_power_off:
            action = menu.addAction(icon('drive'), 'Bezpiecznie usuń nośnik',
                                    lambda: self.device_operation(device.path, 'power-off'))
            action.setEnabled(not busy)

    def device_context(self, path, global_pos):
        device = self.device_entry(path)
        if not device:
            return
        menu = self.menu()
        if device.mountpoints:
            destination = device.mountpoint
            menu.addAction(icon('plus'), 'Otwórz w nowej karcie', lambda: self.add_tab(destination))
            menu.addAction(icon('terminal'), 'Otwórz w terminalu', lambda: self.open_terminal(destination))
            if device.can_unmount or device.can_power_off:
                menu.addSeparator()
        self.add_device_actions(menu, device)
        menu.popup(global_pos)

    def device_operation(self, path, action):
        if self.filesystem_operations_busy():
            self.status_text.setText('Poczekaj na zakończenie operacji na plikach przed zmianą stanu nośnika.')
            return
        device = self.device_entry(path)
        if not device:
            self.scan_devices(force=True)
            self.error('Nośnik jest już niedostępny. Lista urządzeń zostanie odświeżona.')
            return
        affected = [entry for entry in self.device_cache
                    if entry.path == path or (action == 'power-off' and entry.disk_path == device.disk_path)]
        old_mountpoints = tuple(mount for entry in affected for mount in entry.mountpoints)
        holder = {}
        page = self.page
        self.device_scan_generation += 1

        def operation():
            holder['devices'] = devices.operate(path, action)

        def after():
            self.device_cache = holder['devices']
            self.device_discovery_error = ''
            self.refresh_mounts(scan=False)
            if action == 'mount':
                mounted = self.device_entry(path)
                if mounted and mounted.mountpoints and alive(page):
                    page.navigate(mounted.mountpoint)
            else:
                for pane in self.all_pages():
                    if pane.location not in VIRTUAL and not QUrl(pane.location).scheme() and any(
                            Path(pane.location).is_relative_to(Path(mount)) for mount in old_mountpoints):
                        pane.navigate('computer')
            self.status_text.setText({'mount': 'Nośnik został zamontowany.',
                                      'unmount': 'Nośnik został odmontowany.',
                                      'power-off': 'Można bezpiecznie odłączyć nośnik.'}[action])

        self.run_job(operation, after, on_error=lambda: self.scan_devices(force=True))

    def scan_devices(self, force=False):
        if self.device_scan_pending:
            self.device_scan_retry = self.device_scan_retry or force
            return
        if not force and time.monotonic() - self.device_last_scan < 4:
            return
        self.device_scan_pending = True
        self.device_last_scan = time.monotonic()
        generation = self.device_scan_generation
        holder = {}
        job = FileJob(lambda: holder.__setitem__('devices', devices.discover()))

        def finished(error):
            self.background_jobs.discard(job)
            if not alive(self):
                return
            self.device_scan_pending = False
            if generation == self.device_scan_generation:
                if error:
                    self.device_discovery_error = error
                else:
                    self.device_cache = holder['devices']
                    self.device_discovery_error = ''
                self.refresh_mounts(scan=False)
            retry = self.device_scan_retry or generation != self.device_scan_generation
            self.device_scan_retry = False
            if retry:
                self.scan_devices(force=True)

        job.signals.finished.connect(finished)
        self.start_job(job)

    def refresh_mounts(self, scan=True):
        if scan:
            self.scan_devices()
        self.update_device_controls()
        volumes = mounted_volumes()
        signature = (tuple((v.rootPath(), volume_name(v)) for v in volumes),
                     self.device_cache, self.device_discovery_error)
        if signature == self.mount_signature:
            return
        self.mount_signature = signature
        old_buttons = set(self.mount_widget.findChildren(QPushButton))
        self.nav_buttons = [(b, dest) for b, dest in self.nav_buttons if b not in old_buttons]
        while self.mount_layout.count():
            item = self.mount_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        for volume in volumes:
            entry = self.device_entry(mountpoint=volume.rootPath())
            self.mount_layout.addWidget(self.nav_item(volume_name(volume), 'drive', volume.rootPath(), indent=True,
                                                     device_path=entry.path if entry else None))
        known_mounts = {volume.rootPath() for volume in volumes}
        for entry in self.device_cache:
            if any(mount in known_mounts for mount in entry.mountpoints):
                continue
            destination = entry.mountpoint if entry.mountpoints else 'device:' + entry.path
            name = entry.title if entry.mountpoints else f'{entry.title} (niezamontowany)'
            button = self.nav_item(name, 'drive', destination, indent=True, device_path=entry.path)
            button.setToolTip(f'{entry.path}\n{entry.filesystem or "Brak systemu plików"} · {size_text(entry.size)}')
            if not entry.mountpoints and not entry.can_mount:
                button.setEnabled(entry.can_power_off)
            self.mount_layout.addWidget(button)
        if self.page:
            for page in self.all_pages():
                if page.location == 'computer':
                    page.build_computer()
            self.sync()

    def toggle_terminal(self):
        visible = self.terminal.isHidden()
        if visible:
            directory = self.page.current_dir() if self.page else None
            if directory:
                self.terminal.set_directory(directory)
        self.terminal.setVisible(visible)
        if hasattr(self, 'terminal_button'):
            self.terminal_button.setChecked(visible)
        if visible:
            self.terminal.input.setFocus()

    def open_terminal(self, directory=None):
        if not isinstance(directory, str):
            paths = self.page.selected_paths()
            directory = paths[0] if len(paths) == 1 and Path(paths[0]).is_dir() else self.page.current_dir() or str(HOME)
        try:
            subprocess.Popen(['/usr/bin/konsole', '--workdir', directory], start_new_session=True)
        except OSError as exc:
            self.error(exc)

    def open_new_window(self):
        """Ctrl+N: a second window on the current folder, like Windows."""
        page = self.page
        new_window(page.location if page else 'home')

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def toggle_split_view(self):
        container = self.pages.currentWidget() if hasattr(self, 'pages') else None
        if container:
            container.toggle_split()

    def compare_files(self, file1, file2):
        try:
            if shutil.which('kompare'):
                subprocess.Popen(['kompare', file1, file2], start_new_session=True)
            elif shutil.which('meld'):
                subprocess.Popen(['meld', file1, file2], start_new_session=True)
            else:
                self.error("Nie znaleziono narzędzia do porównywania (kompare ani meld). Zainstaluj jedno z nich.")
        except OSError as exc:
            self.error(exc)

    def open_with_dialog(self):
        paths = self.page.selected_paths()
        if not paths:
            return
        mime, default_id, application_ids = self.applications_for_paths(paths)
        dialog = OpenWithDialog(self, mime)
        accepted = dialog.exec() == QDialog.Accepted
        chosen, set_default, command = dialog.chosen_id, dialog.set_default, dialog.chosen_command
        dialog.deleteLater()
        if not accepted:
            return
        if command:
            try:
                subprocess.Popen([*shlex.split(command), *paths], start_new_session=True, cwd=self.page.current_dir() or str(HOME))
            except (OSError, ValueError) as exc:
                self.error(f'Nie udało się uruchomić polecenia: {exc}')
        elif chosen:
            self.open_with_application(chosen)
            if set_default and mime:
                self.set_default_application(mime, chosen)

    def create_symlink(self):
        paths = self.page.selected_paths()
        directory = self.page.current_dir()
        if paths and directory and not self.busy:
            self.make_symlinks([Path(p) for p in paths], directory)

    def make_symlinks(self, sources, directory):
        created = []
        for source in sources:
            base = f'{source.name} — dowiązanie'
            link, number = Path(directory) / base, 1
            while os.path.lexists(link):
                number += 1
                link = Path(directory) / f'{base} ({number})'
            try:
                link.symlink_to(source)
                created.append(('copy', source, link))
            except OSError as exc:
                self.error(f'Nie można utworzyć dowiązania symbolicznego:\n{exc}')
                break
        if created:
            self.record(('transfer', created))
            self.select_results([link for _, _, link in created])

    def run_as_admin(self, directory=None):
        directory = directory if isinstance(directory, (str, Path)) else self.page.current_dir() or str(HOME)
        try:
            self.add_tab(network.administrator_location(directory))
        except (OSError, ValueError) as exc:
            self.error(f'Nie można otworzyć folderu w trybie administratora:\n{exc}')


    def reveal_file(self, path, page, after=None):
        page.set_search('')
        page.set_type_filter('')
        page.proxy.beginFilterChange()
        page.proxy.revealed_paths.add(str(path))
        page.proxy.endFilterChange()
        self.sync()
        timer = QTimer(page)
        attempts = 0
        def reveal():
            nonlocal attempts
            if not alive(page):
                return
            attempts += 1
            index = page.proxy.mapFromSource(self.model.index(path))
            if index.isValid() and index.parent() == page.tree.rootIndex():
                page.tree.selectionModel().setCurrentIndex(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
                page.tree.scrollTo(index)
                page.grid.scrollTo(index)
                timer.stop()
                timer.deleteLater()
                if after is not None:
                    QTimer.singleShot(0, self, after)
            elif attempts >= 40:
                timer.stop()
                timer.deleteLater()
        timer.timeout.connect(reveal)
        timer.start(50)

    def restore_tabs(self):
        locations = self.settings.value('session_tabs', [], type=list)
        opened = 0
        for location in locations:
            if location in VIRTUAL or QUrl(str(location)).scheme() in REMOTE_SCHEMES or os.path.isdir(str(location)):
                self.add_tab(str(location))
                opened += 1
        if opened:
            current = self.settings.value('session_index', 0, type=int)
            if 0 <= current < self.tabs.count():
                self.tabs.setCurrentIndex(current)
        return opened > 0

    def save_session(self):
        self.settings.setValue('session_tabs', [self.pages.widget(i).left_page.pending_remote or network.display_location(self.pages.widget(i).left_page.location)
                                               for i in range(self.pages.count())])
        self.settings.setValue('session_index', self.tabs.currentIndex())
        self.settings.setValue('geometry', self.saveGeometry())
        self.settings.setValue('maximized', self.isMaximized())
        self.settings.setValue('splitter_sizes', self.splitter.sizes())
        self.settings.setValue('details_visible', self.details.isVisible())
        self.settings.setValue('preview_visible', self.preview.isVisible())
        if self.page:
            self.settings.setValue('header_state', self.page.tree.header().saveState())

    def remember_sort(self, column, order):
        self.settings.setValue('sort_column', int(column))
        self.settings.setValue('sort_order', int(order == Qt.DescendingOrder))

    def reopen_tab(self):
        if self.closed_tabs:
            self.add_tab(self.closed_tabs.pop())

    def add_tab(self, location='home'):
        container = TabContainer(self, location)
        container.changed.connect(self.sync)
        container.selection_changed.connect(self.schedule_status)
        index = self.pages.addWidget(container)
        self.tabs.addTab(icon_for(container.location), title_for(container.location))
        close = tool('close', 'Zamknij kartę', lambda: self.close_tab(self.pages.indexOf(container)))
        close.setFixedSize(22, 22)
        close.setIconSize(QSize(12, 12))
        self.tabs.setTabButton(index, QTabBar.RightSide, close)
        self.tabs.setCurrentIndex(index)
        self.pages.setCurrentIndex(index)
        self.sync()

    def close_tab(self, index):
        if not 0 <= index < self.tabs.count():
            return
        if self.tabs.count() == 1:
            self.close()
            return
        page = self.pages.widget(index)
        if page.location in VIRTUAL or os.path.isdir(page.location):
            self.closed_tabs = self.closed_tabs[-9:] + [page.active_page.pending_remote or network.display_location(page.location)]
        for pane in (page.left_page, page.right_page):
            if pane is not None:
                pane.stop_search(show_view=False)
        self.tabs.blockSignals(True)
        self.tabs.removeTab(index)
        self.pages.removeWidget(page)
        page.deleteLater()
        self.tabs.blockSignals(False)
        self.pages.setCurrentIndex(self.tabs.currentIndex())
        self.sync()

    def tab_changed(self, index):
        if hasattr(self, 'pages') and index >= 0:
            self.pages.setCurrentIndex(index)
            self.sync()

    def cycle_tab(self, delta):
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + delta) % self.tabs.count())

    def tab_moved(self, from_index, to_index):
        widget = self.pages.widget(from_index)
        self.pages.removeWidget(widget)
        self.pages.insertWidget(to_index, widget)
        self.pages.setCurrentIndex(self.tabs.currentIndex())

    def drag_tab(self, index):
        """Drag a tab out of the strip: onto another window's strip it moves there, dropped elsewhere it becomes a new window."""
        container = self.pages.widget(index)
        if container is None:
            return
        location = container.active_page.location
        mime = QMimeData()
        mime.setData(TAB_MIME, json.dumps({'pid': os.getpid(), 'window': id(self), 'location': location}).encode())
        drag = QDrag(self.tabs)
        drag.setMimeData(mime)
        drag.setPixmap(self.tabs.grab(self.tabs.tabRect(index)))
        drag.setHotSpot(QPoint(24, 18))
        self.tab_drop_internal = False
        result = drag.exec(Qt.MoveAction | Qt.CopyAction, Qt.MoveAction)
        if not alive(self) or not alive(container):
            return
        if self.tab_drop_internal:
            self.tab_drop_internal = False
            return
        if result == Qt.MoveAction:
            self.close_tab(self.pages.indexOf(container))
        elif result == Qt.IgnoreAction and self.tabs.count() > 1:
            new_window(location, QCursor.pos() - QPoint(120, 20))
            self.close_tab(self.pages.indexOf(container))

    def receive_tab(self, data, target):
        """A tab dropped on this window's strip; `target` is the tab index under the cursor or -1 for the end."""
        if data.get('pid') == os.getpid() and data.get('window') == id(self):
            source = self.tabs.currentIndex()
            if 0 <= target != source:
                self.tabs.moveTab(source, target)
            self.tab_drop_internal = True
            return
        location = data.get('location') or 'home'
        self.add_tab(location)
        new = self.tabs.currentIndex()
        if 0 <= target < new:
            self.tabs.moveTab(new, target)
        self.raise_()
        self.activateWindow()

    def tab_context_menu(self, pos):
        index = self.tabs.tabAt(pos)
        if index < 0:
            return
        menu = self.menu()
        menu.addAction(icon('plus'), 'Nowa karta\tCtrl+T', lambda: self.add_tab())
        menu.addAction(icon('duplicate'), 'Duplikuj kartę', lambda: self.add_tab(self.pages.widget(index).location))
        if self.closed_tabs:
            menu.addAction(icon('undo'), 'Przywróć zamkniętą kartę\tCtrl+Shift+T', self.reopen_tab)
        menu.addSeparator()
        menu.addAction(icon('close'), 'Zamknij kartę\tCtrl+W', lambda: self.close_tab(index))
        if self.tabs.count() > 1:
            menu.addAction(icon('close'), 'Zamknij inne karty', lambda: self.close_other_tabs(index))
        menu.popup(self.tabs.mapToGlobal(pos))

    def close_other_tabs(self, keep):
        if not 0 <= keep < self.tabs.count():
            return
        self.tabs.blockSignals(True)
        indices = [i for i in range(self.tabs.count()) if i != keep]
        for i in sorted(indices, reverse=True):
            page = self.pages.widget(i)
            for pane in (page.left_page, page.right_page):
                if pane is not None:
                    pane.stop_search(show_view=False)
            self.tabs.removeTab(i)
            self.pages.removeWidget(page)
            page.deleteLater()
        self.tabs.blockSignals(False)
        self.tabs.setCurrentIndex(0)
        self.pages.setCurrentIndex(0)
        self.sync()


    def sync(self):
        page = self.page
        if not page:
            return
        index = self.pages.currentIndex()
        container = self.pages.currentWidget()
        self.split_button.setChecked(container.right_page is not None)
        self.tabs.setTabText(index, title_for(page.location))
        self.tabs.setTabIcon(index, icon_for(page.location))
        self.setWindowTitle(f'{title_for(page.location)} — Eksplorator plików')
        self.back_button.setEnabled(page.history_index > 0)
        self.forward_button.setEnabled(page.history_index < len(page.history) - 1)
        self.up_button.setEnabled(bool(page.current_dir()) and page.current_dir() != '/')
        self.search_edit.blockSignals(True)
        self.search_edit.setText(page.search)
        self.search_edit.setPlaceholderText(f'Przeszukaj: {title_for(page.location)}')
        self.search_edit.blockSignals(False)
        self.search_edit.setEnabled(page.location != 'computer')
        self.search_mode_combo.blockSignals(True)
        self.search_mode_combo.setCurrentIndex(max(0, self.search_mode_combo.findData(page.search_mode)))
        self.search_mode_combo.blockSignals(False)
        self.search_mode_combo.setEnabled(bool(page.current_dir()) and page.location != 'gallery')
        self.filter_edit.blockSignals(True)
        self.filter_edit.setText(page.type_filter)
        self.filter_edit.blockSignals(False)
        if page.type_filter and self.filter_box.isHidden():
            self.filter_box.show()
        self.make_breadcrumbs()
        accent = THEMES[QApplication.instance().property('theme')]['accent']
        for button, destination in self.nav_buttons:
            active = destination == page.location
            if button.property('active') != active:
                button.setProperty('active', active)
                button.style().unpolish(button)
                button.style().polish(button)
                mark = button.findChild(QWidget, 'navMark')
                mark.setStyleSheet(f'background: {accent}; border-radius: 2px;' if active else 'background: transparent;')
        if self.terminal.isVisible():
            directory = page.current_dir()
            if directory:
                self.terminal.set_directory(directory)
        self.update_status()

    def make_breadcrumbs(self):
        location = self.page.location
        theme = QApplication.instance().property('theme')
        if self.last_crumbs == (location, theme):
            return
        self.last_crumbs = (location, theme)
        while self.crumb_layout.count():
            item = self.crumb_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if location in VIRTUAL:
            crumbs = [(title_for(location), location)]
        elif network.breadcrumbs(location):
            crumbs = network.breadcrumbs(location)
        else:
            path = Path(location)
            if path.is_relative_to(HOME):
                crumbs = [('Folder osobisty', str(HOME))]
                base = HOME
                parts = path.relative_to(HOME).parts
            else:
                crumbs = [('Ten komputer', 'computer'), ('System plików', '/')]
                base = Path('/')
                parts = path.parts[1:]
            for part in parts:
                base = base / part
                crumbs.append((part, str(base)))
            if len(crumbs) > 4:
                crumbs = [crumbs[0], ('…', str(path.parent.parent))] + crumbs[-2:]
        picture = QLabel()
        picture.setPixmap(icon_for(location).pixmap(18, 18))
        self.crumb_layout.addWidget(picture)
        self.crumb_layout.addSpacing(6)
        previous = None
        for name, destination in crumbs:
            self.crumb_layout.addWidget(self.crumb_arrow(previous))
            button = QPushButton(name if len(name) < 25 else name[:22] + '…')
            button.setToolTip(network.display_location(destination))
            button.setFixedHeight(28)
            button.clicked.connect(lambda checked=False, p=destination: self.page.navigate(p))
            self.crumb_layout.addWidget(button)
            previous = destination
        self.crumb_layout.addWidget(self.crumb_arrow(previous))
        blank = QPushButton()
        blank.setFixedHeight(28)
        blank.clicked.connect(self.edit_address)
        self.crumb_layout.addWidget(blank, 1)

    def crumb_arrow(self, destination):
        """The chevron between two path segments; over a real folder it drops down its subfolders, like Windows."""
        if not destination or destination in VIRTUAL or not os.path.isdir(destination):
            arrow = QLabel()
            arrow.setPixmap(icon('right', '#888').pixmap(12, 12))
            return arrow
        button = QToolButton()
        button.setObjectName('crumbArrow')
        button.setIcon(icon('right', '#888'))
        button.setIconSize(QSize(12, 12))
        button.setFixedSize(20, 28)
        button.setToolTip(f'Podfoldery: {destination}')
        button.clicked.connect(lambda checked=False, d=destination, b=button: self.crumb_menu(b, d))
        return button

    def crumb_menu(self, button, directory):
        menu = self.menu()
        try:
            with os.scandir(directory) as scan:
                folders = sorted((entry for entry in scan
                                  if (self.show_hidden or not entry.name.startswith('.')) and entry.is_dir()),
                                 key=lambda entry: entry.name.lower())[:60]
        except OSError as exc:
            folders = []
            menu.addAction(str(exc)).setEnabled(False)
        if not folders:
            menu.addAction('Brak podfolderów').setEnabled(False)
        current = self.page.current_dir() or ''
        for entry in folders:
            action = menu.addAction(icon_for(entry.path), entry.name,
                                    lambda checked=False, p=entry.path: self.page.navigate(p))
            if current == entry.path or current.startswith(entry.path + os.sep):
                action.setCheckable(True)
                action.setChecked(True)
        self.popup(button, menu)

    def edit_address(self):
        self.breadcrumbs.hide()
        self.address_edit.show()
        self.address_edit.setText(network.display_location(self.page.current_dir() or str(HOME)))
        self.address_edit.setFocus()
        self.address_edit.selectAll()

    def finish_address(self):
        self.address_edit.hide()
        self.breadcrumbs.show()

    def submit_address(self):
        path = self.address_edit.text().strip()
        try:
            resolved = local_path(path) if path and path not in VIRTUAL and QUrl(path).scheme() in ('', 'file') else path
        except ValueError:
            resolved = path
        if resolved and os.path.isfile(resolved):
            if self.page.navigate(str(Path(resolved).parent)):
                self.reveal_file(resolved, self.page)
                self.finish_address()
                self.page.setFocus()
            return
        if self.page.navigate(path):
            self.finish_address()
            self.page.setFocus()

    def go_up(self):
        directory = self.page.current_dir()
        if directory:
            self.page.navigate(network.parent_location(directory))

    def connect_to_server(self):
        value, accepted = QInputDialog.getText(self, 'Połącz z serwerem',
                                             'Adres serwera, np. smb://serwer/udział lub sftp://użytkownik@serwer/folder:',
                                             QLineEdit.Normal, 'smb://')
        if not accepted:
            return
        try:
            uri = network.validate_remote_url(value)
        except ValueError as exc:
            self.error(exc)
            return
        self.page.navigate(uri)

    def open_location(self, path):
        page = self.page
        if page.navigate(str(Path(path).parent)):
            self.reveal_file(path, page)

    def backspace(self):
        if not self.typing():
            self.page.travel(-1)

    def escape(self):
        if QApplication.focusWidget() is self.address_edit:
            self.finish_address()
        elif QApplication.focusWidget() is self.search_edit:
            self.search_edit.clear()
            self.page.setFocus()
        elif self.page:
            self.page.tree.clearSelection()

    def select_all(self):
        if self.typing():
            QApplication.focusWidget().selectAll()
        elif self.page:
            self.page.select_all()

    def refresh(self):
        page = self.page
        if not page:
            return
        query = page.search
        page.navigate(page.location, record=False)
        page.set_search(query)
        self.sync()

    def change_search_mode(self, *_):
        if not self.page:
            return
        mode = self.search_mode_combo.currentData()
        self.page.search_mode = mode
        self.settings.setValue('search_mode', mode)
        if self.search_edit.text().strip():
            self.search_timer.stop()
            self.page.set_search(self.search_edit.text())

    def saved_searches(self):
        try:
            values = json.loads(self.settings.value('saved_searches', '[]'))
        except (ValueError, TypeError):
            return []
        if not isinstance(values, list):
            return []
        result = []
        for value in values[:200]:
            try:
                result.append(search_features.validate_saved_search(value))
            except ValueError:
                continue
        return result

    def save_current_search(self):
        query = self.search_edit.text().strip()
        directory = self.page.current_dir() if self.page else None
        if not query or not directory:
            return
        name, accepted = QInputDialog.getText(self, 'Zapisz wyszukiwanie', 'Nazwa:', QLineEdit.Normal, query)
        if not accepted or not name.strip():
            return
        value = {'name': name.strip(), 'root': os.path.abspath(directory), 'query': query,
                 'mode': self.search_mode_combo.currentData()}
        searches = [item for item in self.saved_searches() if item['name'] != value['name']]
        self.settings.setValue('saved_searches', json.dumps(searches + [value], ensure_ascii=False))
        self.status_text.setText('Wyszukiwanie zostało zapisane.')

    def open_saved_search(self, value):
        if not self.page.navigate(value['root']):
            return
        self.page.search_mode = value['mode']
        self.search_mode_combo.blockSignals(True)
        self.search_mode_combo.setCurrentIndex(self.search_mode_combo.findData(value['mode']))
        self.search_mode_combo.blockSignals(False)
        self.search_edit.setText(value['query'])
        self.search_timer.stop()
        self.page.set_search(value['query'])

    def remove_saved_search(self, name):
        values = [value for value in self.saved_searches() if value['name'] != name]
        self.settings.setValue('saved_searches', json.dumps(values, ensure_ascii=False))

    def search_options_menu(self):
        menu = self.menu()
        action = menu.addAction(icon('star'), 'Zapisz bieżące wyszukiwanie…', self.save_current_search)
        action.setEnabled(bool(self.page and self.page.current_dir() and self.search_edit.text().strip()))
        searches = self.saved_searches()
        if searches:
            menu.addSeparator()
            for value in searches:
                menu.addAction(icon('search'), value['name'].replace('&', '&&'),
                               lambda checked=False, item=value: self.open_saved_search(item))
            remove = menu.addMenu('Usuń zapisane wyszukiwanie')
            for value in searches:
                remove.addAction(value['name'].replace('&', '&&'),
                                 lambda checked=False, name=value['name']: self.remove_saved_search(name))
        self.popup(self.search_options_button, menu)

    def refresh_service_menus(self, force=False):
        if self.service_menu_pending or (not force and time.monotonic() - self.service_menu_last_scan < 5):
            return
        self.service_menu_pending = True
        self.service_menu_last_scan = time.monotonic()
        holder = {}
        job = FileJob(lambda: holder.__setitem__('actions', service_menus.load()))

        def finished(error):
            self.background_jobs.discard(job)
            if not alive(self):
                return
            self.service_menu_pending = False
            if not error:
                self.service_menu_cache = holder['actions']

        job.signals.finished.connect(finished)
        self.start_job(job)

    def add_service_menus(self, menu, paths):
        self.refresh_service_menus()
        mimes = tuple('inode/directory' if os.path.isdir(path) else
                      MIME_DB.mimeTypeForFile(path).name() for path in paths)
        actions = [action for action in self.service_menu_cache if service_menus.applicable(
            action, paths, mimes, inherits=lambda actual, wanted: MIME_DB.mimeTypeForName(actual).inherits(wanted))]
        if not actions:
            return
        holder = menu.addMenu(icon('settings'), 'Akcje KDE')
        submenus = {}
        for action in actions:
            target = menu if action.priority == 'TopLevel' and not action.submenu else holder
            if action.submenu:
                key = (action.priority, action.submenu)
                if key not in submenus:
                    base = menu if action.priority == 'TopLevel' else holder
                    submenus[key] = base.addMenu(action.submenu.replace('&', '&&'))
                target = submenus[key]
            item = target.addAction(QIcon.fromTheme(action.icon, icon('file')), action.name.replace('&', '&&'),
                                    lambda checked=False, value=action, selected=tuple(paths): self.run_service_action(value, selected))
            item.setToolTip(action.source)
            item.setEnabled(not self.busy)
        if not holder.actions():
            menu.removeAction(holder.menuAction())
            holder.deleteLater()

    def run_service_action(self, action, paths):
        if self.busy:
            return
        if any(not os.path.lexists(path) for path in paths):
            self.error('Zaznaczony element jest już niedostępny.')
            return
        self.run_job(lambda: service_menus.launch(action, paths),
                     lambda: self.status_text.setText(f'Uruchomiono: {action.name}'))

    def edit_tags(self, paths, operation='set'):
        if self.busy or not paths:
            return
        existing = search_features.get_tags(paths[0]) if operation == 'set' and len(paths) == 1 else ()
        captions = {'set': 'Edytuj tagi', 'add': 'Dodaj tagi', 'remove': 'Usuń tagi'}
        text, accepted = QInputDialog.getText(self, captions[operation], 'Tagi oddzielone przecinkami:',
                                             QLineEdit.Normal, ', '.join(existing))
        if not accepted:
            return
        try:
            tags = search_features.normalize_tags(text.split(','))
        except ValueError as exc:
            self.error(exc)
            return
        count = [0]

        def work():
            for path in paths:
                current = search_features.get_tags(path)
                values = (tags if operation == 'set' else tuple(dict.fromkeys(current + tags))
                          if operation == 'add' else tuple(tag for tag in current if tag not in tags))
                search_features.set_tags(path, values)
                count[0] += 1

        def after():
            self.details_key = None
            self.status_text.setText(f'Zaktualizowano tagi: {count[0]}')

        self.run_job(work, after, on_error=after)

    def update_filter(self):
        # Per-page filtering can reveal a requested hidden file without changing
        # the user's global hidden-file preference or exposing every hidden sibling.
        flags = QDir.AllEntries | QDir.NoDotAndDotDot | QDir.System | QDir.Hidden
        self.model.show_hidden = self.show_hidden
        self.model.setFilter(flags)
        if hasattr(self, 'pages'):
            for page in self.all_pages():
                page.proxy.beginFilterChange()
                page.proxy.endFilterChange()

    def toggle_type_filter(self):
        if self.filter_box.isHidden():
            self.filter_box.show()
            self.filter_edit.setFocus()
        else:
            self.filter_box.hide()
            self.filter_edit.clear()

    def apply_type_filter(self, text):
        if self.page and self.page.type_filter != text.strip():
            self.page.set_type_filter(text)
            self.schedule_status()

    def schedule_status(self):
        if hasattr(self, 'status_timer'):
            self.status_timer.start()

    def update_status(self):
        page = self.page
        if not page or not hasattr(self, 'status_text') or not alive(page):
            return
        paths = page.selected_paths()
        directory = page.current_dir()
        if directory and page.searching:
            count = page.results.topLevelItemCount()
        elif directory:
            count = page.proxy.rowCount(page.tree.rootIndex())
        elif page.location == 'home':
            count = page.recent_tree.topLevelItemCount() if page.recent_tree else 0
        elif page.location == 'trash':
            count = page.trash_tree.topLevelItemCount()
        else:
            count = None
        text = f'Elementy: {count}' if count is not None else 'Ten komputer'
        if paths:
            text += f'   |   Zaznaczone: {len(paths)}'
            if len(paths) <= 2000:
                total = 0
                for p in paths:
                    try:
                        info = os.stat(p)
                    except OSError:
                        continue
                    if stat_module.S_ISREG(info.st_mode):
                        total += info.st_size
                if total:
                    text += f'   {size_text(total)}'
        self.status_text.setText('Trwa operacja na plikach…' if self.busy else
                                 f'Łączenie: {page.pending_remote}…' if page.pending_remote else text)
        if paths and len(paths) <= 50:
            self.prefetch_applications(paths)
        if directory:
            cached = self.space_cache.get(directory)
            if not cached or time.monotonic() - cached[0] > 5:
                volume = QStorageInfo(directory)
                text_value = (f'Wolne: {size_text(volume.bytesAvailable())} z {size_text(volume.bytesTotal())}'
                              if volume.isValid() and volume.bytesTotal() > 0 else '')
                if len(self.space_cache) > 64:
                    self.space_cache.clear()
                cached = self.space_cache[directory] = (time.monotonic(), text_value)
            self.space_text.setText(cached[1])
        else:
            self.space_text.setText('')
        for key, button in self.file_buttons.items():
            enabled = bool(paths)
            if key == 'rename':
                enabled = len(paths) >= 1 and page.location != 'trash'
            elif key == 'paste':
                enabled = bool(directory) and clipboard_has_files()
            elif key in ('cut', 'trash') and page.location == 'trash':
                enabled = False
            button.setEnabled(enabled and not self.busy)
        self.new_button.setEnabled(bool(directory) and not self.busy)
        self.sort_button.setEnabled(bool(directory))
        if self.details.isVisible():
            self.fill_details()
        if self.preview.isVisible():
            self.preview.show_paths(paths)
        self.update_device_controls()

    def error(self, text):
        QMessageBox.warning(self, 'Eksplorator plików', str(text))

    def typing(self):
        return isinstance(QApplication.focusWidget(), (QLineEdit, TerminalView))

    def open_selected(self):
        if self.typing():
            if QApplication.focusWidget() == self.address_edit:
                self.submit_address()
            return
        paths = self.page.selected_paths()
        if not paths:
            return
        if len(paths) == 1:
            self.page.open_path(paths[0])
            return
        # Windows opens every selected item: folders in their own tabs, files in one launch per application.
        files = []
        for path in paths[:20]:
            if Path(path).is_dir():
                self.add_tab(path)
            else:
                files.append(path)
        groups = {}
        for path in files:
            groups.setdefault(self.selection_mime([path]), []).append(path)
        for group in groups.values():
            self.launch_paths(group)

    @staticmethod
    def desktop_application(desktop_id):
        """Return the visible name and icon of an installed desktop entry, or None when it is not installed."""
        if not desktop_id or '/' in desktop_id or not desktop_id.endswith('.desktop'):
            return None
        values = desktop_entries().get(desktop_id)
        if values is None:
            return None
        name = values.get('Name[pl]') or values.get('Name') or desktop_id.removesuffix('.desktop')
        return name, values.get('Icon', '')

    def applications_for_paths(self, paths):
        """Read the MIME association database for one uniform file selection (cached per MIME type for 30 s)."""
        mime = self.selection_mime(paths)
        if not mime:
            return '', '', []
        cached = self.app_cache.get(mime)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        result = self.query_applications(mime)
        self.app_cache[mime] = (time.monotonic(), result)
        return result

    def selection_mime(self, paths):
        files = [path for path in paths if Path(path).is_file()]
        if not files or len(files) != len(paths):
            return ''
        mime_types = {MIME_DB.mimeTypeForFile(path, QMimeDatabase.MatchDefault).name() for path in files[:50]}
        return mime_types.pop() if len(mime_types) == 1 else ''

    def prefetch_applications(self, paths):
        """Warm the MIME association cache in the background so the context menu opens without a `gio` round trip."""
        mime = self.selection_mime(paths)
        if not mime or mime in self.app_pending:
            return
        cached = self.app_cache.get(mime)
        if cached and time.monotonic() - cached[0] < 30:
            return
        self.app_pending.add(mime)
        holder = {}
        job = FileJob(lambda: holder.__setitem__('result', self.query_applications(mime)))

        def finished(error):
            self.background_jobs.discard(job)
            if alive(self):
                self.app_pending.discard(mime)
                if not error and 'result' in holder:
                    self.app_cache[mime] = (time.monotonic(), holder['result'])
        job.signals.finished.connect(finished)
        self.start_job(job)

    def query_applications(self, mime):
        default_id = ''
        registered = []
        try:
            result = subprocess.run(['/usr/bin/gio', 'mime', mime], capture_output=True, text=True, timeout=2,
                                    env={**os.environ, 'LC_ALL': 'C'})
            collecting = False
            for raw_line in result.stdout.splitlines() if result.returncode == 0 else []:
                line = raw_line.strip()
                if line.startswith('Default application for'):
                    default_id = line.rsplit(':', 1)[-1].strip()
                elif line in ('Registered applications:', 'Recommended applications:'):
                    collecting = True
                elif line.endswith('applications:'):
                    collecting = False
                elif collecting and line.endswith('.desktop'):
                    registered.append(line)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not default_id:
            try:
                result = subprocess.run(['/usr/bin/xdg-mime', 'query', 'default', mime], capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    default_id = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        application_ids = []
        entries = desktop_entries()
        for desktop_id in [default_id, *registered]:
            if desktop_id == 'fileexplorer.desktop':
                continue
            if desktop_id and desktop_id not in application_ids and desktop_entry_available(entries.get(desktop_id)):
                application_ids.append(desktop_id)
        if default_id and not desktop_entry_available(entries.get(default_id)):
            default_id = ''
        # A mistaken association with this file manager must never reopen the same file recursively.
        # Keep its id when there is no alternative so launch_paths can report the bad association.
        if (not default_id or default_id == 'fileexplorer.desktop') and application_ids:
            default_id = application_ids[0]
        return mime, default_id, application_ids

    @staticmethod
    def spawn(command):
        """Check quick launcher failures without waiting for the application itself."""
        try:
            process = subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if Path(command[0]).name in ('gtk-launch', 'gio', 'xdg-open'):
                try:
                    return process.wait(timeout=0.5) == 0
                except subprocess.TimeoutExpired:
                    # Some xdg-open backends keep running for the life of the application.
                    # Retrying would then open a duplicate, so leave the detached process running.
                    return True
            return True
        except OSError:
            return False

    def launch_paths(self, paths, desktop_id=None):
        """Open files in their application.

        `QDesktopServices.openUrl()`, `gio open` and `xdg-open` all return success yet start nothing on this
        KDE session, so the resolved desktop entry is launched directly and they are only the last resort.
        """
        paths = [os.path.abspath(os.fspath(path)) for path in paths if path]
        if not paths:
            return
        explicit = desktop_id is not None
        if not explicit:
            _, desktop_id, application_ids = self.applications_for_paths(paths)
            if desktop_id == 'fileexplorer.desktop':
                desktop_id = next((entry for entry in application_ids if entry != desktop_id), desktop_id)
        if desktop_id == 'fileexplorer.desktop':
            self.error('Eksplorator plików jest przypisany do otwierania tego pliku. Wybierz właściwą aplikację przez „Otwórz za pomocą…”.')
            return
        uris = [QUrl.fromLocalFile(path).toString() for path in paths]
        values = desktop_entries().get(desktop_id) if desktop_id else None
        entry = values.get('X-Entry-Path') if desktop_entry_available(values) else None
        if entry:
            if self.spawn(['/usr/bin/gtk-launch', desktop_id.removesuffix('.desktop'), *uris]):
                return
            if self.spawn(['/usr/bin/gio', 'launch', entry, *paths]):
                return
        if explicit or values is not None:
            self.error('Nie udało się uruchomić wybranej aplikacji. Wybierz inną przez „Otwórz za pomocą…”.')
            return
        if self.spawn(['/usr/bin/gio', 'open', *paths]):
            return
        failed = []
        for path in paths:
            if not self.spawn(['/usr/bin/xdg-open', path]) and not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                failed.append(path)
        if failed:
            self.error('Nie znaleziono aplikacji do otwarcia tego pliku.')

    def open_with_application(self, desktop_id):
        paths = self.page.selected_paths()
        if paths:
            self.launch_paths(paths, desktop_id)

    def set_default_application(self, mime, desktop_id):
        if not mime or '/' not in mime or any(character.isspace() for character in mime) or not desktop_entry_available(desktop_entries().get(desktop_id)):
            self.error('Nie można ustawić domyślnej aplikacji: nieprawidłowy typ pliku lub niedostępna aplikacja.')
            return
        if desktop_id == 'fileexplorer.desktop' and mime not in ('inode/directory', 'x-scheme-handler/trash'):
            self.error('Eksplorator plików może być domyślną aplikacją tylko dla folderów i kosza.')
            return
        failures = []
        for command in (['/usr/bin/gio', 'mime', mime, desktop_id],
                        ['/usr/bin/xdg-mime', 'default', desktop_id, mime]):
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    break
                failures.append(result.stderr.strip() or f'{Path(command[0]).name}: kod {result.returncode}')
            except (OSError, subprocess.TimeoutExpired) as exc:
                failures.append(str(exc))
        else:
            self.error(f'Nie udało się ustawić domyślnej aplikacji: {"; ".join(failures)}')
            return
        self.app_cache.pop(mime, None)
        application = self.desktop_application(desktop_id)
        self.status_text.setText(f'Domyślna aplikacja: {application[0] if application else desktop_id}')

    def share(self):
        paths = self.page.selected_paths()
        if not paths:
            if self.share_server is not None:
                paths = [str(p) for p in self.share_server.files.values()]
            else:
                self.status_text.setText('Zaznacz pliki, które chcesz udostępnić.')
                return
        dialog = ShareDialog(self, paths)
        dialog.exec()
        dialog.deleteLater()

    def stop_share_server(self):
        if self.share_server is not None:
            try:
                self.share_server.stop()
            except OSError:
                pass
            self.share_server = None

    def copy_path(self, location_only=False):
        paths = self.page.selected_paths()
        if not paths:
            return
        values = [str(Path(path).parent) if location_only and Path(path).is_file() else path for path in paths]
        QApplication.clipboard().setText('\n'.join(values))
        self.status_text.setText(f"Skopiowano {'położenie' if location_only else 'ścieżkę'}: {len(values)}")

    def duplicate(self):
        paths = self.page.selected_paths()
        directory = self.page.current_dir()
        if not paths or not directory or self.busy:
            return
        copies = []
        reserved = set()
        for value in paths:
            source = Path(value)
            suffix = source.suffix if source.is_file() else ''
            stem = source.stem if suffix else source.name
            target = Path(directory) / source.name
            number = 1
            while os.path.lexists(target) or target in reserved:
                target = Path(directory) / f'{stem} — kopia{f" ({number})" if number > 1 else ""}{suffix}'
                number += 1
            copies.append((source, target))
            reserved.add(target)
        self.run_transfer_prepared(copies, cut=False)

    def move_to_new_folder(self):
        paths = self.page.selected_paths()
        directory = self.page.current_dir()
        if not paths or not directory or self.busy:
            return
        name = self.ask_name('Przenieś do nowego katalogu', 'Nowy folder')
        if not name:
            return
        target_dir = Path(directory) / name
        if os.path.lexists(target_dir):
            self.error('Element o tej nazwie już istnieje.')
            return
        try:
            target_dir.mkdir()
        except OSError as exc:
            self.error(exc)
            return
        self.run_transfer([Path(p) for p in paths if Path(p) != target_dir], target_dir, cut=True)

    def archive(self):
        paths = self.page.selected_paths()
        directory = self.page.current_dir()
        if not paths or not directory or self.busy:
            return
        default_name = Path(paths[0]).stem if len(paths) == 1 else Path(directory).name
        name, ok = QInputDialog.getText(self, 'Utwórz archiwum', 'Nazwa archiwum (bez rozszerzenia):', QLineEdit.Normal, default_name)
        name = name.strip()
        if not ok or not name or '/' in name:
            return
        formats = [('zip', 'ZIP (.zip)', '.zip'), ('gztar', 'Tar GZ (.tar.gz)', '.tar.gz'), ('bztar', 'Tar BZ2 (.tar.bz2)', '.tar.bz2'),
                   ('xztar', 'Tar XZ (.tar.xz)', '.tar.xz'), ('tar', 'Tar (.tar)', '.tar')]
        fmt_name, ok = QInputDialog.getItem(self, 'Format', 'Wybierz format:', [f[1] for f in formats], 0, False)
        if not ok:
            return
        fmt, _, extension = next(f for f in formats if f[1] == fmt_name)
        out_path = Path(directory) / name
        if os.path.lexists(str(out_path) + extension):
            self.error(f'Archiwum {name}{extension} już istnieje.')
            return
        sources = [Path(p) for p in paths]

        def operation():
            # Build privately and publish once complete. A failed compressor or a
            # destination created since the dialog must never damage user files.
            with tempfile.TemporaryDirectory(dir=directory, prefix='.archiwum-') as tmp:
                staged_base = Path(tmp) / 'archive'
                if len(sources) == 1 and sources[0].is_dir() and not sources[0].is_symlink():
                    staged_archive = shutil.make_archive(str(staged_base), fmt,
                                                         root_dir=sources[0].parent, base_dir=sources[0].name)
                else:
                    payload = Path(tmp) / 'payload'
                    payload.mkdir()
                    for src in sources:
                        tgt = payload / src.name
                        if src.is_symlink():
                            os.symlink(os.readlink(src), tgt)
                        elif src.is_dir():
                            shutil.copytree(src, tgt, symlinks=True)
                        else:
                            os.link(src, tgt) if src.stat().st_dev == os.stat(tmp).st_dev else shutil.copy2(src, tgt)
                    staged_archive = shutil.make_archive(str(staged_base), fmt, root_dir=payload)
                rename_noreplace(Path(staged_archive), Path(str(out_path) + extension))

        self.run_job(operation, lambda: self.record(('transfer', [('copy', None, Path(str(out_path) + extension))])))

    def extract_archive(self, dest_folder=False):
        paths = self.page.selected_paths()
        directory = self.page.current_dir()
        if not paths or not directory or self.busy:
            return
        jobs = []
        reserved = set()
        for path in paths:
            src = Path(path)
            if src.suffix.lower() not in ARCHIVE_SUFFIXES:
                continue
            out_dir = Path(directory)
            if dest_folder:
                stem = src.name
                for extension in ('.tar.gz', '.tar.bz2', '.tar.xz'):
                    if stem.lower().endswith(extension):
                        stem = stem[:-len(extension)]
                        break
                else:
                    stem = src.stem
                out_dir = unique_path(directory, stem, 'rozpakowane', reserved=reserved)
                reserved.add(out_dir)
            jobs.append((src, out_dir))
        if not jobs:
            return
        extracted = TransferHistory()

        def operation():
            import zipfile
            for src, out_dir in jobs:
                # Decode into a private, empty directory: existing destination symlinks
                # cannot redirect archive writes, and corrupt archives leave no output.
                with tempfile.TemporaryDirectory(prefix='.fileexplorer-extract-', dir=directory) as temporary:
                    staged = Path(temporary) / 'payload'
                    staged.mkdir()
                    if zipfile.is_zipfile(src):
                        shutil.unpack_archive(str(src), str(staged), format='zip')
                    elif src.suffix.lower() in ('.7z', '.rar'):
                        from archive_formats import extract_extra_archive
                        extract_extra_archive(src, staged)
                    else:
                        shutil.unpack_archive(str(src), str(staged), filter='data')
                    if dest_folder:
                        rename_noreplace(staged, out_dir)
                        extracted.append(('copy', None, out_dir))
                        extracted.capture(extracted[-1])
                    else:
                        entries = list(staged.iterdir())
                        for entry in entries:
                            target = out_dir / entry.name
                            if os.path.lexists(target):
                                raise FileExistsError(f'Element już istnieje: {target}. '
                                                      'Wybierz rozpakowanie do osobnego folderu.')
                        for entry in entries:
                            target = out_dir / entry.name
                            rename_noreplace(entry, target)
                            extracted.append(('copy', None, target))
                            extracted.capture(extracted[-1])

        def after():
            if extracted:
                self.record(('transfer', extracted))

        self.run_job(operation, after, on_error=after)

    def set_wallpaper(self):
        paths = self.page.selected_paths()
        if len(paths) != 1 or Path(paths[0]).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
            self.error('Wybierz pojedynczy plik obrazu, aby ustawić tapetę.')
            return
        try:
            subprocess.Popen(['/usr/bin/plasma-apply-wallpaperimage', paths[0]], start_new_session=True)
        except OSError as exc:
            self.error(exc)

    def copy(self, cut=False):
        if self.typing():
            widget = QApplication.focusWidget()
            widget.cut() if cut else widget.copy()
            return
        paths = self.page.selected_paths()
        if not paths or self.busy:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        mime.setData('application/x-kde-cutselection', b'1' if cut else b'0')
        mime.setData('x-special/gnome-copied-files', (('cut' if cut else 'copy') + '\n' + '\n'.join(QUrl.fromLocalFile(p).toString() for p in paths)).encode())
        QApplication.clipboard().setMimeData(mime)

    def start_job(self, job):
        """Run a non-auto-deleting QRunnable; the window keeps it alive until its signals report completion."""
        self.background_jobs.add(job)
        QThreadPool.globalInstance().start(job)

    def run_job(self, operation, after=None, on_error=None):
        if self.busy:
            self.status_text.setText('Poczekaj na zakończenie bieżącej operacji…')
            return
        self.busy = True
        self.update_status()
        job = self.job = FileJob(operation)

        def finished(error):
            self.background_jobs.discard(job)
            if not alive(self):
                return
            self.busy = False
            self.job = None
            if error:
                if on_error:
                    on_error()
                self.error(error)
            elif after:
                after()
            self.refresh()
        job.signals.finished.connect(finished)
        self.start_job(job)

    def run_transfer(self, sources, target_dir, cut=False):
        """Resolve name conflicts interactively, then copy/move `sources` into `target_dir` in the background."""
        if self.busy:
            return
        target_dir = Path(target_dir)
        sources = list(dict.fromkeys(Path(src) for src in sources))
        transfers = []
        reserved = set()
        overwrites = {}
        apply_all_resolution = None
        for src in sources:
            tgt = target_dir / src.name
            try:
                same = os.path.lexists(src) and os.path.lexists(tgt) and os.lstat(src) == os.lstat(tgt)
            except OSError:
                same = False
            if same:
                if cut:
                    continue
                tgt = unique_path(target_dir, src.name, reserved=reserved)
                transfers.append((src, tgt))  # copy next to the original
                reserved.add(tgt)
                continue
            if src.is_dir() and not src.is_symlink():
                try:
                    if target_dir.resolve().is_relative_to(src.resolve()):
                        self.error(f'Nie można skopiować ani przenieść folderu {src.name} do jego własnego podfolderu.')
                        continue
                except OSError:
                    pass
            if tgt in reserved:
                tgt = unique_path(target_dir, src.name, reserved=reserved)
            if os.path.lexists(tgt):
                res = apply_all_resolution
                if not res:
                    dialog = ConflictDialog(self, src, tgt)
                    res = dialog.resolution if dialog.exec() == QDialog.Accepted else 'skip'
                    if dialog.apply_all:
                        apply_all_resolution = res
                    dialog.deleteLater()
                if res == 'skip':
                    continue
                if res == 'overwrite':
                    try:
                        # Protect selected entries themselves as well as their containing folders.
                        destination = tgt.parent.resolve() / tgt.name
                        source_paths = [source.parent.resolve() / source.name for source in sources]
                        contains_source = tgt.is_dir() and not tgt.is_symlink() and any(
                            source.is_relative_to(destination) for source in source_paths)
                        if destination in source_paths or contains_source:
                            self.error(f'Nie można nadpisać elementu będącego źródłem lub zawierającego pliki źródłowe: {tgt}')
                            continue
                        overwrites[tgt] = entry_version(tgt)
                    except OSError as exc:
                        self.error(exc)
                        continue
                elif res == 'rename':
                    tgt = unique_path(target_dir, src.name, reserved=reserved)
            transfers.append((src, tgt))
            reserved.add(tgt)
        self.run_transfer_prepared(transfers, cut, overwrites=overwrites)

    def run_transfer_prepared(self, transfers, cut=False, overwrites=None):
        if self.busy:
            self.status_text.setText('Poczekaj na zakończenie bieżącej operacji…')
        if not transfers or self.busy:
            return
        self.busy = True
        self.update_status()
        dialog = ProgressDialog(self, 'Przenoszenie plików' if cut else 'Kopiowanie plików')
        job = TransferJob(transfers, cut, overwrites)
        dialog.job = job
        self.job = job

        def finished(error, completed):
            self.background_jobs.discard(job)
            if alive(dialog):
                dialog.job = None
                dialog.hide()
                dialog.deleteLater()
            if not alive(self):
                return
            self.busy = False
            self.job = None
            if error:
                self.error(error)
            if completed:
                self.record(('transfer', completed))
            self.refresh()
            if completed:
                self.select_results([target for _, _, target in completed])
        job.signals.progress.connect(lambda copied, total, name: alive(dialog) and dialog.update_progress(copied, total, name))
        job.signals.finished.connect(finished)
        dialog.show()
        self.start_job(job)

    def record(self, entry):
        """Remember an operation for Ctrl+Z; doing new work drops whatever Ctrl+Y would have repeated."""
        action, data = entry
        if action == 'transfer' and not isinstance(data, TransferHistory):
            entry = (action, TransferHistory(data))
        self.undo_stack.append(entry)
        self.redo_stack.clear()

    def replayable(self, entry):
        """Archives and extractions record no source, so they can be undone but never repeated."""
        action, data = entry
        return action != 'transfer' or all(original_src is not None for _, original_src, _ in data)

    def undo(self):
        self.apply_history(self.undo_stack, self.redo_stack, redo=False)

    def redo(self):
        self.apply_history(self.redo_stack, self.undo_stack, redo=True)

    def apply_history(self, source_stack, target_stack, redo):
        if self.busy or not source_stack:
            return
        entry = source_stack[-1]
        action, data = entry
        pending = list(data if redo else reversed(data))
        completed = []
        new_versions = {}

        def operation():
            blocked_overwrites = []
            for item in pending:
                if action == 'transfer':
                    op_type, original_src, current_tgt = item
                    saved = getattr(data, 'backups', {}).get(current_tgt)
                    if saved is not None:
                        callbacks = dict(version=tree_version, rename=rename_noreplace,
                                         move=lambda src, tgt: TransferJob([]).move_entry(Path(src), Path(tgt), 0))
                        saved.redo(**callbacks) if redo else saved.undo(**callbacks)
                        if redo and op_type == 'copy':
                            new_versions[current_tgt] = tree_version(current_tgt)
                        completed.append(item)
                        continue
                    if not redo and current_tgt in getattr(data, 'overwritten', ()):
                        blocked_overwrites.append(current_tgt)
                        continue
                    source, target = (original_src, current_tgt) if redo else (current_tgt, original_src)
                    if source is None or not os.path.lexists(source):
                        raise FileNotFoundError(f'Nie znaleziono elementu: {source}')
                    if not redo and op_type == 'copy':
                        if isinstance(data, TransferHistory):
                            expected = data.versions.get(current_tgt)
                            if expected is None or tree_version(source) != expected:
                                raise OSError(f'Nie można cofnąć: kopia została zmieniona po utworzeniu: {source}')
                        remove_entry(source)
                    else:
                        if os.path.lexists(target):
                            raise FileExistsError(f'Element docelowy już istnieje: {target}')
                        Path(target).parent.mkdir(parents=True, exist_ok=True)
                        job = TransferJob([])
                        if op_type == 'move':
                            job.move_entry(Path(source), Path(target), 0)
                        else:
                            job.copy_atomic(Path(source), Path(target))
                            snapshot = TransferHistory([item])
                            new_versions.update(snapshot.versions)
                elif action == 'trash':
                    original_src, trashed_path = item
                    source, target = (original_src, trashed_path) if redo else (trashed_path, original_src)
                    if not os.path.lexists(source):
                        raise FileNotFoundError(f'Nie znaleziono elementu: {source}')
                    if os.path.lexists(target):
                        raise FileExistsError(f'Element docelowy już istnieje: {target}')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if redo:
                        write_trashinfo(original_src, trashed_path)
                    try:
                        TransferJob([]).move_entry(source, target, 0)
                    except OSError:
                        if redo:
                            trash_backend.info_path(trashed_path, TRASH_ROOT).unlink(missing_ok=True)
                        raise
                    if not redo:
                        trash_backend.info_path(trashed_path, TRASH_ROOT).unlink(missing_ok=True)
                elif action == 'rename':
                    old_path, new_path = item
                    source, target = (old_path, new_path) if redo else (new_path, old_path)
                    rename_noreplace(source, target)
                completed.append(item)
            if blocked_overwrites:
                names = '\n'.join(str(path) for path in blocked_overwrites[:8])
                message = ('Nie można cofnąć nadpisania: poprzednia wersja nie została zachowana. '
                           'Bieżące pliki pozostają bez zmian.\n\n' + names)
                if completed:
                    message += f'\n\nCofnięto pozostałe bezpieczne elementy: {len(completed)}.'
                raise OSError(message)

        def update_history():
            if not completed:
                return
            source_stack.pop()
            remaining = list(pending)
            for item in completed:
                remaining.remove(item)
            def history_items(items, versions):
                return (TransferHistory(items, versions=versions, overwritten=data.overwritten, backups=data.backups)
                        if isinstance(data, TransferHistory) else items)
            old_versions = getattr(data, 'versions', {})
            if remaining:
                source_stack.append((action, history_items(remaining if redo else list(reversed(remaining)), old_versions)))
            applied_items = list(completed) if redo else list(reversed(completed))
            applied = (action, history_items(applied_items, new_versions if redo else old_versions))
            if redo or self.replayable(applied):
                target_stack.append(applied)

        def finished():
            update_history()
            self.status_text.setText('Ponowiono ostatnią operację.' if redo else 'Cofnięto ostatnią operację.')

        self.run_job(operation, finished, on_error=update_history)

    def paste(self):
        if self.typing():
            QApplication.focusWidget().paste()
            return
        directory = self.page.current_dir()
        mime = QApplication.clipboard().mimeData()
        if not directory or mime is None or not mime.hasUrls() or self.busy:
            return
        sources = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
        if not sources:
            return
        cut = bytes(mime.data('application/x-kde-cutselection')) == b'1' or bytes(mime.data('x-special/gnome-copied-files')).startswith(b'cut\n')
        clipboard_urls = [url.toString() for url in mime.urls()]
        self.run_transfer(sources, directory, cut)
        current = QApplication.clipboard().mimeData()
        if cut and current is not None and [url.toString() for url in current.urls()] == clipboard_urls:
            QApplication.clipboard().clear()

    def ask_name(self, title, default):
        name, ok = QInputDialog.getText(self, title, 'Nazwa:', QLineEdit.Normal, default)
        if not ok:
            return None
        if not name or name in ('.', '..') or '/' in name or '\x00' in name:
            self.error('Podaj prawidłową nazwę bez znaku /.')
            return None
        return name

    def new_folder(self):
        directory = self.page.current_dir()
        if not directory or self.busy:
            return
        target = numbered_path(directory, 'Nowy folder')
        try:
            target.mkdir()
        except OSError as exc:
            self.error(exc)
            return
        self.page.edit_when_available(target)

    def new_file(self):
        directory = self.page.current_dir()
        if not directory or self.busy:
            return
        target = numbered_path(directory, 'Nowy dokument tekstowy.txt')
        try:
            with target.open('x'):
                pass
        except OSError as exc:
            self.error(exc)
            return
        self.page.edit_when_available(target)

    def rename(self):
        if self.typing() or self.busy:
            return
        paths = self.page.selected_paths()
        if not paths:
            return
            
        if len(paths) == 1:
            if self.page.edit_name(paths[0]):
                return
            source = Path(paths[0])
            name = self.ask_name('Zmień nazwę', source.name)
            if name and name != source.name:
                target = source.with_name(name)
                try:
                    rename_noreplace(source, target)
                    self.record(('rename', [(source, target)]))
                except OSError as exc:
                    self.error(exc)
            return
        dialog = BatchRenameDialog(self, len(paths))
        accepted = dialog.exec() == QDialog.Accepted
        pattern = dialog.pattern()
        dialog.deleteLater()
        if not accepted or not pattern or '/' in pattern:
            return
        hash_match = re.search(r'#+', pattern)
        renames = []
        errors = []
        for counter, p in enumerate(sorted(paths, key=lambda value: Path(value).name.casefold()), 1):
            src = Path(p)
            new_name = pattern.replace(hash_match.group(), str(counter).zfill(len(hash_match.group())), 1) if hash_match else pattern
            suffix = src.suffix if src.is_file() else ''
            tgt = src.with_name(new_name + suffix)
            inner = 1
            while os.path.lexists(tgt) and tgt != src:
                inner += 1
                tgt = src.with_name(f'{new_name} ({inner}){suffix}')
            if src == tgt:
                continue
            try:
                rename_noreplace(src, tgt)
                renames.append((src, tgt))
            except OSError as exc:
                errors.append(f'{src.name}: {exc.strerror or exc}')
        if renames:
            self.record(('rename', renames))
            self.refresh()
        if errors:
            self.error('Nie udało się zmienić nazwy:\n' + '\n'.join(errors[:10]))

    def trash(self):
        if self.typing():
            QApplication.focusWidget().del_()
            return
        paths = self.page.selected_paths()
        if not paths or self.busy:
            return
        # Like Windows: no confirmation, the Recycle Bin and Ctrl+Z are the safety net.
        page, successor = self.page, self.page.neighbour_of_selection()
        trashed_items = []

        def operation():
            for path in paths:
                src = Path(path)
                if not os.path.lexists(src):
                    continue
                tgt, info_file = trash_backend.reserve(src, TRASH_ROOT)
                try:
                    TransferJob([]).move_entry(src, tgt, 0)
                except OSError:
                    info_file.unlink(missing_ok=True)
                    raise
                trashed_items.append((src, tgt))

        def after():
            if trashed_items:
                self.record(('trash', trashed_items))
                self.status_text.setText(f'Przeniesiono do kosza: {len(trashed_items)}')
            self.select_successor(page, successor)

        self.run_job(operation, after, on_error=after)

    def delete_permanently(self):
        if self.typing() or self.busy:
            return
        paths = self.page.selected_paths()
        if not paths:
            return
        names = '\n'.join(Path(p).name for p in paths[:8]) + ('\n…' if len(paths) > 8 else '')
        answer = QMessageBox.warning(self, 'Usuń trwale', f'Trwale usunąć zaznaczone elementy ({len(paths)})? Tej operacji nie można cofnąć.\n\n{names}',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        page, successor = self.page, self.page.neighbour_of_selection()

        from_trash = page.location == 'trash'

        def operation():
            for path in paths:
                remove_entry(path)
                if from_trash:
                    trash_backend.info_path(path, TRASH_ROOT).unlink(missing_ok=True)

        self.run_job(operation, lambda: self.select_successor(page, successor))

    def empty_trash(self):
        if self.busy:
            return
        answer = QMessageBox.question(self, 'Opróżnij kosz', 'Czy na pewno chcesz trwale usunąć wszystkie elementy z kosza?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        def operation():
            errors = []
            for root in trash_backend.roots(TRASH_ROOT):
                files, info = root.files, root.info
                try:
                    stored_items = list(files.iterdir())
                except OSError as exc:
                    errors.append(f'{files}: {exc}')
                    continue
                for item in stored_items:
                    try:
                        remove_entry(item)
                        (info / f'{item.name}.trashinfo').unlink(missing_ok=True)
                    except OSError as exc:
                        errors.append(f'{item}: {exc}')
                for metadata in info.glob('*.trashinfo'):
                    if not os.path.lexists(files / metadata.name.removesuffix('.trashinfo')):
                        try:
                            metadata.unlink()
                        except OSError as exc:
                            errors.append(f'{metadata}: {exc}')
            if errors:
                raise OSError('Nie udało się opróżnić całego kosza:\n' + '\n'.join(errors[:10]))

        def after():
            remaining_history = []
            for action, data in self.undo_stack:
                if action == 'trash':
                    data = [(original, stored) for original, stored in data if os.path.lexists(stored)]
                    if not data:
                        continue
                remaining_history.append((action, data))
            self.undo_stack = remaining_history

        self.run_job(operation, after, on_error=after)

    def trash_context_menu(self, global_pos):
        page = self.page
        if not page or page.location != 'trash':
            return
        items = page.trash_tree.selectedItems()
        menu = self.menu()
        if items:
            menu.addAction(icon('restore'), 'Przywróć', self.restore_from_trash)
            menu.addAction(icon('trash'), 'Usuń trwale\tShift+Delete', self.delete_permanently)
            menu.addSeparator()
            menu.addAction(icon('copy'), 'Kopiuj oryginalną ścieżkę', lambda: QApplication.clipboard().setText(
                '\n'.join(item.data(0, Qt.UserRole + 1) or '' for item in items)))
            menu.addSeparator()
        menu.addAction(icon('refresh'), 'Odśwież', self.refresh)
        menu.addAction(icon('trash'), 'Opróżnij Kosz', self.empty_trash).setEnabled(page.trash_tree.topLevelItemCount() > 0 and not self.busy)
        menu.popup(global_pos)

    def open_trash_item(self, item):
        stored = item.data(0, Qt.UserRole)
        if stored and Path(stored).is_dir():
            self.add_tab(stored)
        elif stored:
            self.page.open_path(stored)

    def restore_from_trash(self):
        if self.busy or not hasattr(self, 'page') or self.page.location != 'trash':
            return
        items = self.page.trash_tree.selectedItems()
        if not items:
            return
        restores = []
        for item in items:
            stored = item.data(0, Qt.UserRole)
            original = item.data(0, Qt.UserRole + 1)
            if stored and original:
                restores.append((Path(stored), Path(original)))
        def operation():
            for stored, original in restores:
                if not os.path.lexists(stored):
                    continue
                original.parent.mkdir(parents=True, exist_ok=True)
                target = unique_path(original.parent, original.name, 'odzyskany')
                TransferJob([]).move_entry(stored, target, 0)
                trash_backend.info_path(stored, TRASH_ROOT).unlink(missing_ok=True)
        self.run_job(operation)

    def popup(self, button, menu):
        menu.popup(button.mapToGlobal(QPoint(0, button.height() + 4)))

    def new_menu(self):
        menu = self.menu()
        self.fill_new_menu(menu)
        self.popup(self.new_button, menu)

    def fill_new_menu(self, menu):
        menu.addAction(icon('folder'), 'Folder\tCtrl+Shift+N', self.new_folder)
        menu.addAction(icon('file'), 'Dokument tekstowy', self.new_file)
        templates = self.templates()
        if templates:
            menu.addSeparator()
            for template in templates:
                menu.addAction(self.provider.icon(QFileInfo(str(template))), template.stem if template.is_file() else template.name,
                               lambda checked=False, t=template: self.new_from_template(t))
        if self.page.selected_paths():
            menu.addSeparator()
            menu.addAction(icon('link'), 'Dowiązanie symboliczne', self.create_symlink)

    @staticmethod
    def templates():
        """Files in the XDG Templates folder (Szablony), like KDE's and GNOME's 'New' menus."""
        directory = Path(QStandardPaths.writableLocation(QStandardPaths.TemplatesLocation))
        try:
            return sorted((p for p in directory.iterdir() if not p.name.startswith('.')), key=lambda p: p.name.casefold())[:20]
        except OSError:
            return []

    def new_from_template(self, template):
        directory = self.page.current_dir()
        if not directory or self.busy:
            return
        target = numbered_path(directory, template.name)
        try:
            if template.is_dir():
                shutil.copytree(template, target, symlinks=True)
            else:
                shutil.copy2(template, target)
        except OSError as exc:
            self.error(exc)
            return
        self.record(('transfer', [('copy', template, target)]))
        self.page.edit_when_available(target)

    def sort_menu(self):
        menu = self.menu()
        self.fill_sort_menu(menu)
        self.popup(self.sort_button, menu)

    def fill_sort_menu(self, menu):
        column = self.page.tree.header().sortIndicatorSection()
        order = self.page.tree.header().sortIndicatorOrder()
        group = QActionGroup(menu)
        for text, value in [('Nazwa', 0), ('Data modyfikacji', 3), ('Typ', 2), ('Rozmiar', 1)]:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(column == value)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, v=value: self.page.tree.sortByColumn(v, order))
        menu.addSeparator()
        direction = QActionGroup(menu)
        for text, value in [('Rosnąco', Qt.AscendingOrder), ('Malejąco', Qt.DescendingOrder)]:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(order == value)
            direction.addAction(action)
            action.triggered.connect(lambda checked=False, v=value: self.page.tree.sortByColumn(column, v))

    def fill_view_modes(self, menu, page):
        group = QActionGroup(menu)
        for number, (mode, (label, glyph, _)) in enumerate(VIEW_MODES.items(), 1):
            action = menu.addAction(icon(glyph), f'{label}\tCtrl+Shift+{number}', lambda checked=False, m=mode: page.set_mode(m))
            action.setCheckable(True)
            action.setChecked(page.mode == mode)
            group.addAction(action)

    def view_menu(self):
        menu = self.menu()
        self.fill_view_modes(menu, self.page)
        menu.addSeparator()
        action = menu.addAction(icon('eye'), 'Elementy ukryte\tCtrl+H')
        action.setCheckable(True)
        action.setChecked(self.show_hidden)
        action.triggered.connect(self.toggle_hidden)
        menu.addSeparator()
        preview = menu.addAction(icon('eye'), 'Panel podglądu\tAlt+P')
        preview.setCheckable(True)
        preview.setChecked(self.preview.isVisible())
        preview.triggered.connect(self.toggle_preview)
        details = menu.addAction(icon('details'), 'Panel szczegółów\tAlt+Shift+P')
        details.setCheckable(True)
        details.setChecked(self.details.isVisible())
        details.triggered.connect(self.toggle_details)
        menu.addSeparator()
        themes = menu.addMenu('Motyw')
        group = QActionGroup(themes)
        for name, value in [('Jasny', 'light'), ('Ciemny', 'dark'), ('OLED — czysta czerń', 'oled')]:
            action = themes.addAction(name)
            action.setCheckable(True)
            action.setChecked(QApplication.instance().property('theme') == value)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, theme=value: self.set_theme(theme))
        self.popup(self.view_button, menu)

    def set_keep_running(self, enabled):
        """Keep the process alive as the D-Bus service so later windows open instantly; autostart it at login."""
        self.settings.setValue('keep_running', enabled)
        self.settings.sync()
        QApplication.instance().setQuitOnLastWindowClosed(not enabled)
        update_tray(enabled)
        autostart = Path(QStandardPaths.writableLocation(QStandardPaths.ConfigLocation)) / 'autostart' / 'fileexplorer-background.desktop'
        try:
            if enabled:
                autostart.parent.mkdir(parents=True, exist_ok=True)
                autostart.write_text('[Desktop Entry]\nType=Application\nName=Eksplorator plików (w tle)\n'
                                     f'Exec={desktop_exec([ROOT / "fileexplorer", "--dbus"])}\nIcon=folder\nNoDisplay=true\nX-KDE-autostart-phase=2\n'
                                     'X-GNOME-Autostart-enabled=true\n')
                self.status_text.setText('Eksplorator pozostanie w tle po zamknięciu okna i uruchomi się przy logowaniu.')
            else:
                autostart.unlink(missing_ok=True)
                self.status_text.setText('Wyłączono działanie w tle.')
        except OSError as exc:
            self.error(f'Nie można zapisać wpisu autostartu:\n{exc}')

    def set_theme(self, name):
        apply_theme(name)
        self.settings.setValue('theme', QApplication.instance().property('theme'))

    def toggle_hidden(self, checked):
        self.show_hidden = checked
        self.settings.setValue('show_hidden', checked)
        self.update_filter()

    def more_menu(self):
        menu = self.menu()
        if self.undo_stack or self.redo_stack:
            if self.undo_stack:
                menu.addAction(icon('undo'), 'Cofnij\tCtrl+Z', self.undo)
            if self.redo_stack:
                menu.addAction(icon('redo'), 'Ponów\tCtrl+Y', self.redo)
            menu.addSeparator()
        menu.addAction(icon('restore'), 'Zachowane wersje plików…', self.saved_file_versions)
        menu.addSeparator()
        background = menu.addAction('Działaj w tle po zamknięciu (natychmiastowy start)')
        background.setCheckable(True)
        background.setChecked(self.settings.value('keep_running', False, type=bool))
        background.toggled.connect(self.set_keep_running)
        menu.addSeparator()
        menu.addAction(icon('settings'), 'Ustawienia', self.open_settings)
        menu.addAction(icon('network'), 'Połącz z serwerem…\tCtrl+Shift+L', self.connect_to_server)
        menu.addSeparator()
        menu.addAction(icon('info'), 'Właściwości', self.properties)
        menu.addAction(icon('terminal'), 'Otwórz w terminalu', self.open_terminal)
        menu.addAction(icon('terminal'), 'Wbudowany terminal\tF4', self.toggle_terminal)
        menu.addAction(icon('refresh'), 'Odśwież\tF5', self.refresh)
        menu.addSeparator()
        menu.addAction(icon('select'), 'Zaznacz wszystko\tCtrl+A', self.select_all)
        menu.addAction(icon('select'), 'Odwróć zaznaczenie\tCtrl+Shift+A', lambda: self.page.invert_selection())
        menu.addSeparator()
        menu.addAction(icon('shield'), 'Otwórz jako administrator', self.run_as_admin)
        menu.addAction(icon('home'), 'Otwórz folder osobisty', lambda: self.page.navigate(str(HOME)))
        self.popup(self.more_button, menu)

    def saved_file_versions(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Zachowane wersje plików')
        dialog.resize(760, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('Poprzednie wersje nadpisanych plików pozostają dostępne także po zamknięciu okna. '
                               'Odzyskanie tworzy osobną kopię.', word_wrap=True))
        tree = QTreeWidget(dialog)
        tree.setHeaderLabels(['Plik docelowy', 'Zachowana wersja'])
        tree.setRootIsDecorated(False)
        for saved in overwrite_backup.list_backups():
            for payload, description in ((saved.previous, 'Wersja sprzed nadpisania'),
                                         (saved.current, 'Wersja cofniętej operacji')):
                if os.path.lexists(payload):
                    item = QTreeWidgetItem([str(saved.target), description])
                    item.setData(0, Qt.UserRole, (saved, payload))
                    tree.addTopLevelItem(item)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(tree, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        recover = buttons.addButton('Odzyskaj kopię', QDialogButtonBox.ActionRole)
        delete = buttons.addButton('Usuń zachowane wersje…', QDialogButtonBox.ActionRole)
        def selection():
            return tree.currentItem().data(0, Qt.UserRole) if tree.currentItem() else None
        def enabled():
            recover.setEnabled(bool(selection()) and not self.busy)
            delete.setEnabled(bool(selection()) and not self.busy)
        def restore():
            chosen = selection()
            if chosen and not self.busy:
                saved, payload = chosen
                target = unique_path(saved.target.parent, saved.target.name, 'odzyskana wersja')
                dialog.reject()
                self.run_transfer_prepared([(payload, target)])
        def discard():
            chosen = selection()
            if not chosen or self.busy:
                return
            saved, _ = chosen
            if QMessageBox.warning(dialog, 'Usuń zachowane wersje',
                    f'Trwale usunąć zachowane wersje pliku {saved.target.name}? '
                    'Cofanie i ponawianie jego nadpisania przestanie być dostępne.',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            dialog.reject()
            def remove():
                remove_entry(saved.directory)
                saved.index.unlink(missing_ok=True)
            self.run_job(remove)
        tree.itemSelectionChanged.connect(enabled)
        recover.clicked.connect(restore)
        delete.clicked.connect(discard)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        enabled()
        dialog.exec()
        dialog.deleteLater()

    def icon_row(self, menu, buttons):
        """Windows 11 style row of icon-only commands at the top of a context menu."""
        row = QWidget(menu)
        row.setObjectName('menuIconRow')
        layout = box(row, margins=(6, 4, 6, 4), spacing=2)
        layout.addStretch()
        for name, tip, callback, enabled in buttons:
            button = QToolButton(row)
            button.setObjectName('menuIconButton')
            button.setIcon(icon(name))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(38, 34)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setEnabled(enabled)
            button.clicked.connect(lambda checked=False, m=menu, c=callback: (m.close(), c()))
            layout.addWidget(button)
        layout.addStretch()
        action = QWidgetAction(menu)
        action.setDefaultWidget(row)
        menu.addAction(action)
        menu.addSeparator()

    def context_menu(self, global_pos):
        page = self.page
        if page.location == 'trash':
            self.trash_context_menu(global_pos)
            return
        paths = page.selected_paths()
        directory = page.current_dir()
        menu = self.menu()
        idle = not self.busy
        if paths:
            single = len(paths) == 1
            first = Path(paths[0])
            self.icon_row(menu, [
                ('cut', 'Wytnij (Ctrl+X)', lambda: self.copy(True), idle),
                ('copy', 'Kopiuj (Ctrl+C)', self.copy, True),
                ('paste', 'Wklej (Ctrl+V)', self.paste, idle and bool(directory) and clipboard_has_files()),
                ('rename', 'Zmień nazwę (F2)', self.rename, idle),
                ('share', 'Udostępnij (Ctrl+Shift+S)', self.share, True),
                ('path', 'Kopiuj ścieżkę (Ctrl+Alt+C)', self.copy_path, True),
                ('trash', 'Przenieś do kosza (Delete)', self.trash, idle),
            ])
            menu.addAction(icon('folder' if first.is_dir() else 'file'), 'Otwórz\tEnter', self.open_selected)
            mime, default_id, application_ids = self.applications_for_paths(paths)
            if default_id and default_id in application_ids:
                name, icon_name = self.desktop_application(default_id)
                menu.addAction(QIcon.fromTheme(icon_name, icon('file')) if icon_name else icon('file'),
                               f'Otwórz w: {name}', lambda: self.open_with_application(default_id))
            open_menu = menu.addMenu(icon('openwith'), 'Otwórz w')
            for desktop_id in application_ids:
                name, icon_name = self.desktop_application(desktop_id)
                open_menu.addAction(QIcon.fromTheme(icon_name, icon('file')) if icon_name else icon('file'),
                                    f'{name} (domyślna)' if desktop_id == default_id else name,
                                    lambda checked=False, app=desktop_id: self.open_with_application(app))
            if application_ids:
                defaults_menu = open_menu.addMenu('Ustaw jako domyślną')
                for desktop_id in application_ids:
                    name, icon_name = self.desktop_application(desktop_id)
                    action = defaults_menu.addAction(QIcon.fromTheme(icon_name, icon('file')) if icon_name else icon('file'), name,
                                                     lambda checked=False, app=desktop_id: self.set_default_application(mime, app))
                    action.setCheckable(True)
                    action.setChecked(desktop_id == default_id)
                open_menu.addSeparator()
            open_menu.addAction('Wybierz inną aplikację…', self.open_with_dialog)
            if single and first.is_dir():
                menu.addAction(icon('plus'), 'Otwórz w nowej karcie', lambda: self.add_tab(paths[0]))
            if page.searching and single:
                menu.addAction(icon('folder'), 'Otwórz lokalizację pliku', lambda: self.open_location(paths[0]))
            menu.addSeparator()
            favs = {str(p) for p in self.favorites()}
            remove = all(p in favs for p in paths)
            menu.addAction(icon('favorite' if remove else 'star'), 'Usuń z ulubionych' if remove else 'Dodaj do ulubionych', lambda: self.set_favorites(paths, remove))
            if directory:
                menu.addAction(icon('zip'), 'Kompresuj do archiwum…', self.archive).setEnabled(idle)
                if any(p.lower().endswith(tuple(ARCHIVE_SUFFIXES)) for p in paths):
                    menu.addAction(icon('extract'), 'Wyodrębnij wszystko…', lambda: self.extract_archive(dest_folder=True)).setEnabled(idle)
            menu.addAction(icon('path'), 'Kopiuj jako ścieżkę\tCtrl+Alt+C', self.copy_path)
            self.add_service_menus(menu, paths)
            tags = menu.addMenu(icon('star'), 'Tagi')
            if single:
                tags.addAction('Edytuj tagi…', lambda checked=False, selected=tuple(paths): self.edit_tags(selected)).setEnabled(idle)
            tags.addAction('Dodaj tagi…', lambda checked=False, selected=tuple(paths): self.edit_tags(selected, 'add')).setEnabled(idle)
            tags.addAction('Usuń tagi…', lambda checked=False, selected=tuple(paths): self.edit_tags(selected, 'remove')).setEnabled(idle)
            menu.addAction(icon('info'), 'Właściwości\tAlt+Enter', self.properties)
            menu.addSeparator()
            more = menu.addMenu(icon('more'), 'Pokaż więcej opcji')
            if directory:
                more.addAction(icon('duplicate'), 'Powiel tutaj\tCtrl+D', self.duplicate).setEnabled(idle)
                more.addAction(icon('move'), 'Przenieś do nowego katalogu…', self.move_to_new_folder).setEnabled(idle)
                more.addAction(icon('link'), 'Utwórz dowiązanie symboliczne', self.create_symlink).setEnabled(idle)
                if any(p.lower().endswith(tuple(ARCHIVE_SUFFIXES)) for p in paths):
                    more.addAction(icon('extract'), 'Rozpakuj tutaj', lambda: self.extract_archive(dest_folder=False)).setEnabled(idle)
                more.addSeparator()
            more.addAction(icon('folder'), 'Kopiuj położenie', lambda: self.copy_path(True))
            if single and first.is_dir():
                more.addAction(icon('terminal'), 'Otwórz w terminalu', lambda: self.open_terminal(paths[0]))
            elif len(paths) == 2 and all(Path(p).is_file() for p in paths):
                more.addAction(icon('compare'), 'Porównaj pliki', lambda: self.compare_files(paths[0], paths[1]))
            if single and is_image(paths[0]):
                more.addAction(icon('pictures'), 'Ustaw jako tapetę Plasma', self.set_wallpaper)
            more.addAction(icon('shield'), 'Otwórz jako administrator', self.run_as_admin)
            more.addSeparator()
            more.addAction(icon('trash'), 'Usuń trwale\tShift+Delete', self.delete_permanently).setEnabled(idle)
        else:
            self.icon_row(menu, [
                ('paste', 'Wklej (Ctrl+V)', self.paste, idle and bool(directory) and clipboard_has_files()),
                ('undo', 'Cofnij (Ctrl+Z)', self.undo, idle and bool(self.undo_stack)),
                ('redo', 'Ponów (Ctrl+Y)', self.redo, idle and bool(self.redo_stack)),
                ('plus', 'Nowy folder (Ctrl+Shift+N)', self.new_folder, idle and bool(directory)),
                ('details', 'Panel szczegółów', self.toggle_details, True),
            ])
            view = menu.addMenu(icon('view'), 'Wyświetl')
            self.fill_view_modes(view, page)
            view.addSeparator()
            hidden = view.addAction(icon('eye'), 'Elementy ukryte\tCtrl+H')
            hidden.setCheckable(True)
            hidden.setChecked(self.show_hidden)
            hidden.triggered.connect(self.toggle_hidden)
            if directory:
                sort = menu.addMenu(icon('sort'), 'Sortuj według')
                self.fill_sort_menu(sort)
            menu.addAction(icon('refresh'), 'Odśwież\tF5', self.refresh)
            menu.addSeparator()
            if directory:
                new = menu.addMenu(icon('plus'), 'Nowy')
                self.fill_new_menu(new)
                menu.addSeparator()
                menu.addAction(icon('terminal'), 'Otwórz w terminalu', self.open_terminal)
                self.add_service_menus(menu, [directory])
            menu.addSeparator()
            more = menu.addMenu(icon('more'), 'Pokaż więcej opcji')
            more.addAction(icon('select'), 'Zaznacz wszystko\tCtrl+A', self.select_all)
            more.addAction(icon('select'), 'Odwróć zaznaczenie\tCtrl+Shift+A', lambda: page.invert_selection())
            more.addSeparator()
            more.addAction(icon('terminal'), 'Wbudowany terminal\tF4', self.toggle_terminal)
            more.addAction(icon('split'), 'Widok podzielony\tF3', self.toggle_split_view)
            more.addSeparator()
            more.addAction(icon('shield'), 'Otwórz jako administrator', self.run_as_admin)
        menu.popup(global_pos)

    def favorites(self):
        return [Path(p) for p in self.settings.value('favorites', [], type=list) if Path(p).exists()]

    def set_favorites(self, paths, remove):
        values = [str(p) for p in self.favorites()]
        values = [p for p in values if p not in paths] if remove else list(dict.fromkeys(values + paths))
        self.settings.setValue('favorites', values)
        if self.page and self.page.location == 'home':
            self.page.build_home()
        self.build_sidebar()

    def recent_files(self):
        path = Path(QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)) / 'recently-used.xbel'
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return []
        if self.recent_cache[0] == stamp:
            return [p for p in self.recent_cache[1] if p.is_file()]
        result = []
        try:
            bookmarks = ET.parse(path).getroot().findall('bookmark')
            bookmarks.sort(key=lambda b: b.get('visited', b.get('modified', '')), reverse=True)
            for bookmark in bookmarks:
                url = QUrl(bookmark.get('href', ''))
                if url.isLocalFile():
                    target = Path(url.toLocalFile())
                    if target.is_file():
                        result.append(target)
                        if len(result) == 30:
                            break
        except (OSError, ET.ParseError):
            pass
        self.recent_cache = (stamp, result)
        return result

    def toggle_preview(self):
        visible = self.preview.isHidden()
        self.preview.setVisible(visible)
        self.preview_button.setChecked(visible)
        self.settings.setValue('preview_visible', visible)
        if visible:
            self.preview.show_paths(self.page.selected_paths() if self.page else [])

    def toggle_details(self):
        visible = not self.details.isVisible()
        self.details.setVisible(visible)
        self.details_button.setChecked(visible)
        if visible:
            self.fill_details()

    def fill_details(self):
        paths = self.page.selected_paths()
        path = Path(paths[0]) if paths else Path(self.page.current_dir()) if self.page.current_dir() else None
        key = (tuple(paths), str(path) if path else '', self.page.location)
        same = key == self.details_key
        if same and (getattr(self, '_details_loading', False) or time.monotonic() - getattr(self, '_details_last_check', 0) < 1):
            return
        self.details_key = key
        self._details_generation = getattr(self, '_details_generation', 0) + 1
        generation = self._details_generation
        previous = getattr(self, '_details_cancel', None)
        if previous is not None:
            previous.set()
        if not same:
            self._details_signature = None
            self._render_details(path, len(paths), None)
        if path is None:
            return
        self._details_loading = True
        self._details_last_check = time.monotonic()

        def ready(result):
            if not alive(self) or self._details_generation != generation:
                return
            self._details_loading = False
            if not self.details.isVisible() or result['kind'] in ('unchanged', 'cancelled'):
                return
            self._details_signature = result.get('signature')
            self._render_details(path, len(paths), result)

        self._details_cancel = load_preview_async(path, ready, owner=self, text_suffixes=TEXT_SUFFIXES,
                                                  previous_signature=getattr(self, '_details_signature', None))

    def _render_details(self, path, count, result):
        while self.details_layout.count():
            item = self.details_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.details_layout.addWidget(label('Szczegóły', 'detailsTitle'))
        if path is None:
            self.details_layout.addWidget(label(title_for(self.page.location), 'sectionTitle'))
            self.details_layout.addWidget(label('Wybierz plik, aby wyświetlić jego szczegóły.', 'muted', True))
        elif result is None:
            self.details_layout.addWidget(label(path.name, 'sectionTitle', True))
            self.details_layout.addWidget(label('Przygotowywanie szczegółów…', 'muted'))
        elif result['kind'] == 'error':
            self.details_layout.addWidget(label('Element jest niedostępny.', 'muted'))
        else:
            metadata = result['metadata']
            preview = QLabel()
            preview.setAlignment(Qt.AlignCenter)
            preview.setFixedHeight(150)
            if result['kind'] == 'text':
                preview.setObjectName('textPreview')
                preview.setTextFormat(Qt.PlainText)
                preview.setWordWrap(True)
                text = result['text']
                preview.setText(text[:500] + ('…' if len(text) > 500 else ''))
            elif result['kind'] in ('image', 'pdf'):
                preview.setPixmap(QPixmap.fromImage(result['image']).scaled(220, 145, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                preview.setPixmap(QIcon.fromTheme(metadata['icon'], icon('file')).pixmap(80, 80))
            self.details_layout.addWidget(preview)
            self.details_layout.addWidget(label(path.name or 'System plików', 'sectionTitle', True))
            if count > 1:
                self.details_layout.addWidget(label(f'Zaznaczone elementy: {count}', 'muted'))
            directory = stat_module.S_ISDIR(metadata['mode'])
            rows = [('Typ', 'Folder plików' if directory else metadata['comment'] or path.suffix.upper().lstrip('.') or 'Plik'),
                    ('Lokalizacja', network.display_location(str(path.parent))),
                    ('Data modyfikacji', format_date(metadata['mtime'])),
                    ('Rozmiar', '—' if directory else size_text(metadata['size']))]
            if metadata.get('dimensions'):
                width, height = metadata['dimensions']
                rows.insert(1, ('Wymiary', f'{width} × {height} px'))
            for name, value in rows:
                self.details_layout.addWidget(label(name, 'muted'))
                self.details_layout.addWidget(label(value, word_wrap=True))
        self.details_layout.addStretch()

    def open_settings(self):
        dialog = SettingsDialog(self)
        dialog.exec()

    def properties(self, *_signal_args, paths=None):
        paths = self.page.selected_paths() if paths is None else paths
        target = paths[0] if paths else self.page.current_dir()
        if not target:
            return
        path = Path(target)
        try:
            stat = path.stat()
        except OSError as exc:
            self.error(exc)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f'Właściwości: {path.name or "/"}')
        dialog.setMinimumSize(610, 465)
        layout = box(dialog, vertical=True, margins=(22, 20, 22, 18), spacing=14)
        header = QWidget()
        header_layout = box(header, margins=(2, 0, 2, 0), spacing=12)
        picture = QLabel()
        picture.setPixmap(self.provider.icon(QFileInfo(str(path))).pixmap(42, 42))
        header_layout.addWidget(picture)
        header_layout.addWidget(label(path.name or 'System plików', 'detailsTitle', True), 1)
        layout.addWidget(header)
        tabs = QTabWidget()
        general = QWidget()
        form = QFormLayout(general)
        form.setContentsMargins(14, 16, 14, 16)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        mime_type = QMimeDatabase().mimeTypeForFile(str(path), QMimeDatabase.MatchContent)
        common_types = {'text/plain': 'Dokument tekstowy', 'application/pdf': 'Dokument PDF',
                        'application/zip': 'Archiwum ZIP', 'application/x-tar': 'Archiwum TAR'}
        item_type = 'Dowiązanie symboliczne' if path.is_symlink() else 'Folder plików' if path.is_dir() else common_types.get(mime_type.name(), mime_type.comment() or f'Plik {path.suffix.upper()}')
        form.addRow('Typ:', label(item_type))
        path_row = QWidget()
        path_layout = box(path_row, margins=(0, 0, 0, 0), spacing=7)
        path_edit = QLineEdit(str(path))
        path_edit.setReadOnly(True)
        path_edit.setCursorPosition(0)
        path_edit.setAccessibleName('Pełna ścieżka pliku')
        path_layout.addWidget(path_edit, 1)
        copy_button = QPushButton(icon('copy'), 'Kopiuj ścieżkę')
        def copy_value(value, button, initial):
            QApplication.clipboard().setText(value)
            button.setText('Skopiowano')
            QTimer.singleShot(1300, lambda: button.setText(initial))
        copy_button.clicked.connect(lambda: copy_value(str(path), copy_button, 'Kopiuj ścieżkę'))
        path_layout.addWidget(copy_button)
        form.addRow('Pełna ścieżka:', path_row)
        uri = QUrl.fromLocalFile(str(path)).toString(QUrl.FullyEncoded)
        uri_row = QWidget()
        uri_layout = box(uri_row, margins=(0, 0, 0, 0), spacing=7)
        uri_edit = QLineEdit(uri)
        uri_edit.setReadOnly(True)
        uri_edit.setCursorPosition(0)
        uri_layout.addWidget(uri_edit, 1)
        copy_uri = QPushButton(icon('copy'), 'Kopiuj URI')
        copy_uri.clicked.connect(lambda: copy_value(uri, copy_uri, 'Kopiuj URI'))
        uri_layout.addWidget(copy_uri)
        form.addRow('Adres URI:', uri_row)
        if path.is_dir():
            size_label = label('Obliczanie…', 'muted')
            statistics = FileJob(lambda: None)
            statistics.cancelled = False

            def compute():
                statistics.result = self.directory_statistics(path, should_stop=lambda: statistics.cancelled)
            statistics.operation = compute

            def show_size(error):
                self.background_jobs.discard(statistics)
                if error or not alive(size_label) or statistics.cancelled:
                    return
                size, count, exceeded = statistics.result
                size_label.setText(f'{size_text(size)} w {count} elementach' + (' (co najmniej)' if exceeded else ''))
                size_label.setObjectName('')
            statistics.signals.finished.connect(show_size)
            dialog.finished.connect(lambda *_: setattr(statistics, 'cancelled', True))
            self.start_job(statistics)
            form.addRow('Rozmiar:', size_label)
        else:
            form.addRow('Rozmiar:', label(f'{size_text(stat.st_size)} ({stat.st_size:,} bajtów)'.replace(',', ' ')))
        form.addRow('Położenie:', label(str(path.parent), word_wrap=True))
        form.addRow('Zmodyfikowano:', label(datetime.fromtimestamp(stat.st_mtime).strftime('%d.%m.%Y %H:%M:%S')))
        form.addRow('Ostatni dostęp:', label(datetime.fromtimestamp(stat.st_atime).strftime('%d.%m.%Y %H:%M:%S')))
        if path.is_symlink():
            try:
                form.addRow('Wskazuje na:', label(os.readlink(path), word_wrap=True))
            except OSError:
                pass
        tabs.addTab(general, 'Ogólne')
        permissions = QWidget()
        permissions_layout = box(permissions, vertical=True, margins=(18, 18, 18, 18), spacing=10)
        permissions_layout.addWidget(label('Uprawnienia POSIX', 'sectionTitle'))
        permissions_layout.addWidget(label('Zmień dostęp do bieżącego elementu. Zmiana zostanie zastosowana po kliknięciu „Zastosuj”.', 'muted', True))
        permission_grid = QGridLayout()
        permission_grid.setHorizontalSpacing(16)
        permission_grid.setVerticalSpacing(8)
        permission_grid.addWidget(label(''), 0, 0)
        permission_boxes = []
        for column, text in enumerate(('Odczyt', 'Zapis', 'Wykonanie'), 1):
            permission_grid.addWidget(label(text), 0, column)
        for row, (name, bits) in enumerate((('Właściciel', (0o400, 0o200, 0o100)), ('Grupa', (0o040, 0o020, 0o010)), ('Pozostali', (0o004, 0o002, 0o001))), 1):
            permission_grid.addWidget(label(name), row, 0)
            for column, bit in enumerate(bits, 1):
                checkbox = QCheckBox()
                checkbox.setChecked(bool(stat.st_mode & bit))
                checkbox.setAccessibleName(f'{name}: {text}')
                checkbox.setToolTip(f'{name}: {text}')
                checkbox.setMinimumWidth(60)
                permission_grid.addWidget(checkbox, row, column)
                permission_boxes.append((checkbox, bit))
        permissions_layout.addLayout(permission_grid)
        permissions_layout.addSpacing(8)
        permissions_layout.addWidget(label(f'Bieżący tryb: {stat_module.filemode(stat.st_mode)}   ({oct(stat.st_mode & 0o777)})'))
        admin_button = QPushButton('Otwórz folder w trybie administratora')
        admin_button.clicked.connect(lambda: self.run_as_admin(path if path.is_dir() else path.parent))
        permissions_layout.addWidget(admin_button)
        permissions_layout.addStretch()
        tabs.addTab(permissions, 'Uprawnienia')
        metadata = QWidget()
        metadata_form = QFormLayout(metadata)
        metadata_form.setContentsMargins(14, 16, 14, 16)
        metadata_form.setHorizontalSpacing(18)
        metadata_form.setVerticalSpacing(12)
        try:
            owner = pwd.getpwuid(stat.st_uid).pw_name
        except KeyError:
            owner = str(stat.st_uid)
        try:
            group = grp.getgrgid(stat.st_gid).gr_name
        except KeyError:
            group = str(stat.st_gid)
        for name, value in [('Właściciel:', owner), ('Grupa:', group), ('Typ MIME:', mime_type.name()), ('Urządzenie:', str(stat.st_dev)), ('Numer i-węzła:', str(stat.st_ino)), ('Tryb:', f'{stat_module.filemode(stat.st_mode)} ({oct(stat.st_mode & 0o777)})'), ('Zmiana metadanych:', datetime.fromtimestamp(stat.st_ctime).strftime('%d.%m.%Y %H:%M:%S'))]:
            metadata_form.addRow(name, label(value, word_wrap=True))
        tabs.addTab(metadata, 'Metadane')
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close | QDialogButtonBox.Apply)
        buttons.button(QDialogButtonBox.Close).setText('Zamknij')
        apply_button = buttons.button(QDialogButtonBox.Apply)
        apply_button.setText('Zastosuj')
        if path.is_symlink():
            apply_button.setEnabled(False)
            apply_button.setToolTip('Uprawnienia dowiązań symbolicznych nie są zmieniane tutaj.')
        def apply_permissions():
            mode = stat.st_mode & ~0o777
            for checkbox, bit in permission_boxes:
                if checkbox.isChecked():
                    mode |= bit
            try:
                os.chmod(path, mode)
                self.refresh()
                QMessageBox.information(dialog, 'Właściwości', 'Uprawnienia zostały zaktualizowane.')
            except OSError as exc:
                self.error(exc)
        apply_button.clicked.connect(apply_permissions)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

    @staticmethod
    def directory_statistics(path, limit=500000, should_stop=None):
        total = count = 0
        for root, directories, files in os.walk(path, followlinks=False):
            if should_stop and should_stop():
                return total, count, True
            for filename in files:
                count += 1
                try:
                    total += os.lstat(os.path.join(root, filename)).st_size
                except OSError:
                    pass
            count += len(directories)
            if count >= limit:
                return total, count, True
        return total, count, False

    def toggle_maximize(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()
        self.max_button.setIcon(icon('restore' if self.isMaximized() else 'maximize'))

    def closeEvent(self, event):
        if self.busy:
            self.error('Poczekaj na zakończenie operacji na plikach przed zamknięciem okna.')
            event.ignore()
            return
        self.save_session()
        for page in self.all_pages():
            page.stop_search(show_view=False)
        QApplication.clipboard().dataChanged.disconnect(self.schedule_status)
        self.mount_timer.stop()
        self.status_timer.stop()
        self.terminal.shutdown()
        self.stop_share_server()
        super().closeEvent(event)


TRAY = {'icon': None}


WINDOWS = set()  # top-level windows are owned by Python: keep them referenced until destroyed


def new_window(location='home', pos=None):
    """A brand-new explorer window (detached tab, "open in new window"), registered with the D-Bus service if any."""
    window = Explorer(location, restore_session=False)
    WINDOWS.add(window)
    window.destroyed.connect(lambda obj=None, w=window: WINDOWS.discard(w))
    service = QApplication.instance().property('service')
    if service is not None:
        service.windows.append(window)
        window.destroyed.connect(lambda obj=None, w=window, s=service: s.windows.remove(w) if w in s.windows else None)
    if pos is not None:
        window.move(pos)
    window.show()
    return window


def open_window(location='home'):
    """Open (or raise) an explorer window from the tray or the D-Bus service."""
    service = QApplication.instance().property('service')
    if service is not None:
        service.show([location] if location != 'home' else [])
        return
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, Explorer) and widget.isVisible():
            widget.page.navigate(location)
            widget.raise_()
            widget.activateWindow()
            return
    new_window(location)


def quit_background():
    app = QApplication.instance()
    for widget in app.topLevelWidgets():
        if isinstance(widget, Explorer):
            widget.close()
    app.setQuitOnLastWindowClosed(True)
    QTimer.singleShot(0, app.quit)


def update_tray(enabled):
    """Show a tray icon while the explorer runs in the background, so the user can see it and stop it."""
    app = QApplication.instance()
    tray = TRAY['icon']
    if not enabled:
        if tray is not None:
            tray.hide()
            tray.deleteLater()
            TRAY['icon'] = None
        return
    if tray is not None or not QSystemTrayIcon.isSystemTrayAvailable():
        return
    tray = TRAY['icon'] = QSystemTrayIcon(icon('folder'), app)
    tray.setToolTip('Eksplorator plików działa w tle — kliknij, aby otworzyć okno')
    menu = QMenu()
    menu.addAction(icon('folder'), 'Otwórz okno', lambda: open_window('home'))
    menu.addAction(icon('home'), 'Folder osobisty', lambda: open_window(str(HOME)))
    menu.addAction(icon('downloads'), 'Pobrane', lambda: open_window(str(FOLDERS[1][2])))
    menu.addSeparator()
    menu.addAction(icon('close'), 'Zakończ działanie w tle', quit_background)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: open_window('home') if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()


def create_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('FileExplorer')
    app.setApplicationDisplayName('Eksplorator plików')
    app.setDesktopFileName('fileexplorer')
    app.setStyle('Fusion')
    translator = QTranslator(app)
    for directory in (QLibraryInfo.path(QLibraryInfo.TranslationsPath), '/usr/share/qt6/translations', '/usr/lib/qt6/translations'):
        if translator.load(QLocale('pl'), 'qtbase', '_', directory):
            app.installTranslator(translator)
            break
    for name in ('segoeui.ttf', 'segoeuib.ttf'):
        font = Path('/mnt/windows/Windows/Fonts') / name
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont('Segoe UI', 10))
    apply_theme(QSettings('Damian', 'FileExplorer').value('theme', 'light'))
    app.aboutToQuit.connect(release_clipboard)
    return app


def release_clipboard():
    """A QMimeData created in Python must not outlive the interpreter, or Qt's exit handlers crash freeing it.

    Copied file paths stay available as plain text (KDE's Klipper keeps the original entry anyway).
    """
    clipboard = QApplication.clipboard()
    mime = clipboard.mimeData()
    try:
        from shiboken6 import createdByPython
        ours = mime is not None and createdByPython(mime)
    except ImportError:
        ours = clipboard.ownsClipboard()
    if not ours:
        return
    paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()] if mime.hasUrls() else []
    clipboard.clear()
    if paths:
        clipboard.setText('\n'.join(paths))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('paths', nargs='*')
    parser.add_argument('--select', action='store_true')
    parser.add_argument('--dbus', action='store_true')
    parser.add_argument('--settings', action='store_true', help='otwórz bezpośrednio panel ustawień')
    parser.add_argument('--screenshot', metavar='PNG')
    parser.add_argument('--no-restore', action='store_true', help='nie przywracaj kart z poprzedniej sesji')
    args = parser.parse_args()
    if args.settings:
        from settings_app import main as settings_main
        settings_args = ['--screenshot', args.screenshot] if args.screenshot else []
        sys.exit(settings_main(settings_args))
    mark('argumenty przetworzone (importy gotowe)')
    locations = []
    for value in args.paths:
        try:
            locations.append(value if value in VIRTUAL or QUrl(value).scheme() in ('trash', *REMOTE_SCHEMES) else local_path(value))
        except ValueError as exc:
            print(f'FileExplorer: {exc}', file=sys.stderr)
    app = create_app()
    mark('QApplication, czcionki i motyw')
    if args.screenshot:
        window = Explorer(locations[0] if locations else 'home')
        window.show()
        def capture():
            window.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(1800, capture)
    else:
        service = FileManagerService(Explorer, app, restore_session=not args.no_restore, job_factory=FileJob)
        app.setProperty('service', service)
        keep_running = args.dbus or QSettings('Damian', 'FileExplorer').value('keep_running', False, type=bool)
        app.setQuitOnLastWindowClosed(not keep_running)
        registered = service.register(args.dbus or is_default())
        mark(f'rejestracja D-Bus: {"ok" if registered else "usługa zajęta"}')
        if not registered:
            if args.dbus and not locations:
                sys.exit(0)  # a background instance is already running; nothing to show
            forwarded = forward_to_running(locations, args.select)
            mark(f'przekazanie do działającej instancji: {"ok" if forwarded else "brak odpowiedzi"}')
            if forwarded:
                sys.exit(0)
            app.setQuitOnLastWindowClosed(True)  # a second, service-less process must not linger
        if keep_running and registered:
            update_tray(True)
        if not args.dbus or locations:
            service.show(locations, select=args.select)
            mark('okno utworzone i pokazane')
            if not service.windows and not keep_running and not any(QUrl(str(p)).scheme() in REMOTE_SCHEMES for p in locations):
                sys.exit(0)
            QTimer.singleShot(0, lambda: mark('pierwsze zdarzenia obsłużone (okno widoczne)'))
    sys.exit(app.exec())
