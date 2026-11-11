"""Read-only previews: file access and rendering run in cancellable workers."""
from __future__ import annotations
import codecs
import ctypes
import os
from pathlib import Path
import re
import stat
import threading
import time
import weakref
import xml.etree.ElementTree as ET
import zipfile
from PySide6.QtCore import QEvent, QMimeDatabase, QObject, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QFontDatabase, QImageReader, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                              QSizePolicy, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)
from shiboken6 import isValid

TEXT_LIMIT = 256 * 1024
IMAGE_LIMIT = QSize(1600, 1200)
OFFICE_LIMIT = 8 * 1024 * 1024
OFFICE_SUFFIXES = {'.docx', '.xlsx', '.pptx', '.odt', '.ods', '.odp', '.rtf'}
ARCHIVE_SUFFIXES = {'.zip', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.txz', '.tbz2', '.7z', '.rar', '.zst'}
_active_jobs = set()


def _check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError('Podgląd anulowany')


def _read_bounded(path, limit, cancel):
    chunks = []
    with path.open('rb') as stream:
        remaining = limit + 1
        while remaining:
            _check_cancel(cancel)
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    return b''.join(chunks)


def _decode_text(data):
    encoding = 'utf-8-sig'
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = 'utf-32'
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = 'utf-16'
    return data.decode(encoding, errors='replace')


def _office_text(path, cancel):
    if path.suffix.lower() == '.rtf':
        source = _read_bounded(path, TEXT_LIMIT, cancel).decode('latin-1')
        encoding = re.search(r'\\ansicpg(\d+)', source)
        codec = 'cp' + encoding[1] if encoding else 'cp1252'
        try:
            codecs.lookup(codec)
        except LookupError:
            codec = 'cp1252'
        source = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m[1]).decode(codec, errors='replace'), source)
        source = re.sub(r'\\(?:par|line)\b\s?', '\n', source)
        source = re.sub(r'\\[a-zA-Z]+-?\d*\s?', '', source)
        return source.replace('{', '').replace('}', '').replace('\\\\', '\\')[:TEXT_LIMIT]
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('Dokument jest zbyt duży do podglądu tekstu.')
    pieces, remaining = [], TEXT_LIMIT
    with zipfile.ZipFile(path) as document:
        def xml(name):
            _check_cancel(cancel)
            entry = document.getinfo(name)
            if entry.file_size > OFFICE_LIMIT:
                raise ValueError('Zawartość dokumentu przekracza limit podglądu.')
            with document.open(entry) as stream:
                data = stream.read(OFFICE_LIMIT + 1)
            _check_cancel(cancel)
            if len(data) > OFFICE_LIMIT:
                raise ValueError('Zawartość dokumentu przekracza limit podglądu.')
            return ET.fromstring(data)
        def append(text):
            nonlocal remaining
            _check_cancel(cancel)
            if remaining > 0:
                text = text[:remaining]
                pieces.append(text)
                remaining -= len(text) + 1
        suffix = path.suffix.lower()
        if suffix == '.docx':
            for paragraph in xml('word/document.xml').iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
                append(''.join(element.text or '' for element in paragraph.iter() if element.tag.endswith('}t')))
        elif suffix == '.pptx':
            names = [name for name in document.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', name)]
            for name in sorted(names, key=lambda s: int(re.search(r'slide(\d+)', s)[1])):
                append('Slajd ' + re.search(r'slide(\d+)', name)[1])
                for element in xml(name).iter():
                    if element.tag.endswith('}t'):
                        append(element.text or '')
                if remaining <= 0:
                    break
        elif suffix == '.xlsx':
            shared = []
            if 'xl/sharedStrings.xml' in document.namelist():
                for item in xml('xl/sharedStrings.xml'):
                    shared.append(''.join(e.text or '' for e in item.iter() if e.tag.endswith('}t')))
            names = [name for name in document.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name)]
            for name in sorted(names, key=lambda s: int(re.search(r'sheet(\d+)', s)[1])):
                append('Arkusz ' + re.search(r'sheet(\d+)', name)[1])
                for row in xml(name).iter():
                    if not row.tag.endswith('}row'):
                        continue
                    cells = []
                    for cell in row:
                        value = next((e.text or '' for e in cell if e.tag.endswith('}v')), '')
                        if cell.get('t') == 's' and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                        elif cell.get('t') == 'inlineStr':
                            value = ''.join(e.text or '' for e in cell.iter() if e.tag.endswith('}t'))
                        cells.append(value)
                    append('\t'.join(cells))
                    if remaining <= 0:
                        break
                if remaining <= 0:
                    break
        else:
            for paragraph in xml('content.xml').iter():
                if paragraph.tag in ('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p',
                                     '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}h'):
                    append(''.join(paragraph.itertext()))
    return '\n'.join(pieces)[:TEXT_LIMIT]


def _archive_text(path, cancel):
    from archive_formats import archive_library
    library = archive_library()
    library.archive_entry_size.restype = ctypes.c_int64
    library.archive_entry_size.argtypes = [ctypes.c_void_p]
    reader = library.archive_read_new()
    if not reader:
        raise MemoryError('Nie można utworzyć czytnika archiwum.')
    rows = []
    try:
        for result in (library.archive_read_support_filter_all(reader), library.archive_read_support_format_all(reader),
                       library.archive_read_open_filename(reader, os.fsencode(path), 10240)):
            if result < 0:
                raise ValueError('Nie można odczytać archiwum.')
        header = ctypes.c_void_p()
        while len(rows) <= 500:
            _check_cancel(cancel)
            result = library.archive_read_next_header(reader, ctypes.byref(header))
            if result == 1:
                break
            if result < 0:
                raise ValueError('Nie można odczytać archiwum. Plik może być uszkodzony lub chroniony hasłem.')
            name = library.archive_entry_pathname(header)
            size = library.archive_entry_size(header)
            rows.append(f'{os.fsdecode(name) if name else "?"}\t{size:,} B'.replace(',', ' '))
    finally:
        library.archive_read_free(reader)
    return '\n'.join(rows[:500]), len(rows) > 500


def prepare_preview(path, text_suffixes=(), cancel=None, pdf_page=0, previous_signature=None):
    """Worker-only: returns ordinary values and QImage, never QWidget or QPixmap."""
    cancel = cancel or threading.Event()
    path = Path(path)
    _check_cancel(cancel)
    info = path.stat()
    signature = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size, pdf_page)
    if signature == previous_signature:
        return {'kind': 'unchanged', 'signature': signature}
    mode = QMimeDatabase.MatchDefault if stat.S_ISREG(info.st_mode) else QMimeDatabase.MatchExtension
    mime = QMimeDatabase().mimeTypeForFile(str(path), mode)
    metadata = {'path': str(path), 'size': info.st_size, 'mtime': info.st_mtime, 'mode': info.st_mode,
                'mime': mime.name(), 'comment': mime.comment(), 'icon': mime.iconName()}
    result = {'kind': 'metadata', 'metadata': metadata, 'signature': signature, 'note': ''}
    _check_cancel(cancel)
    if not stat.S_ISREG(info.st_mode):
        return result
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        try:
            from PySide6.QtPdf import QPdfDocument
        except ImportError:
            result['note'] = 'Podgląd PDF wymaga QtPdf.'
            return result
        document = QPdfDocument()
        try:
            if document.load(str(path)) != QPdfDocument.Error.None_ or not document.pageCount():
                raise ValueError('Nie można wyświetlić tego PDF. Sprawdź, czy nie wymaga hasła.')
            page = max(0, min(pdf_page, document.pageCount() - 1))
            _check_cancel(cancel)
            image = document.render(page, document.pagePointSize(page).toSize().scaled(IMAGE_LIMIT, Qt.KeepAspectRatio))
            _check_cancel(cancel)
            if image.isNull():
                raise ValueError('Nie można wyświetlić strony PDF.')
            result.update(kind='pdf', image=image, pages=document.pageCount(), page=page,
                          note=f'Strona {page + 1} z {document.pageCount()}')
        finally:
            document.close()
        return result
    if mime.name().startswith('image/'):
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        dimensions = reader.size()
        if dimensions.isValid():
            reader.setScaledSize(dimensions.scaled(IMAGE_LIMIT, Qt.KeepAspectRatio))
            metadata['dimensions'] = (dimensions.width(), dimensions.height())
        _check_cancel(cancel)
        image = reader.read()
        _check_cancel(cancel)
        if image.isNull():
            result['note'] = 'Nie można wyświetlić obrazu.'
        else:
            result.update(kind='image', image=image,
                          note=f'{dimensions.width()} × {dimensions.height()} px' if dimensions.isValid() else '')
        return result
    if mime.name().startswith(('audio/', 'video/')) and suffix not in ('.m3u', '.m3u8', '.pls', '.xspf'):
        result.update(kind='media', video=mime.name().startswith('video/'))
        return result
    if suffix in OFFICE_SUFFIXES:
        result.update(kind='text', text=_office_text(path, cancel), note='Podgląd tekstu dokumentu.')
        return result
    if suffix in ARCHIVE_SUFFIXES or mime.name() in ('application/zip', 'application/x-7z-compressed', 'application/vnd.rar'):
        text, truncated = _archive_text(path, cancel)
        result.update(kind='text', text=text, note='Zawartość archiwum — pierwsze 500 elementów.' if truncated else 'Zawartość archiwum.')
        return result
    if suffix in text_suffixes or mime.inherits('text/plain'):
        data = _read_bounded(path, TEXT_LIMIT, cancel)
        text = _decode_text(data[:TEXT_LIMIT])
        if '\0' in text:
            result['note'] = 'Plik zawiera dane binarne.'
        else:
            result.update(kind='text', text=text, note='Podgląd pierwszych 256 KiB pliku.' if len(data) > TEXT_LIMIT else '')
    return result


class PreviewSignals(QObject):
    finished = Signal(object)


class PreviewJob(QRunnable):
    def __init__(self, path, text_suffixes, cancel, pdf_page, previous_signature):
        super().__init__()
        self.setAutoDelete(False)
        self.signals = PreviewSignals()
        self.path, self.text_suffixes, self.cancel = path, text_suffixes, cancel
        self.pdf_page, self.previous_signature = pdf_page, previous_signature

    def run(self):
        try:
            result = prepare_preview(self.path, self.text_suffixes, self.cancel, self.pdf_page, self.previous_signature)
        except InterruptedError:
            result = {'kind': 'cancelled'}
        except Exception as exc:
            result = {'kind': 'error', 'error': str(exc)}
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            _active_jobs.discard(self)


def load_preview_async(path, callback, *, owner, text_suffixes=(), pdf_page=0, previous_signature=None):
    event = threading.Event()
    job = PreviewJob(str(path), tuple(text_suffixes), event, pdf_page, previous_signature)
    owner_ref = weakref.ref(owner)
    cancel = lambda *_: event.set()
    owner.destroyed.connect(cancel)
    _active_jobs.add(job)
    def finished(result):
        _active_jobs.discard(job)
        target = owner_ref()
        if target is None or not isValid(target):
            return
        target.destroyed.disconnect(cancel)
        if not event.is_set():
            callback(result)
    job.signals.finished.connect(finished)
    QThreadPool.globalInstance().start(job)
    return event


class PreviewImage(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.original = QPixmap()

    def set_image(self, image):
        self.original = QPixmap.fromImage(image)
        self.fit_image()

    def fit_image(self):
        if self.original.isNull():
            self.clear()
        else:
            self.setPixmap(self.original.scaled(self.contentsRect().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_image()


class PreviewPanel(QWidget):
    TEXT_LIMIT = TEXT_LIMIT
    IMAGE_LIMIT = IMAGE_LIMIT

    def __init__(self, text_suffixes=(), parent=None):
        super().__init__(parent)
        self.setObjectName('previewPane')
        self.setMinimumWidth(250)
        self.setMaximumWidth(600)
        self.text_suffixes = frozenset(text_suffixes)
        self._paths = ()
        self._generation = 0
        self._cancel = None
        self._signature = None
        self._last_check = 0
        self._loading = False
        self._pdf_page = self._pdf_pages = 0
        self.media_player = self.audio_output = self.video_widget = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 14)
        self.title = QLabel('Podgląd')
        self.title.setObjectName('detailsTitle')
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.content = QStackedWidget()
        layout.addWidget(self.content, 1)
        self.message = QLabel('Wybierz plik, aby wyświetlić podgląd.')
        self.message.setObjectName('muted')
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignCenter)
        self.content.addWidget(self.message)
        self.image = PreviewImage()
        self.content.addWidget(self.image)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setObjectName('fileTextPreview')
        self.text.installEventFilter(self)
        self.content.addWidget(self.text)
        self.metadata = QLabel()
        self.metadata.setTextFormat(Qt.PlainText)
        self.metadata.setWordWrap(True)
        self.metadata.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self.metadata.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.metadata_page = QScrollArea()
        self.metadata_page.setWidgetResizable(True)
        self.metadata_page.setFrameShape(QScrollArea.NoFrame)
        self.metadata_page.setWidget(self.metadata)
        self.content.addWidget(self.metadata_page)
        self.pdf_controls = QWidget()
        row = QHBoxLayout(self.pdf_controls)
        row.setContentsMargins(0, 0, 0, 0)
        self.previous_page = QPushButton('‹')
        self.previous_page.setAccessibleName('Poprzednia strona PDF')
        self.next_page = QPushButton('›')
        self.next_page.setAccessibleName('Następna strona PDF')
        self.page_number = QSpinBox()
        self.page_number.setPrefix('Strona ')
        self.page_number.setAccessibleName('Numer strony PDF')
        self.previous_page.clicked.connect(lambda: self.set_pdf_page(self._pdf_page - 1))
        self.next_page.clicked.connect(lambda: self.set_pdf_page(self._pdf_page + 1))
        self.page_number.valueChanged.connect(lambda number: self.set_pdf_page(number - 1))
        row.addWidget(self.previous_page)
        row.addWidget(self.page_number, 1)
        row.addWidget(self.next_page)
        self.pdf_controls.hide()
        layout.addWidget(self.pdf_controls)
        self.media_page = QWidget()
        self.media_layout = QVBoxLayout(self.media_page)
        self.media_layout.setContentsMargins(0, 0, 0, 0)
        self.media_title = QLabel('Podgląd audio/wideo')
        self.media_title.setTextFormat(Qt.PlainText)
        self.media_title.setAlignment(Qt.AlignCenter)
        self.media_layout.addWidget(self.media_title)
        self.content.addWidget(self.media_page)
        self.note = QLabel()
        self.note.setObjectName('muted')
        self.note.setTextFormat(Qt.PlainText)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def eventFilter(self, obj, event):
        if obj is self.text and event.type() == QEvent.ShortcutOverride:
            if event.key() in (Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Return, Qt.Key_Enter) or (
                    event.modifiers() & Qt.ControlModifier and event.key() in (
                        Qt.Key_A, Qt.Key_C, Qt.Key_X, Qt.Key_V, Qt.Key_Z, Qt.Key_Y)):
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _stop_media(self):
        if self.media_player is not None:
            self.media_player.stop()
            self.media_player.setSource(QUrl())

    def _clear(self):
        self._stop_media()
        self.text.clear()
        self.image.original = QPixmap()
        self.image.clear()
        self.metadata.clear()
        self.note.clear()
        self.pdf_controls.hide()
        self.content.setCurrentWidget(self.message)

    def cancel(self):
        self._generation += 1
        if self._cancel is not None:
            self._cancel.set()
        self._loading = False
        self._last_check = 0

    def hideEvent(self, event):
        self.cancel()
        self._stop_media()
        super().hideEvent(event)

    def show_paths(self, paths, force=False):
        paths = tuple(str(path) for path in paths)
        same = paths == self._paths
        if same and not force and (self._loading or time.monotonic() - self._last_check < 1):
            return
        self.cancel()
        self._paths = paths
        if not same:
            self._signature = None
            self._pdf_page = self._pdf_pages = 0
            self._clear()
        path = Path(paths[0]) if len(paths) == 1 else None
        self.title.setText(path.name if path else 'Podgląd')
        self.title.setToolTip(str(path) if path else '')
        if not paths or path is None:
            self._clear()
            self.message.setText('Wybierz plik, aby wyświetlić podgląd.' if not paths else
                                 f'Zaznaczone elementy: {len(paths)}.\nWybierz jeden plik, aby wyświetlić podgląd.')
            return
        if self._signature is None:
            self.message.setText('Przygotowywanie podglądu…')
        generation = self._generation
        self._loading = True
        self._last_check = time.monotonic()
        owner_ref = weakref.ref(self)
        def ready(result):
            panel = owner_ref()
            if panel is not None and isValid(panel) and panel._generation == generation:
                panel._loading = False
                panel._display_result(result)
        self._cancel = load_preview_async(path, ready, owner=self, text_suffixes=self.text_suffixes,
                                          pdf_page=self._pdf_page, previous_signature=self._signature)

    def _display_result(self, result):
        kind = result['kind']
        if kind in ('unchanged', 'cancelled'):
            return
        if kind == 'error':
            self._clear()
            self.message.setText('Nie można przygotować podglądu.')
            self.note.setText(result['error'])
            return
        self._signature = result['signature']
        metadata = result['metadata']
        self.metadata.setText('\n\n'.join((
            f'Typ: {"Folder" if stat.S_ISDIR(metadata["mode"]) else metadata["comment"] or metadata["mime"]}',
            f'Rozmiar: {metadata["size"]:,} B'.replace(',', ' '),
            f'Lokalizacja: {Path(metadata["path"]).parent}',
            f'Uprawnienia: {stat.filemode(metadata["mode"])}')))
        self.pdf_controls.hide()
        self.note.setText(result.get('note', ''))
        if kind in ('image', 'pdf'):
            self._stop_media()
            self.image.set_image(result['image'])
            self.content.setCurrentWidget(self.image)
            if kind == 'pdf':
                self._pdf_page, self._pdf_pages = result['page'], result['pages']
                self.page_number.blockSignals(True)
                self.page_number.setRange(1, self._pdf_pages)
                self.page_number.setValue(self._pdf_page + 1)
                self.page_number.blockSignals(False)
                self.previous_page.setEnabled(self._pdf_page > 0)
                self.next_page.setEnabled(self._pdf_page + 1 < self._pdf_pages)
                self.pdf_controls.show()
        elif kind == 'text':
            self._stop_media()
            self.text.setPlainText(result['text'])
            self.content.setCurrentWidget(self.text)
        elif kind == 'media' and self._ensure_media():
            self._stop_media()
            self.video_widget.setVisible(result['video'])
            self.media_title.setVisible(not result['video'])
            self.media_player.setSource(QUrl.fromLocalFile(metadata['path']))
            self.content.setCurrentWidget(self.media_page)
            self.note.setText('Naciśnij Odtwórz, aby rozpocząć.')
        else:
            self._stop_media()
            self.content.setCurrentWidget(self.metadata_page)

    def set_pdf_page(self, index):
        if not self._pdf_pages or not 0 <= index < self._pdf_pages or index == self._pdf_page:
            return
        self._pdf_page = index
        self.show_paths(self._paths, force=True)

    def _ensure_media(self):
        if self.media_player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
        except ImportError:
            self.note.setText('Podgląd audio/wideo wymaga QtMultimedia.')
            return False
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.7)
        self.media_player.setAudioOutput(self.audio_output)
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(1, 1)
        self.media_layout.insertWidget(0, self.video_widget, 1)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_seek = QSlider(Qt.Horizontal)
        self.media_seek.setAccessibleName('Pozycja odtwarzania')
        self.media_seek.sliderMoved.connect(self.media_player.setPosition)
        self.media_player.positionChanged.connect(lambda pos: self.media_seek.setValue(min(pos, 2147483647)))
        self.media_player.durationChanged.connect(lambda duration: self.media_seek.setRange(0, min(duration, 2147483647)))
        self.media_layout.addWidget(self.media_seek)
        controls = QHBoxLayout()
        self.play_button = QPushButton('Odtwórz')
        self.stop_button = QPushButton('Zatrzymaj')
        self.play_button.clicked.connect(lambda: self.media_player.pause() if
                                        self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                                        else self.media_player.play())
        self.stop_button.clicked.connect(self.media_player.stop)
        self.media_player.playbackStateChanged.connect(lambda state: self.play_button.setText(
            'Pauza' if state == QMediaPlayer.PlaybackState.PlayingState else 'Odtwórz'))
        self.media_player.errorOccurred.connect(lambda *_: self.note.setText('Nie można odtworzyć pliku: ' + self.media_player.errorString()))
        controls.addWidget(self.play_button)
        controls.addWidget(self.stop_button)
        self.media_layout.addLayout(controls)
        return True
