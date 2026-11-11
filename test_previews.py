"""Read-only preview workers and format coverage; no real user profile or associations."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import wave
import zipfile
from unittest.mock import Mock, patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, QSettings, QThreadPool, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPdfWriter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from shiboken6 import isValid
import main
import preview_panel as preview


class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = tempfile.TemporaryDirectory()
        settings = QSettings(str(Path(cls.profile.name) / 'settings.ini'), QSettings.IniFormat)
        with patch('main.QSettings', return_value=settings):
            cls.app = main.create_app()

    @classmethod
    def tearDownClass(cls):
        cls.profile.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.panel = preview.PreviewPanel(main.TEXT_SUFFIXES)
        self.panel.resize(350, 500)
        self.panel.show()
        QTest.qWait(10)

    def tearDown(self):
        if isValid(self.panel):
            self.panel.close()
            self.panel.deleteLater()
        QThreadPool.globalInstance().waitForDone(3000)
        self.app.processEvents()
        self.temporary.cleanup()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            QTest.qWait(5)
        return predicate()

    def show(self, path):
        self.panel.show_paths([str(path)])
        self.assertTrue(self.wait_until(lambda: not self.panel._loading))

    def office(self, suffix, entries):
        path = self.root / ('document' + suffix)
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return path

    def test_slow_read_does_not_block_gui_and_latest_selection_wins(self):
        old, new = self.root / 'old.txt', self.root / 'new.txt'
        old.write_text('old result')
        new.write_text('new result')
        release, started = threading.Event(), threading.Event()
        actual = preview.prepare_preview
        threads, ticks = [], []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(5)
        def prepare(path, *args, **kwargs):
            threads.append(threading.get_ident())
            if Path(path) == old:
                started.set()
                release.wait(3)
            return actual(path, *args, **kwargs)
        try:
            with patch('preview_panel.prepare_preview', side_effect=prepare):
                self.panel.show_paths([str(old)])
                self.assertTrue(self.wait_until(lambda: started.is_set() and ticks))
                self.panel.show_paths([str(new)])
                self.assertTrue(self.wait_until(lambda: self.panel.text.toPlainText() == 'new result'))
                release.set()
                self.assertTrue(self.wait_until(lambda: not preview._active_jobs))
        finally:
            release.set()
            timer.stop()
        self.assertEqual(self.panel.text.toPlainText(), 'new result')
        self.assertTrue(all(identifier != threading.get_ident() for identifier in threads))

    def test_image_decode_is_in_worker_and_pixmap_creation_in_gui(self):
        path = self.root / 'wide.png'
        image = QImage(3200, 1000, QImage.Format_RGB32)
        image.fill(QColor('red'))
        image.save(str(path))
        decode_threads, pixmap_threads = [], []
        reader, from_image = preview.QImageReader, QPixmap.fromImage
        def read(*args):
            decode_threads.append(threading.get_ident())
            return reader(*args)
        def pixmap(*args):
            pixmap_threads.append(threading.get_ident())
            return from_image(*args)
        with patch('preview_panel.QImageReader', side_effect=read), patch('preview_panel.QPixmap.fromImage', side_effect=pixmap):
            self.show(path)
        self.assertIs(self.panel.content.currentWidget(), self.panel.image)
        self.assertLessEqual(self.panel.image.original.width(), preview.IMAGE_LIMIT.width())
        self.assertTrue(all(identifier != threading.get_ident() for identifier in decode_threads))
        self.assertEqual(pixmap_threads, [threading.get_ident()])

    def test_owner_deletion_cancels_and_discards_late_result(self):
        path = self.root / 'file.txt'
        path.write_text('text')
        release, started = threading.Event(), threading.Event()
        actual = preview.prepare_preview
        def delayed(*args, **kwargs):
            started.set()
            release.wait(3)
            return actual(*args, **kwargs)
        try:
            with patch('preview_panel.prepare_preview', side_effect=delayed):
                self.panel.show_paths([str(path)])
                self.assertTrue(self.wait_until(started.is_set))
                cancel = self.panel._cancel
                self.panel.deleteLater()
                QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                self.assertTrue(cancel.is_set())
                release.set()
                self.assertTrue(self.wait_until(lambda: not preview._active_jobs))
        finally:
            release.set()

    def test_bounded_utf16_text_and_explicit_refresh_of_changed_file(self):
        path = self.root / 'large.txt'
        path.write_bytes(('ą' * (preview.TEXT_LIMIT + 100)).encode('utf-16'))
        self.show(path)
        self.assertLessEqual(len(self.panel.text.toPlainText()), preview.TEXT_LIMIT)
        self.assertIn('256 KiB', self.panel.note.text())
        path.write_text('changed')
        self.panel.show_paths([str(path)], force=True)
        self.assertTrue(self.wait_until(lambda: self.panel.text.toPlainText() == 'changed'))

    def test_multiple_selection_clears_image_and_pending_content(self):
        path = self.root / 'text.txt'
        path.write_text('visible')
        self.show(path)
        self.panel.show_paths([str(path), str(path)])
        self.assertIs(self.panel.content.currentWidget(), self.panel.message)
        self.assertEqual(self.panel.text.toPlainText(), '')
        self.assertIn('Zaznaczone elementy: 2', self.panel.message.text())

    def test_pdf_navigation_reaches_every_page(self):
        path = self.root / 'pages.pdf'
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        for index, color in enumerate(('red', 'green', 'blue')):
            if index:
                writer.newPage()
            painter.fillRect(0, 0, writer.width(), writer.height(), QColor(color))
        painter.end()
        del writer
        self.show(path)
        self.assertEqual(self.panel._pdf_pages, 3)
        self.assertEqual(self.panel.note.text(), 'Strona 1 z 3')
        first = self.panel.image.original.toImage().pixelColor(200, 200)
        self.panel.next_page.click()
        self.assertTrue(self.wait_until(lambda: self.panel.note.text() == 'Strona 2 z 3'))
        second = self.panel.image.original.toImage().pixelColor(200, 200)
        self.assertNotEqual(first, second)
        self.panel.page_number.setValue(3)
        self.assertTrue(self.wait_until(lambda: self.panel.note.text() == 'Strona 3 z 3'))
        self.assertFalse(self.panel.next_page.isEnabled())
        self.panel.previous_page.click()
        self.assertTrue(self.wait_until(lambda: self.panel.note.text() == 'Strona 2 z 3'))

    def test_audio_loads_without_autoplay_and_unloads_when_selection_changes(self):
        from PySide6.QtMultimedia import QMediaPlayer
        path = self.root / 'sound.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b'\0\0' * 800)
        self.show(path)
        self.assertIs(self.panel.content.currentWidget(), self.panel.media_page)
        self.assertEqual(self.panel.media_player.source().toLocalFile(), str(path))
        self.assertEqual(self.panel.media_player.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.panel.show_paths([])
        self.assertTrue(self.panel.media_player.source().isEmpty())
        self.assertEqual(self.panel.media_player.playbackState(), QMediaPlayer.PlaybackState.StoppedState)

    def test_video_uses_video_widget_and_does_not_autoplay(self):
        path = self.root / 'sample.mp4'
        path.write_bytes(b'\0\0\0\x18ftypmp42' + b'\0' * 24)
        self.show(path)
        self.assertIs(self.panel.content.currentWidget(), self.panel.media_page)
        self.assertFalse(self.panel.video_widget.isHidden())
        self.assertEqual(self.panel.play_button.text(), 'Odtwórz')

    def test_archive_listing_is_bounded_and_does_not_extract_members(self):
        entries = {'../outside.txt': 'never extract'}
        entries.update({f'file-{index}.txt': 'data' for index in range(510)})
        path = self.office('.zip', entries)
        before = set(self.root.iterdir())
        self.show(path)
        self.assertIs(self.panel.content.currentWidget(), self.panel.text)
        self.assertEqual(len(self.panel.text.toPlainText().splitlines()), 500)
        self.assertIn('pierwsze 500', self.panel.note.text())
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertFalse((self.root.parent / 'outside.txt').exists())

    def test_docx_and_odf_extract_plain_text_only(self):
        examples = [('.docx', 'word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Hello Word</w:t></w:r></w:p></w:document>', 'Hello Word'),
                    ('.odt', 'content.xml', '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><text:p>Hello ODF</text:p></office:document>', 'Hello ODF')]
        for suffix, entry, xml, expected in examples:
            with self.subTest(suffix=suffix):
                self.show(self.office(suffix, {entry: xml}))
                self.assertIn(expected, self.panel.text.toPlainText())

    def test_xlsx_displays_values_and_pptx_displays_slide_text(self):
        sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c t="inlineStr"><is><t>Cell text</t></is></c><c><f>EXTERNAL()</f><v>42</v></c></row></sheetData></worksheet>'
        self.show(self.office('.xlsx', {'xl/worksheets/sheet1.xml': sheet}))
        self.assertIn('Cell text\t42', self.panel.text.toPlainText())
        self.assertNotIn('EXTERNAL()', self.panel.text.toPlainText())
        slide = '<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><a:t>Slide content</a:t></p:sld>'
        self.show(self.office('.pptx', {'ppt/slides/slide1.xml': slide}))
        self.assertIn('Slajd 1\nSlide content', self.panel.text.toPlainText())

    def test_xml_external_entity_and_oversize_member_are_rejected(self):
        xml = '<!DOCTYPE root [<!ENTITY external SYSTEM "file:///must-not-be-read">]><root>&external;</root>'
        self.show(self.office('.docx', {'word/document.xml': xml}))
        self.assertIs(self.panel.content.currentWidget(), self.panel.message)
        oversized = '<root>' + 'x' * preview.OFFICE_LIMIT + '</root>'
        self.show(self.office('.odt', {'content.xml': oversized}))
        self.assertIn('limit', self.panel.note.text())

    def test_details_image_metadata_is_read_in_worker(self):
        path = self.root / 'image.png'
        image = QImage(120, 45, QImage.Format_RGB32)
        image.fill(QColor('blue'))
        image.save(str(path))
        settings = QSettings(str(self.root / 'window.ini'), QSettings.IniFormat)
        with patch('main.QSettings', return_value=settings):
            window = main.Explorer(str(self.root))
        window.show()
        try:
            window.page.selected_paths = lambda: [str(path)]
            window.toggle_details()
            self.assertTrue(self.wait_until(lambda: '120 × 45 px' in [label.text() for label in window.details.findChildren(QLabel)]))
        finally:
            window.close()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
