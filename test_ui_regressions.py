"""Keyboard, split-pane, search lifecycle and external drag regressions.

Run: QT_QPA_PLATFORM=offscreen python test_ui_regressions.py
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp(prefix='fileexplorer-ui-config-')

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QTreeWidgetItem

QSettings.setDefaultFormat(QSettings.IniFormat)
import main


class UIRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or main.create_app()
        main.apply_theme('dark')

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='fileexplorer-ui-')
        self.root = Path(self.work.name)
        QSettings('Damian', 'FileExplorer').clear()
        self.window = main.Explorer(str(self.root))
        self.window.show()
        QTest.qWait(80)

    def tearDown(self):
        self.window.close()
        QTest.qWait(50)
        self.work.cleanup()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            QTest.qWait(10)
        return predicate()

    def test_terminal_keyboard_executes_and_ctrl_l_clears(self):
        self.window.toggle_terminal()
        terminal = self.window.terminal
        terminal.input.setFocus()
        terminal.input.setText('echo FILEEXPLORER_KEYBOARD_RESULT')
        QTest.keyClick(terminal.input, Qt.Key_Return)
        self.assertEqual(terminal.input.text(), '')
        self.assertTrue(self.wait_until(
            lambda: terminal.output.toPlainText().count('FILEEXPLORER_KEYBOARD_RESULT') == 2))
        QTest.keyClick(terminal.input, Qt.Key_L, Qt.ControlModifier)
        self.assertEqual(terminal.output.toPlainText(), '')
        self.assertIs(self.app.focusWidget(), terminal.input)
        self.assertFalse(self.window.address_edit.isVisible())

    def test_keyboard_focus_changes_active_split_pane(self):
        self.window.toggle_split_view()
        container = self.window.pages.currentWidget()
        self.assertIs(container.active_page, container.right_page)
        container.left_page.tree.setFocus()
        self.app.processEvents()
        self.assertIs(container.active_page, container.left_page)
        container.right_page.set_mode('large')
        container.right_page.grid.setFocus()
        self.app.processEvents()
        self.assertIs(container.active_page, container.right_page)

    def test_terminal_output_does_not_trigger_file_operation_shortcuts(self):
        self.window.toggle_terminal()
        output = self.window.terminal.output
        output.setFocus()
        shortcuts = {shortcut.key().toString(): shortcut
                     for shortcut in self.window.findChildren(QShortcut)}
        for key, modifier, combo in (
                (Qt.Key_Delete, Qt.NoModifier, 'Del'),
                (Qt.Key_Backspace, Qt.NoModifier, 'Backspace'),
                (Qt.Key_Z, Qt.ControlModifier, 'Ctrl+Z'),
                (Qt.Key_V, Qt.ControlModifier, 'Ctrl+V')):
            spy = QSignalSpy(shortcuts[combo].activated)
            QTest.keyClick(output, key, modifier)
            self.assertEqual(spy.count(), 0, combo)
        output.setPlainText('terminal output')
        QTest.keyClick(output, Qt.Key_L, Qt.ControlModifier)
        self.assertEqual(output.toPlainText(), '')
        self.assertFalse(self.window.address_edit.isVisible())

    def test_empty_folder_message_does_not_capture_pointer(self):
        page = self.window.page
        self.assertTrue(self.wait_until(page.empty_label.isVisible))
        point = page.empty_label.mapToGlobal(page.empty_label.rect().center())
        self.assertIs(self.app.widgetAt(point), page.tree.viewport())

    def test_preview_shortcut_selection_updates_and_setting_restore(self):
        path = self.root / 'preview.txt'
        path.write_text('<b>Tekst pozostaje zwykłym tekstem.</b>\n', encoding='utf-8')
        page = self.window.page
        self.assertTrue(self.wait_until(lambda: self.window.model.index(str(path)).isValid()))
        page.select_paths([str(path)])
        self.assertTrue(self.wait_until(lambda: page.selected_paths() == [str(path)]))
        page.tree.setFocus()
        QTest.keyClick(page.tree, Qt.Key_P, Qt.AltModifier)
        preview = self.window.preview
        self.assertTrue(preview.isVisible())
        self.assertTrue(self.window.preview_button.isChecked())
        self.assertFalse(self.window.details.isVisible())
        self.assertTrue(self.wait_until(lambda: preview.text.toPlainText() == path.read_text()))
        self.assertIs(preview.content.currentWidget(), preview.text)
        self.assertTrue(self.window.settings.value('preview_visible', False, type=bool))
        extra = main.Explorer(str(self.root))
        try:
            extra.show()
            self.assertTrue(extra.preview.isVisible())
            self.assertTrue(extra.preview_button.isChecked())
        finally:
            extra.close()
            QTest.qWait(30)
        page.tree.clearSelection()
        self.assertTrue(self.wait_until(lambda: preview.content.currentWidget() is preview.message))
        self.assertEqual(preview.text.toPlainText(), '')
        self.assertIn('Wybierz plik', preview.message.text())

    def test_preview_text_is_bounded_and_does_not_modify_file(self):
        path = self.root / 'large.txt'
        data = b'a' * (self.window.preview.TEXT_LIMIT + 150)
        path.write_bytes(data)
        stamp = path.stat().st_mtime_ns
        preview = self.window.preview
        preview.show_paths([str(path)])
        self.assertTrue(self.wait_until(lambda: len(preview.text.toPlainText()) == preview.TEXT_LIMIT))
        self.assertIn('256 KiB', preview.note.text())
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(path.stat().st_mtime_ns, stamp)

    def test_preview_image_scales_and_multiple_selection_clears_content(self):
        from PySide6.QtGui import QImage, QColor
        path = self.root / 'wide.png'
        image = QImage(240, 80, QImage.Format_RGB32)
        image.fill(QColor('red'))
        image.save(str(path))
        preview = self.window.preview
        preview.show_paths([str(path)])
        self.assertTrue(self.wait_until(lambda: preview.content.currentWidget() is preview.image))
        self.window.page.select_paths([str(path)])
        self.assertTrue(self.wait_until(lambda: self.window.page.selected_paths() == [str(path)]))
        self.window.toggle_preview()
        QTest.qWait(20)
        pixmap = preview.image.pixmap()
        self.assertFalse(pixmap.isNull())
        self.assertLessEqual(pixmap.width(), preview.image.width())
        self.assertLessEqual(pixmap.height(), preview.image.height())
        self.assertLessEqual(abs(pixmap.width() - 3 * pixmap.height()), 3)
        preview.show_paths([str(path), str(self.root / 'second.png')])
        self.assertIs(preview.content.currentWidget(), preview.message)
        self.assertTrue(preview.image.original.isNull())
        self.assertIn('Zaznaczone elementy: 2', preview.message.text())

    def test_preview_pdf_first_page_and_binary_metadata(self):
        try:
            from PySide6.QtPdf import QPdfDocument
        except ImportError:
            self.skipTest('QtPdf jest opcjonalny')
        from PySide6.QtGui import QPainter, QPdfWriter
        path = self.root / 'document.pdf'
        writer = QPdfWriter(str(path))
        painter = QPainter(writer)
        painter.drawText(20, 60, 'Preview PDF')
        painter.end()
        del writer
        preview = self.window.preview
        preview.show_paths([str(path)])
        self.assertTrue(self.wait_until(lambda: preview.content.currentWidget() is preview.image))
        self.assertFalse(preview.image.original.isNull())
        self.assertEqual(preview.note.text(), 'Strona 1 z 1')
        binary = self.root / 'binary.bin'
        binary.write_bytes(b'\x00\x01\x02')
        preview.show_paths([str(binary)])
        self.assertTrue(self.wait_until(lambda: preview.content.currentWidget() is preview.metadata_page))
        self.assertIn('Rozmiar: 3 B', preview.metadata.text())

    def populate_results(self):
        page = self.window.page
        page.ensure_results()
        page.searching = True
        page.stack.setCurrentWidget(page.results_page)
        for name in ('a.txt', 'b.txt'):
            path = self.root / name
            path.write_text(name)
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.UserRole, str(path))
            page.results.addTopLevelItem(item)
        return page

    def test_select_all_and_invert_target_visible_search_results(self):
        page = self.populate_results()
        page.select_all()
        self.assertEqual(len(page.selected_paths()), 2)
        page.invert_selection()
        self.assertEqual(page.selected_paths(), [])
        page.results.topLevelItem(0).setSelected(True)
        page.invert_selection()
        self.assertEqual(page.selected_paths(), [str(self.root / 'b.txt')])

    def test_search_result_drag_exposes_file_urls(self):
        page = self.populate_results()
        page.results.selectAll()
        mime = page.results.mimeData(page.results.selectedItems())
        self.assertTrue(mime.hasUrls())
        self.assertEqual([url.toLocalFile() for url in mime.urls()],
                         [str(self.root / 'a.txt'), str(self.root / 'b.txt')])

    def test_gallery_select_all_and_mode_keep_gallery_visible(self):
        from PySide6.QtGui import QImage
        path = self.root / 'photo.png'
        QImage(10, 10, QImage.Format_RGB32).save(str(path))
        with patch.object(main, 'FOLDERS', [*main.FOLDERS[:3], ('Obrazy', 'pictures', self.root), *main.FOLDERS[4:]]):
            page = self.window.page
            page.navigate('gallery')
            self.assertTrue(self.wait_until(lambda: page.proxy.rowCount(page.gallery_grid.rootIndex()) == 1))
            page.select_all()
            self.assertEqual(page.selected_paths(), [str(path)])
            page.set_mode('medium')
            self.assertIs(page.stack.currentWidget(), page.gallery)

    def test_completed_and_cancelled_search_jobs_are_released(self):
        page = self.window.page
        with patch.object(self.window, 'start_job', side_effect=self.window.background_jobs.add):
            page.set_search('first')
            first = page.search_job
            page.set_search('second')
            second = page.search_job
            self.assertTrue(first.cancelled)
            first.signals.done.emit(0, False)
            self.assertNotIn(first, self.window.background_jobs)
            self.assertIs(page.search_job, second)
            second.signals.done.emit(0, False)
            self.assertNotIn(second, self.window.background_jobs)
            self.assertIsNone(page.search_job)

    def test_closing_split_tab_or_window_cancels_search(self):
        with patch.object(self.window, 'start_job', side_effect=self.window.background_jobs.add):
            self.window.toggle_split_view()
            page = self.window.page
            page.set_search('pending')
            job = page.search_job
            self.window.toggle_split_view()
            self.assertTrue(job.cancelled)
            self.assertIsNone(page.search_job)
            self.window.add_tab(str(self.root))
            page = self.window.page
            page.set_search('pending')
            job = page.search_job
            self.window.close_tab(self.window.tabs.currentIndex())
            self.assertTrue(job.cancelled)
            self.assertIsNone(page.search_job)
            self.window.page.set_search('pending')
            job = self.window.page.search_job
            self.window.close()
            self.assertTrue(job.cancelled)


if __name__ == '__main__':
    unittest.main()
