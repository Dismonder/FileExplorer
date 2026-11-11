"""Smoke test: drives the real window offscreen through navigation and file operations.

Run:  QT_QPA_PLATFORM=offscreen python test_explorer.py
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QPoint, QPointF, Qt, QTimer, QSettings
from PySide6.QtGui import QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit, QMessageBox

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
QSettings.setDefaultFormat(QSettings.IniFormat)
os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp(prefix='fileexplorer-config-')

import main  # noqa: E402

FAILURES = []


def check(condition, message):
    if not condition:
        FAILURES.append(message)
        print('FAIL:', message)


def wait(ms=150):
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.start(ms)
    while deadline.isActive():
        QApplication.processEvents()


def wait_until(predicate, timeout=5000):
    elapsed = 0
    while elapsed < timeout:
        if predicate():
            return True
        wait(50)
        elapsed += 50
    return predicate()


def run_tests():
    try:
        work = Path(tempfile.mkdtemp(prefix='fileexplorer-test-'))
        main.TRASH_ROOT = work / 'Trash'
        (work / 'src').mkdir()
        (work / 'src' / 'a.txt').write_text('alpha')
        (work / 'src' / 'b.txt').write_text('beta' * 1000)
        (work / 'src' / 'sub').mkdir()
        (work / 'src' / 'sub' / 'c.txt').write_text('gamma')
        os.symlink('a.txt', work / 'src' / 'link.txt')
        (work / 'dst').mkdir()
        (work / 'pics').mkdir()
        main.FOLDERS[3] = ('Obrazy', 'pictures', work / 'pics')  # gallery collection independent of the real home

        explorer = main.Explorer(str(work / 'src'))
        explorer.show()
        wait()
        page = explorer.page
        check(page.location == str(work / 'src'), 'initial location')

        print('Testing navigation...')
        for location in ('computer', 'trash', 'gallery', 'home', str(work / 'src')):
            check(page.navigate(location), f'navigate {location}')
        check(not page.navigate(str(work / 'missing')), 'navigate to missing dir fails')
        check(page.gallery is not None and page.trash_tree is not None, 'lazy pages built on first visit')
        page.travel(-1)
        check(page.location == 'home', 'travel back')
        page.travel(1)
        check(page.location == str(work / 'src'), 'travel forward')

        print('Testing sorting and search...')
        wait_until(lambda: page.proxy.rowCount(page.tree.rootIndex()) == 4)
        check(page.proxy.rowCount(page.tree.rootIndex()) == 4, 'four entries listed')
        names = [page.proxy.data(page.proxy.index(i, 0, page.tree.rootIndex())) for i in range(4)]
        check(names[0] == 'sub', f'folders first: {names}')
        page.set_search('c.t')
        check(page.searching and page.stack.currentWidget() is page.results_page, 'search shows results view')
        check(wait_until(lambda: page.search_job is None and page.results.topLevelItemCount() == 1), 'recursive search found nested file')
        check(page.results.topLevelItem(0).text(1) == 'sub', 'result shows relative folder')
        check(page.selected_paths() == [], 'no selection in results yet')
        page.results.selectAll()
        check(page.selected_paths() == [str(work / 'src' / 'sub' / 'c.txt')], 'results selection maps to path')
        page.set_search('*.txt')
        check(wait_until(lambda: page.search_job is None and page.results.topLevelItemCount() == 4), f'wildcard search: {page.results.topLevelItemCount()}')
        page.set_search('')
        wait()
        check(not page.searching and page.stack.currentWidget() is page.tree, 'clearing search restores view')
        page.set_mode('icons')
        check(page.proxy.show_thumbnails, 'icons mode enables thumbnails')
        from PySide6.QtGui import QImage, QColor, QIcon
        image = QImage(64, 48, QImage.Format_RGB32)
        image.fill(QColor('red'))
        image.save(str(work / 'src' / 'photo.png'))
        wait_until(lambda: page.proxy.rowCount(page.tree.rootIndex()) == 5)
        source = explorer.model.index(str(work / 'src' / 'photo.png'))
        index = page.proxy.mapFromSource(source)
        check(index.isValid(), 'image row present')
        page.proxy.data(index, Qt.DecorationRole)  # queues the background decode
        check(wait_until(lambda: page.proxy.thumbnails), 'thumbnail decoded in background')
        thumb = page.proxy.data(index, Qt.DecorationRole)
        check(isinstance(thumb, QIcon) and not thumb.isNull() and thumb.pixmap(32, 32).toImage().pixelColor(16, 16).red() > 200, 'thumbnail shows image content')
        (work / 'src' / 'photo.png').unlink()
        wait_until(lambda: page.proxy.rowCount(page.tree.rootIndex()) == 4)
        page.set_mode('details')

        print('Testing copy/paste with progress...')
        page.tree.selectAll()
        explorer.copy()
        page.navigate(str(work / 'dst'))
        explorer.paste()
        check(wait_until(lambda: not explorer.busy), 'paste finished')
        check((work / 'dst' / 'a.txt').read_text() == 'alpha', 'file copied')
        check((work / 'dst' / 'sub' / 'c.txt').exists(), 'directory copied recursively')
        check(os.path.islink(work / 'dst' / 'link.txt'), 'symlink copied as symlink')
        check(wait_until(lambda: sorted(Path(p).name for p in page.selected_paths()) == sorted(p.name for p in (work / 'dst').iterdir())),
              'pasted entries selected afterwards (Windows behaviour)')

        print('Testing click-on-selected rename and rubber band...')
        check(page.grid.isSelectionRectVisible(), 'rubber band visible in icon grid')
        check(page.tree.editTriggers() == main.QAbstractItemView.SelectedClicked, 'click on selected item renames')
        page.navigate(str(work / 'src'))
        explorer.reveal_file(str(work / 'src' / 'a.txt'), page)
        check(wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'a.txt')]), 'a.txt selected')
        index = page.proxy.mapFromSource(explorer.model.index(str(work / 'src' / 'a.txt')))
        QTest.mouseClick(page.tree.viewport(), Qt.LeftButton, Qt.NoModifier, page.tree.visualRect(index).center())
        check(wait_until(lambda: page.tree.findChild(QLineEdit) is not None, timeout=3000), 'single click on selected name opens editor')
        QTest.keyClick(page.tree.findChild(QLineEdit), Qt.Key_Escape)
        QTest.qWait(50)
        sub = page.proxy.mapFromSource(explorer.model.index(str(work / 'src' / 'sub')))
        centre = page.tree.visualRect(sub).center()
        QTest.mouseClick(page.tree.viewport(), Qt.LeftButton, Qt.NoModifier, centre)  # selects 'sub'
        QTest.mouseClick(page.tree.viewport(), Qt.LeftButton, Qt.NoModifier, centre)  # click on selected: edit timer starts
        QTest.mouseDClick(page.tree.viewport(), Qt.LeftButton, Qt.NoModifier, centre)  # …but a double click wins
        check(wait_until(lambda: page.location == str(work / 'src' / 'sub')), 'double click on a selected folder opens it')
        QTest.qWait(600)
        check(page.tree.findChild(QLineEdit) is None, 'double click does not leave an editor behind')
        page.navigate(str(work / 'dst'))

        print('Testing Windows view modes...')
        page.navigate(str(work / 'src'))
        for mode in main.VIEW_MODES:
            page.set_mode(mode)
            expected = page.tree if mode == 'details' else page.grid
            check(page.stack.currentWidget() is expected, f'mode {mode} shows the right view')
            check(page.grid.iconSize().width() == main.VIEW_MODES[mode][2], f'mode {mode} icon size')
            pixmap = page.stack.currentWidget().grab()
            check(not pixmap.isNull(), f'mode {mode} renders')
        check(page.grid.itemDelegateForColumn(0) is page.delegates['content'], 'content mode uses its delegate')
        for mode in main.VIEW_MODES:
            page.set_mode(mode)
            view = page.stack.currentWidget()
            check(view.dragEnabled() and view.viewport().acceptDrops(), f'mode {mode} keeps drag and drop enabled')
            selection = page.tree.selectionModel()
            selection.clearSelection()
            index = page.proxy.mapFromSource(explorer.model.index(str(work / 'src' / 'a.txt')))
            selection.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
            start = view.visualRect(index).center()
            QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, start)
            QTest.mouseMove(view.viewport(), start + QPoint(3, 3))
            QTest.mouseMove(view.viewport(), start + QPoint(40, 60))
            dragging = view.state() != view.State.DragSelectingState
            QTest.mouseRelease(view.viewport(), Qt.LeftButton, Qt.NoModifier, start + QPoint(40, 60))
            check(dragging, f'mode {mode}: dragging a selected item starts a drag, not a rubber band')
        page.set_mode('tiles')
        check(page.grid.itemDelegateForColumn(0) is page.delegates['tiles'] and page.grid.gridSize().width() == 270, 'tiles delegate + grid size')
        check(page.proxy.show_thumbnails, 'tiles show thumbnails')
        page.set_mode('list')
        check(page.grid.flow() == page.grid.Flow.TopToBottom and page.grid.isWrapping() and not page.proxy.show_thumbnails, 'list flows in columns without thumbnails')
        page.cycle_mode(1)
        check(page.mode == 'details', 'Ctrl+wheel down steps to the next mode')
        page.cycle_mode(-1)
        check(page.mode == 'list', 'Ctrl+wheel up steps back')
        page.set_mode('icons')
        check(page.mode == 'large', 'legacy "icons" maps to large icons')
        check(explorer.settings.value('view_mode') == 'large', 'view mode persisted')
        page.set_mode('details')
        page.navigate(str(work / 'dst'))

        print('Testing tab strip width and cross-window tab drops...')
        explorer.add_tab(str(work / 'src'))
        added = explorer.pages.currentWidget()
        QTest.qWait(100)
        check(explorer.tabs.width() <= explorer.tabs.sizeHint().width() + 2, 'tab strip is never wider than its tabs')
        print('Testing tab drag reordering by slot and detach trigger...')
        tabs = explorer.tabs
        last = tabs.count() - 1
        first_title = tabs.tabText(0)
        first_container = explorer.pages.widget(0)
        tabs.setCurrentIndex(0)
        start = tabs.tabRect(0).center()
        QTest.mousePress(tabs, Qt.LeftButton, Qt.NoModifier, start)
        for x in (start.x() + 15, tabs.width() // 2, tabs.width() + 90):  # past the end, within the sideways slack
            QTest.mouseMove(tabs, QPoint(x, start.y()))
        QTest.mouseRelease(tabs, Qt.LeftButton, Qt.NoModifier, QPoint(tabs.width() + 90, start.y()))
        check(tabs.count() == last + 1, 'reorder drag keeps every tab')
        check(tabs.tabText(last) == first_title and explorer.pages.widget(last) is first_container, 'tab dragged past the end lands in the last slot')
        check(tabs.moving_tab() is None if hasattr(tabs, 'moving_tab') else True, 'no floating tab left behind')
        detached = []
        original_drag = type(explorer).drag_tab
        type(explorer).drag_tab = lambda self, index: detached.append(index)
        start = tabs.tabRect(tabs.currentIndex()).center()
        QTest.mousePress(tabs, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(tabs, start + QPoint(10, 0))
        QTest.mouseMove(tabs, start + QPoint(10, 120))
        QTest.mouseRelease(tabs, Qt.LeftButton, Qt.NoModifier, start + QPoint(10, 120))
        type(explorer).drag_tab = original_drag
        check(detached == [tabs.currentIndex()], 'leaving the strip hands the tab over to a real drag')
        other = main.new_window(str(work / 'dst'))
        QTest.qWait(300)
        check(other in main.WINDOWS and other.isVisible(), 'new_window shows and keeps a reference')
        from PySide6.QtCore import QMimeData as _Mime
        from PySide6.QtGui import QDropEvent
        payload = _Mime()
        payload.setData(main.TAB_MIME, json.dumps({'pid': os.getpid(), 'window': id(explorer), 'location': str(work / 'src')}).encode())
        before = other.tabs.count()
        other.tabs.dropEvent(QDropEvent(QPointF(5, 5), Qt.MoveAction, payload, Qt.LeftButton, Qt.NoModifier))
        check(other.tabs.count() == before + 1 and other.page.location == str(work / 'src'), 'tab dropped on another window is added there')
        check(not explorer.tab_drop_internal, 'foreign drop does not mark an internal move')
        # dropping the current tab onto the first position of its own strip only reorders
        explorer.tabs.setCurrentIndex(explorer.pages.indexOf(added))
        moved = added
        explorer.receive_tab({'pid': os.getpid(), 'window': id(explorer), 'location': 'x'}, 0)
        check(explorer.pages.widget(0) is moved and explorer.tab_drop_internal, 'own-strip drop reorders the tab')
        explorer.tab_drop_internal = False
        other.close()
        QTest.qWait(200)
        check(other not in main.WINDOWS, 'closed window leaves the registry')
        explorer.close_tab(explorer.pages.indexOf(moved))

        print('Testing mouse buttons 4/5 (back/forward)...')

        from PySide6.QtGui import QMouseEvent
        before = page.location
        page.navigate(str(work / 'src'))
        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.BackButton, Qt.BackButton, Qt.NoModifier)
        QApplication.sendEvent(page.tree.viewport(), press)
        check(page.location == before, 'mouse button 4 goes back')
        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.ForwardButton, Qt.ForwardButton, Qt.NoModifier)
        QApplication.sendEvent(page.tree.viewport(), press)
        check(page.location == str(work / 'src'), 'mouse button 5 goes forward')
        page.navigate(str(work / 'dst'))

        print('Testing drop hint label...')
        from PySide6.QtCore import QMimeData, QUrl
        from PySide6.QtGui import QDragLeaveEvent, QDragMoveEvent
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(work / 'src' / 'a.txt'))])
        viewport = page.tree.viewport()
        move = QDragMoveEvent(QPoint(viewport.width() - 5, viewport.height() - 5), Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton, Qt.NoModifier)
        page.eventFilter(viewport, move)
        check(explorer.drop_hint is not None and explorer.drop_hint.text() == 'Przenieś do dst', 'hint says move into folder')
        move = QDragMoveEvent(QPoint(viewport.width() - 5, viewport.height() - 5), Qt.CopyAction | Qt.MoveAction, mime, Qt.LeftButton, Qt.ControlModifier)
        page.eventFilter(viewport, move)
        check(explorer.drop_hint.text() == 'Kopiuj do dst', 'Ctrl switches hint to copy')
        page.eventFilter(viewport, QDragLeaveEvent())
        check(not explorer.drop_hint.isVisible(), 'hint hidden after drag leaves')

        print('Testing undo of copy...')
        explorer.undo()
        check(wait_until(lambda: not explorer.busy), 'undo finished')
        check(not (work / 'dst' / 'a.txt').exists(), 'undo removed copy')

        print('Testing redo of copy (Ctrl+Y)...')
        check(explorer.redo_stack and explorer.redo_stack[-1][0] == 'transfer', 'undone copy waits on the redo stack')
        explorer.redo()
        check(wait_until(lambda: not explorer.busy), 'redo finished')
        check((work / 'dst' / 'a.txt').read_text() == 'alpha', 'redo restored the copy')
        check((work / 'dst' / 'sub' / 'c.txt').exists(), 'redo restored the copied directory')
        check(not explorer.redo_stack and explorer.undo_stack, 'redo moves the entry back onto the undo stack')
        explorer.undo()
        check(wait_until(lambda: not explorer.busy), 'undo after redo finished')
        check(not (work / 'dst' / 'a.txt').exists(), 'undo after redo removed the copy again')
        explorer.copy_path()  # a read-only command must not clear the redo stack
        check(explorer.redo_stack, 'copying a path leaves the redo stack alone')

        print('Testing paste into the same folder...')
        page.navigate(str(work / 'src'))
        wait_until(lambda: page.proxy.rowCount(page.tree.rootIndex()) == 4)
        explorer.reveal_file(str(work / 'src' / 'a.txt'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'a.txt')])
        explorer.copy()
        explorer.paste()
        check(wait_until(lambda: not explorer.busy), 'same-folder paste finished')
        check((work / 'src' / 'a — kopia.txt').exists(), 'same-folder paste made a copy')
        check((work / 'src' / 'a.txt').exists(), 'original survived same-folder paste')

        print('Testing inline rename (Win11 style)...')
        QInputDialog.getText = staticmethod(lambda *a, **k: ('renamed.txt', True))
        explorer.reveal_file(str(work / 'src' / 'a — kopia.txt'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'a — kopia.txt')])
        explorer.rename()
        QTest.qWait(50)
        editor = page.tree.findChild(QLineEdit)
        check(editor is not None and editor.text() == 'a — kopia.txt', 'inline editor opened with the name')
        check(editor is not None and editor.selectedText() == 'a — kopia', 'editor preselects the stem')
        editor.setText('renamed.txt')
        QTest.keyClick(editor, Qt.Key_Return)
        check(wait_until(lambda: (work / 'src' / 'renamed.txt').exists()), 'rename applied')
        check(explorer.undo_stack and explorer.undo_stack[-1][0] == 'rename', 'inline rename recorded for undo')
        explorer.undo()
        check(wait_until(lambda: not explorer.busy), 'undo rename finished')
        check((work / 'src' / 'a — kopia.txt').exists(), 'undo rename restored name')
        explorer.redo()
        check(wait_until(lambda: not explorer.busy), 'redo rename finished')
        check((work / 'src' / 'renamed.txt').exists(), 'redo applied the rename again')
        explorer.undo()
        check(wait_until(lambda: (work / 'src' / 'a — kopia.txt').exists()), 'undo rename again')

        print('Testing new folder (created immediately, then inline edit)...')
        explorer.new_folder()
        check((work / 'src' / 'Nowy folder').is_dir(), 'new folder created without a dialog')
        check(wait_until(lambda: page.tree.findChild(QLineEdit) is not None), 'new folder opens inline editor')
        QTest.keyClick(page.tree.findChild(QLineEdit), Qt.Key_Escape)
        explorer.new_folder()
        check((work / 'src' / 'Nowy folder (2)').is_dir(), 'second new folder numbered like Windows')
        wait_until(lambda: page.tree.findChild(QLineEdit) is not None)
        QTest.keyClick(page.tree.findChild(QLineEdit), Qt.Key_Escape)

        print('Testing drop rules...')
        sources = [work / 'src' / 'renamed.txt'] if (work / 'src' / 'renamed.txt').exists() else [work / 'src' / 'a.txt']
        check(explorer.drop_action(sources, str(work / 'dst'), Qt.NoModifier) == 'move', 'plain drop within one volume moves')
        check(explorer.drop_action(sources, str(work / 'dst'), Qt.ControlModifier) == 'copy', 'Ctrl drop copies')
        check(explorer.drop_action(sources, str(work / 'dst'), Qt.ShiftModifier) == 'move', 'Shift drop moves')
        check(explorer.drop_action(sources, str(work / 'dst'), Qt.AltModifier) == 'link', 'Alt drop links')
        check(explorer.drop_action(sources, str(work / 'dst'), Qt.ControlModifier | Qt.ShiftModifier) == 'menu', 'Ctrl+Shift asks')
        check(main.FileModel.removeRows(explorer.model, 0, 1) is False, 'model never deletes rows for a drag')

        print('Testing trash (no confirmation, like Windows) and restore...')
        QMessageBox.question = staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError('trash must not ask')))
        page.tree.clearSelection()
        explorer.reveal_file(str(work / 'src' / 'a — kopia.txt'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'a — kopia.txt')])
        explorer.trash()
        check(wait_until(lambda: not explorer.busy), 'trash finished')
        check(wait_until(lambda: [Path(p).name for p in page.selected_paths()] == ['a.txt']), 'next entry selected after delete')
        check(not (work / 'src' / 'a — kopia.txt').exists(), 'item moved to trash')
        info = list((main.TRASH_ROOT / 'info').glob('*.trashinfo'))
        check(len(info) == 1 and 'Path=' in info[0].read_text(), 'trashinfo written')
        page.navigate('trash')
        check(page.trash_tree.topLevelItemCount() == 1, 'trash page lists item')
        page.trash_tree.selectAll()
        explorer.restore_from_trash()
        check(wait_until(lambda: not explorer.busy), 'restore finished')
        check((work / 'src' / 'a — kopia.txt').exists(), 'restored from trash')
        check(not info[0].exists(), 'trashinfo removed after restore')

        print('Testing redo of trash...')
        page.navigate(str(work / 'src'))
        wait_until(lambda: page.current_dir() == str(work / 'src'))
        explorer.reveal_file(str(work / 'src' / 'a — kopia.txt'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'a — kopia.txt')])
        explorer.trash()
        check(wait_until(lambda: not (work / 'src' / 'a — kopia.txt').exists()), 'trashed again for the redo check')
        explorer.undo()
        check(wait_until(lambda: (work / 'src' / 'a — kopia.txt').exists()), 'undo brought it back')
        explorer.redo()
        check(wait_until(lambda: not (work / 'src' / 'a — kopia.txt').exists()), 'redo trashed it once more')
        check(len(list((main.TRASH_ROOT / 'info').glob('*.trashinfo'))) == 1, 'redo wrote the trashinfo again')
        explorer.undo()
        check(wait_until(lambda: (work / 'src' / 'a — kopia.txt').exists()), 'restored after the redo check')
        check(not list((main.TRASH_ROOT / 'info').glob('*.trashinfo')), 'trash is empty again')

        print('Testing permanent delete...')
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Yes)
        page.navigate(str(work / 'src'))
        explorer.reveal_file(str(work / 'src' / 'a — kopia.txt'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'a — kopia.txt')])
        explorer.delete_permanently()
        check(wait_until(lambda: not explorer.busy), 'delete finished')
        check(not (work / 'src' / 'a — kopia.txt').exists(), 'permanent delete removed file')

        print('Testing archive round trip...')
        QInputDialog.getItem = staticmethod(lambda *a, **k: ('ZIP (.zip)', True))
        QInputDialog.getText = staticmethod(lambda *a, **k: ('paczka', True))
        explorer.reveal_file(str(work / 'src' / 'sub'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'sub')])
        explorer.archive()
        check(wait_until(lambda: not explorer.busy), 'archive finished')
        check((work / 'src' / 'paczka.zip').exists(), 'zip created')
        explorer.reveal_file(str(work / 'src' / 'paczka.zip'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'paczka.zip')])
        explorer.extract_archive(dest_folder=True)
        check(wait_until(lambda: not explorer.busy), 'extract finished')
        check((work / 'src' / 'paczka' / 'sub' / 'c.txt').exists(), 'zip extracted to subfolder')

        print('Testing breadcrumb subfolder dropdowns...')
        from PySide6.QtWidgets import QToolButton
        page.navigate(str(work))
        wait_until(lambda: page.current_dir() == str(work))
        explorer.sync()
        arrows = [b for b in explorer.breadcrumbs.findChildren(QToolButton) if b.objectName() == 'crumbArrow']
        check(arrows, 'path segments have a chevron button')
        menus = []
        original_menu = main.Explorer.menu
        main.Explorer.menu = lambda self: menus.append(original_menu(self)) or menus[-1]
        explorer.crumb_menu(arrows[-1], str(work))
        main.Explorer.menu = original_menu
        check(menus, 'the chevron opens a menu')
        names = [a.text() for a in menus[-1].actions() if a.text()]
        check('src' in names and 'dst' in names, 'the menu lists the subfolders of that segment')
        for menu in menus:
            menu.close()
        menus.clear()
        main.Explorer.menu = lambda self: menus.append(original_menu(self)) or menus[-1]
        explorer.crumb_menu(arrows[-1], str(work / 'src' / 'sub'))
        main.Explorer.menu = original_menu
        check([a.text() for a in menus[-1].actions()] == ['Brak podfolderów'], 'a leaf folder says it has no subfolders')
        for menu in menus:
            menu.close()
        page.navigate(str(work / 'src'))
        wait_until(lambda: page.current_dir() == str(work / 'src'))

        print('Testing image dimensions in the details panel...')
        from PySide6.QtGui import QImage as _QImage
        from PySide6.QtWidgets import QLabel
        shot = work / 'src' / 'wymiar.png'
        _QImage(120, 45, _QImage.Format_RGB32).save(str(shot))
        page.navigate(str(work / 'src'))
        wait_until(lambda: page.current_dir() == str(work / 'src'))
        explorer.reveal_file(str(shot), page)
        wait_until(lambda: page.selected_paths() == [str(shot)])
        was_visible = explorer.details.isVisible()
        if not was_visible:
            explorer.toggle_details()
        explorer.details_key = None
        explorer.fill_details()
        QTest.qWait(200)
        texts = [w.text() for w in explorer.details.findChildren(QLabel) if w.text()]
        check('Wymiary' in texts and '120 × 45 px' in texts, 'details panel shows image dimensions')
        if not was_visible:
            explorer.toggle_details()
        QTest.qWait(100)

        print('Testing Windows shortcuts (Ctrl+N, F11, Ctrl+Shift+C)...')
        before = len(main.WINDOWS)
        explorer.open_new_window()
        QTest.qWait(300)
        check(len(main.WINDOWS) == before + 1, 'Ctrl+N opens a second window')
        extra = max(main.WINDOWS, key=id)
        check(extra.page.location == explorer.page.location, 'the new window starts in the current folder')
        extra.close()
        QTest.qWait(200)
        explorer.toggle_fullscreen()
        QTest.qWait(200)
        check(explorer.isFullScreen(), 'F11 enters fullscreen')
        explorer.toggle_fullscreen()
        QTest.qWait(200)
        check(not explorer.isFullScreen(), 'F11 leaves fullscreen')
        keys = {shortcut.key().toString() for shortcut in explorer.findChildren(QShortcut)}
        for combo in ('Ctrl+Shift+C', 'Ctrl+E', 'Ctrl+N', 'F11'):
            check(combo in keys, f'shortcut {combo} is registered')

        print('Testing file launching (no silent QDesktopServices failure)...')
        launched = []
        original_spawn = main.Explorer.spawn
        main.Explorer.spawn = staticmethod(lambda command: (launched.append(list(command)), True)[1])
        image = work / 'src' / 'obraz.png'
        QImage(8, 8, QImage.Format_RGB32).save(str(image))
        page.navigate(str(work / 'src'))
        wait_until(lambda: page.current_dir() == str(work / 'src'))
        page.open_path(str(image))
        check(launched, 'opening a file spawns a launcher instead of relying on QDesktopServices')
        default_id = explorer.applications_for_paths([str(image)])[1]
        if default_id and (main.desktop_entries().get(default_id) or {}).get('X-Entry-Path'):
            check(launched[0][0].endswith('gtk-launch') and default_id.removesuffix('.desktop') in launched[0],
                  'the resolved desktop entry is launched directly')
        launched.clear()
        page.open_path(str(work / 'dst'))  # a folder still navigates, never launches
        check(not launched and page.current_dir() == str(work / 'dst'), 'folders navigate instead of launching an app')
        page.navigate(str(work / 'src'))
        wait_until(lambda: page.current_dir() == str(work / 'src'))
        explorer.reveal_file(str(image), page)
        wait_until(lambda: page.selected_paths() == [str(image)])
        index = page.proxy.mapFromSource(explorer.model.index(str(work / 'src' / 'a.txt')))
        page.tree.selectionModel().select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        launched.clear()
        explorer.open_selected()
        check(sum(len(command) for command in launched) >= 2 and len(launched) >= 1,
              'Enter with several files selected opens all of them, not only the first')
        main.Explorer.spawn = original_spawn

        print('Testing share server and share dialog...')
        import share
        import urllib.request
        server = share.ShareServer([work / 'src' / 'b.txt', work / 'src' / 'sub'])
        index = urllib.request.urlopen(server.url.replace(server.address, '127.0.0.1'), timeout=5).read().decode()
        check('b.txt' in index, 'share index lists file')
        body = urllib.request.urlopen(server.url.replace(server.address, '127.0.0.1') + 'b.txt', timeout=5).read()
        check(body == (work / 'src' / 'b.txt').read_bytes(), 'share server serves file content')
        check(server.downloads == 1, 'download counted')
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{server.port}/wrong/b.txt', timeout=5)
            check(False, 'wrong token must be rejected')
        except urllib.error.HTTPError as exc:
            check(exc.code == 404, 'wrong token rejected with 404')
        server.stop()
        archive = share.pack_paths([work / 'src' / 'sub', work / 'src' / 'b.txt'], work / 'cache', 'test')
        import zipfile
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
        check(names == {'sub/', 'sub/c.txt', 'b.txt'}, f'pack_paths zips folders and files: {names}')
        check(share.decode_endpoint('MgZ7UyQaLvk3Nl3cXb7Huew') == ('', True, 'telefon'), 'hidden phone endpoint decoded')
        check(share.decode_endpoint('Bt_BQtWrxvlgv5piZfzafgoGRGFtaWFu') == ('Damian', False, 'komputer'), 'visible endpoint decoded')
        check(share.lan_addresses() and all(a[1].count('.') == 3 for a in share.lan_addresses()), f'lan_addresses: {share.lan_addresses()}')
        keys = [t[0] for t in share.targets()]
        check('lan' in keys and 'copy' in keys and any(k.startswith('quickshare:') for k in keys), f'share targets detected: {keys}')
        explorer.reveal_file(str(work / 'src' / 'b.txt'), page)
        wait_until(lambda: page.selected_paths() == [str(work / 'src' / 'b.txt')])
        QTimer.singleShot(200, lambda: next((d for d in QApplication.topLevelWidgets() if isinstance(d, main.ShareDialog)), None).choose('lan', True))
        QTimer.singleShot(900, lambda: next((d for d in QApplication.topLevelWidgets() if isinstance(d, main.ShareDialog)), None).reject())
        explorer.share()
        check(explorer.share_server is not None and 'b.txt' in explorer.share_server.files, 'share dialog started LAN server')
        explorer.stop_share_server()
        check(explorer.share_server is None, 'share server stopped')

        print('Testing split view, terminal, tabs, details...')
        explorer.toggle_split_view()
        check(explorer.pages.currentWidget().right_page is not None, 'split view created right page')
        explorer.toggle_split_view()
        check(explorer.pages.currentWidget().right_page is None, 'split view closed')
        explorer.toggle_terminal()
        explorer.terminal.input.setText("echo 'hello world'")
        explorer.terminal.run_cmd()
        check(wait_until(lambda: 'hello world' in explorer.terminal.output.toPlainText()), 'terminal echoes output')
        explorer.toggle_terminal()
        explorer.add_tab('home')
        check(explorer.tabs.count() == 2, 'tab added')
        explorer.close_tab(1)
        check(explorer.tabs.count() == 1, 'tab closed')
        explorer.reopen_tab()
        check(explorer.tabs.count() == 2 and explorer.page.location == 'home', 'closed tab reopened')
        explorer.close_tab(1)
        explorer.toggle_details()
        check(explorer.details.isVisible(), 'details pane shown')
        explorer.toggle_details()
        explorer.context_menu(explorer.pos())
        explorer.set_theme('dark')
        explorer.set_theme('light')
        explorer.properties()  # dialog is modal; closed by the timer below
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f'crash: {exc}')
        traceback.print_exc()
    finally:
        for widget in QApplication.topLevelWidgets():
            widget.close()
        print('All tests passed.' if not FAILURES else f'{len(FAILURES)} failure(s).')
        QApplication.instance().exit(1 if FAILURES else 0)


def close_dialogs():
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QDialog) and widget.isVisible() and not isinstance(widget, (main.ProgressDialog, main.ShareDialog)):
            widget.reject()


app = main.create_app()
main.apply_theme('dark')
closer = QTimer()
closer.timeout.connect(close_dialogs)
closer.start(300)
QTimer.singleShot(100, run_tests)
sys.exit(app.exec())
