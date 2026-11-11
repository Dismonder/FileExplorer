"""A native PTY terminal with ANSI rendering, readline, alternate screen and scrollback.

pyte/wcwidth are pinned, source-vendored dependencies (see vendor/SOURCES.json).
The child acquires a controlling terminal in a fresh Python process: no Python
preexec_fn runs in the multithreaded Qt process.
"""
from __future__ import annotations

import errno
import fcntl
import os
import re
import shlex
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSocketNotifier, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QMenu, QVBoxLayout, QWidget

from vendor import pyte
from vendor.wcwidth import wcwidth


CHILD_SETUP = '''import fcntl, os, signal, sys, termios
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
for number in (signal.SIGINT, signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE):
    signal.signal(number, signal.SIG_DFL)
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
'''
PROMPT_MARKER = b'\x1b]133;A\x07'


class TerminalEmulator:
    """Dynamic pyte dispatch, plus xterm's alternate-screen/input modes."""
    def __init__(self, columns=80, rows=24, history=5000, reply=lambda data: None):
        self.primary = pyte.HistoryScreen(columns, rows, history=history)
        self.alternate = pyte.Screen(columns, rows)
        self.screen = self.primary
        self.reply = reply
        self.bracketed_paste = False
        self.application_cursor = False
        self.mouse_modes = set()
        self.stream = pyte.ByteStream(self)
        self.primary.write_process_input = self.alternate.write_process_input = self.write_process_input

    def __getattr__(self, name):
        if name in pyte.Stream.events:
            # Stream binds callbacks once. Resolve the active screen on every event.
            def dispatch(*args, **kwargs):
                # xterm has private extensions (e.g. Vim's CSI ? 4 m keyboard query)
                # that share final bytes with ordinary SGR/cursor commands.
                if kwargs.get('private') and name != 'report_device_attributes':
                    return
                return getattr(self.screen, name)(*args, **kwargs)
            return dispatch
        return getattr(self.screen, name)

    @property
    def in_alternate_screen(self):
        return self.screen is self.alternate

    def feed(self, data):
        self.stream.feed(data)

    def write_process_input(self, data):
        self.reply(data.encode('utf-8'))

    def report_device_status(self, mode, **kwargs):
        if kwargs.get('private'):
            if mode == 6:
                cursor = self.screen.cursor
                self.write_process_input(f'\x1b[?{cursor.y + 1};{cursor.x + 1}R')
        else:
            self.screen.report_device_status(mode)

    def set_mode(self, *modes, **kwargs):
        self._set_modes(True, modes, kwargs)

    def reset_mode(self, *modes, **kwargs):
        self._set_modes(False, modes, kwargs)

    def _set_modes(self, enabled, modes, kwargs):
        remaining = []
        for mode in modes:
            if not kwargs.get('private'):
                remaining.append(mode)
            elif mode in (47, 1047, 1049):
                if enabled and not self.in_alternate_screen:
                    if mode == 1049:
                        self.primary.save_cursor()
                    if mode in (1047, 1049):
                        self.alternate.reset()
                    self.screen = self.alternate
                elif not enabled and self.in_alternate_screen:
                    self.screen = self.primary
                    if mode == 1049:
                        self.primary.restore_cursor()
            elif mode == 1048:
                self.screen.save_cursor() if enabled else self.screen.restore_cursor()
            elif mode == 2004:
                self.bracketed_paste = enabled
            elif mode == 1:
                self.application_cursor = enabled
            elif mode in (1000, 1002, 1003, 1005, 1006):
                self.mouse_modes.add(mode) if enabled else self.mouse_modes.discard(mode)
            else:
                remaining.append(mode)
        if remaining:
            handler = self.screen.set_mode if enabled else self.screen.reset_mode
            handler(*remaining, **kwargs)

    def reset(self):
        self.primary.reset()
        self.alternate.reset()
        self.screen = self.primary
        self.bracketed_paste = self.application_cursor = False
        self.mouse_modes.clear()

    def resize(self, columns, rows):
        for screen in (self.primary, self.alternate):
            screen.resize(columns=columns, lines=rows)

    def lines(self):
        history = [] if self.in_alternate_screen else list(self.primary.history.top)
        return history + [self.screen.buffer[row] for row in range(self.screen.lines)]

    def plain_text(self):
        return '\n'.join(''.join(line[column].data for column in range(self.screen.columns)).rstrip()
                         for line in self.lines()).rstrip('\n')


def session_processes(session_id):
    """Only processes in our own PTY session; unrelated sessions are never signalled."""
    result = set()
    if not session_id:
        return result
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            # comm can contain spaces/parentheses. Fields after the final ')' begin with state.
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[3]) == session_id and fields[0] != 'Z':
                result.add(int(entry.name))
        except (OSError, ValueError, IndexError):
            pass
    return result


class PtyBackend:
    def __init__(self):
        self.process = None
        self.master = None
        self.rcfile = None

    def start(self, directory, columns, rows, clean=False):
        master, slave = os.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
            descriptor, self.rcfile = tempfile.mkstemp(prefix='fileexplorer-bash-', suffix='.rc')
            with os.fdopen(descriptor, 'w') as output:
                if not clean:
                    output.write('if [ -r "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi\n')
                output.write('''__fileexplorer_prompt_marker() { local status=$?; printf '\\033]133;A\\007'; return "$status"; }
if [[ $(declare -p PROMPT_COMMAND 2>/dev/null) == 'declare -a'* ]]; then
    PROMPT_COMMAND+=(__fileexplorer_prompt_marker)
else
    PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND; }__fileexplorer_prompt_marker"
fi
''')
                if clean:
                    output.write("PS1='$ '\nHISTFILE=/dev/null\n")
            environment = dict(os.environ, TERM='xterm-256color', COLORTERM='truecolor', TERM_PROGRAM='FileExplorer')
            environment.pop('COLUMNS', None)
            environment.pop('LINES', None)
            self.process = subprocess.Popen(
                [sys.executable, '-c', CHILD_SETUP, '/bin/bash', '--noprofile', '--rcfile', self.rcfile, '-i'],
                stdin=slave, stdout=slave, stderr=slave, cwd=directory,
                env=environment, start_new_session=True, close_fds=True)
            self.master = master
            os.set_blocking(master, False)
        except Exception:
            if self.master != master:
                os.close(master)
            self.close()
            raise
        finally:
            os.close(slave)

    def resize(self, columns, rows):
        if self.master is not None:
            try:
                fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
            except OSError:
                pass

    def foreground(self):
        if self.master is not None:
            try:
                return os.tcgetpgrp(self.master)
            except OSError:
                pass
        return None

    def running(self):
        return self.process is not None and self.process.poll() is None

    def close(self):
        process = self.process
        if process is not None:
            session_id = process.pid
            # Bash job control gives foreground/background jobs their own groups.
            # Killing only the shell's group would leave those jobs running.
            for number, grace in ((signal.SIGHUP, .15), (signal.SIGTERM, .15), (signal.SIGKILL, .1)):
                pids = session_processes(session_id)
                groups = set()
                for pid in pids:
                    try:
                        if os.getsid(pid) == session_id:
                            groups.add(os.getpgid(pid))
                    except ProcessLookupError:
                        pass
                for group in sorted(groups, key=lambda value: value == session_id):
                    try:
                        os.killpg(group, number)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + grace
                while time.monotonic() < deadline and session_processes(session_id):
                    process.poll()
                    time.sleep(.01)
                if not session_processes(session_id):
                    break
            try:
                process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                # A session leader can exit while a deliberate setsid child survives.
                # Never kill that detached child or an unrelated process group.
                pass
        if self.master is not None:
            os.close(self.master)
            self.master = None
        if self.rcfile:
            Path(self.rcfile).unlink(missing_ok=True)
            self.rcfile = None
        self.process = None


PALETTE = dict(zip(
    ('black', 'red', 'green', 'brown', 'blue', 'magenta', 'cyan', 'white',
     'brightblack', 'brightred', 'brightgreen', 'brightbrown', 'brightblue', 'brightmagenta', 'brightcyan', 'brightwhite'),
    ('#20242b', '#e06c75', '#98c379', '#e5c07b', '#61afef', '#c678dd', '#56b6c2', '#d8dee9',
     '#6b7280', '#ff8691', '#b1e58d', '#ffdc8a', '#8cc8ff', '#df9aee', '#81d7df', '#ffffff')))


class TerminalView(QAbstractScrollArea):
    """The single visible input/output surface; compatibility helpers are test-only API."""
    def __init__(self, terminal):
        super().__init__(terminal)
        self.terminal = terminal
        self.setObjectName('terminalScreen')
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.setFrameShape(QAbstractScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setAttribute(Qt.WA_InputMethodEnabled)
        self.viewport().setMouseTracking(True)
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.selection = None
        self._legacy_text = ''
        self._update_metrics()

    def _update_metrics(self):
        metrics = QFontMetrics(self.font())
        self.cell_width = max(1, metrics.horizontalAdvance('M'))
        self.cell_height = max(1, metrics.height())
        self.ascent = metrics.ascent()

    def grid_size(self):
        return max(10, min(512, (self.viewport().width() - 12) // self.cell_width)), max(2, min(256, (self.viewport().height() - 12) // self.cell_height))

    def refresh(self):
        bar = self.verticalScrollBar()
        at_bottom = bar.value() == bar.maximum()
        maximum = max(0, len(self.terminal.emulator.lines()) - self.terminal.emulator.screen.lines)
        bar.setRange(0, maximum)
        bar.setPageStep(self.terminal.emulator.screen.lines)
        if at_bottom:
            bar.setValue(maximum)
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.terminal.resize_terminal(*self.grid_size())
        self.refresh()

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride and event.key() != Qt.Key_F4:
            event.accept()
            return True
        if event.type() == QEvent.FontChange:
            self._update_metrics()
            self.terminal.resize_terminal(*self.grid_size())
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor('#151922'))
        lines = self.terminal.emulator.lines()
        first = self.verticalScrollBar().value()
        screen = self.terminal.emulator.screen
        selected = sorted(self.selection) if self.selection else None
        for row in range(screen.lines):
            absolute = first + row
            if absolute >= len(lines):
                break
            line = lines[absolute]
            for column in range(screen.columns):
                char = line[column]
                if char.data == '':
                    continue  # second cell of a wide glyph
                rectangle = QRect(6 + column * self.cell_width, 6 + row * self.cell_height,
                                  self.cell_width * max(1, wcwidth(char.data[0]) if char.data else 1), self.cell_height)
                foreground = self._color(char.fg, '#d8dee9')
                background = self._color(char.bg, '#151922')
                if char.reverse:
                    foreground, background = background, foreground
                if selected and selected[0] <= (absolute, column) < selected[1]:
                    foreground, background = QColor('#ffffff'), QColor('#355c86')
                painter.fillRect(rectangle, background)
                if char.data.strip():
                    font = QFont(self.font())
                    font.setBold(char.bold)
                    font.setItalic(char.italics)
                    font.setUnderline(char.underscore)
                    font.setStrikeOut(char.strikethrough)
                    painter.setFont(font)
                    painter.setPen(foreground)
                    painter.drawText(rectangle.x(), rectangle.y() + self.ascent, char.data)
        if self.hasFocus() and first == self.verticalScrollBar().maximum() and not screen.cursor.hidden:
            painter.setPen(QColor('#d8dee9'))
            painter.drawRect(QRect(6 + screen.cursor.x * self.cell_width, 6 + screen.cursor.y * self.cell_height,
                                   self.cell_width - 1, self.cell_height - 1))

    @staticmethod
    def _color(value, default):
        return QColor(PALETTE.get(value, '#' + value if re.fullmatch('[0-9a-fA-F]{6}', value) else default))

    def keyPressEvent(self, event):
        key, modifiers = event.key(), event.modifiers()
        ctrl, alt, shift = bool(modifiers & Qt.ControlModifier), bool(modifiers & Qt.AltModifier), bool(modifiers & Qt.ShiftModifier)
        if ctrl and shift and key == Qt.Key_C:
            self.copy()
            return
        if (ctrl and shift and key == Qt.Key_V) or (shift and key == Qt.Key_Insert):
            self.paste()
            return
        if shift and key in (Qt.Key_PageUp, Qt.Key_PageDown):
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + (-1 if key == Qt.Key_PageUp else 1) * bar.pageStep())
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and self._legacy_text:
            self.terminal.run_cmd()
            return
        if ctrl and key == Qt.Key_L:
            self.clear()
        arrows = {Qt.Key_Up: 'A', Qt.Key_Down: 'B', Qt.Key_Right: 'C', Qt.Key_Left: 'D', Qt.Key_Home: 'H', Qt.Key_End: 'F'}
        tilde = {Qt.Key_Insert: 2, Qt.Key_Delete: 3, Qt.Key_PageUp: 5, Qt.Key_PageDown: 6,
                 Qt.Key_F5: 15, Qt.Key_F6: 17, Qt.Key_F7: 18, Qt.Key_F8: 19, Qt.Key_F9: 20, Qt.Key_F10: 21, Qt.Key_F11: 23, Qt.Key_F12: 24}
        modifier = 1 + int(shift) + 2 * int(alt) + 4 * int(ctrl)
        if key in arrows:
            suffix = arrows[key]
            data = (f'\x1b[1;{modifier}{suffix}' if modifier > 1 else ('\x1bO' if self.terminal.emulator.application_cursor else '\x1b[') + suffix).encode()
        elif key in tilde:
            data = (f'\x1b[{tilde[key]};{modifier}~' if modifier > 1 else f'\x1b[{tilde[key]}~').encode()
        elif Qt.Key_F1 <= key <= Qt.Key_F4:
            suffix = chr(ord('P') + key - Qt.Key_F1)
            data = (f'\x1b[1;{modifier}{suffix}' if modifier > 1 else '\x1bO' + suffix).encode()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            data = b'\r'
        elif key == Qt.Key_Backspace:
            data = b'\x7f'
        elif key in (Qt.Key_Tab, Qt.Key_Backtab):
            data = b'\x1b[Z' if shift or key == Qt.Key_Backtab else b'\t'
        elif key == Qt.Key_Escape:
            data = b'\x1b'
        elif ctrl and Qt.Key_A <= key <= Qt.Key_Z:
            data = bytes([key - Qt.Key_A + 1])
        elif ctrl and key in (Qt.Key_Space, Qt.Key_At, Qt.Key_2):
            data = b'\0'
        elif ctrl and key in (Qt.Key_BracketLeft, Qt.Key_Backslash, Qt.Key_BracketRight, Qt.Key_AsciiCircum, Qt.Key_Underscore):
            data = bytes([{Qt.Key_BracketLeft: 27, Qt.Key_Backslash: 28, Qt.Key_BracketRight: 29, Qt.Key_AsciiCircum: 30, Qt.Key_Underscore: 31}[key]])
        else:
            data = event.text().encode('utf-8')
        if alt and key not in arrows and key not in tilde:
            data = b'\x1b' + data
        if data:
            self.selection = None
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            self.terminal.send_input(data)
        event.accept()

    def inputMethodEvent(self, event):
        if event.commitString():
            self.terminal.send_input(event.commitString().encode('utf-8'))
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.ImCursorRectangle:
            cursor = self.terminal.emulator.screen.cursor
            return QRect(6 + cursor.x * self.cell_width, 6 + cursor.y * self.cell_height, self.cell_width, self.cell_height)
        return super().inputMethodQuery(query)

    def _cell(self, position):
        return (max(0, self.verticalScrollBar().value() + (int(position.y()) - 6) // self.cell_height),
                max(0, min(self.terminal.emulator.screen.columns, (int(position.x()) - 6) // self.cell_width)))

    def _report_mouse(self, event, button=None, release=False, motion=False):
        emulator = self.terminal.emulator
        if not emulator.mouse_modes.intersection({1000, 1002, 1003}) or event.modifiers() & Qt.ShiftModifier:
            return False
        if motion and not (1003 in emulator.mouse_modes or 1002 in emulator.mouse_modes and event.buttons()):
            return True
        row, column = self._cell(event.position())
        row -= self.verticalScrollBar().value()
        code = button if button is not None else {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(event.button(), 3)
        if motion:
            code = {Qt.LeftButton: 0, Qt.MiddleButton: 1, Qt.RightButton: 2}.get(event.buttons(), 3) + 32
        if event.modifiers() & Qt.AltModifier:
            code += 8
        if event.modifiers() & Qt.ControlModifier:
            code += 16
        if 1006 in emulator.mouse_modes:
            data = f'\x1b[<{code};{column + 1};{row + 1}{"m" if release else "M"}'.encode()
        elif column < 222 and row < 222:
            data = b'\x1b[M' + bytes([32 + (3 if release else code), 33 + column, 33 + row])
        else:
            return True
        self.terminal.send_input(data)
        return True

    def mousePressEvent(self, event):
        self.setFocus()
        if self._report_mouse(event):
            return
        if event.button() == Qt.MiddleButton:
            self.terminal.paste_text(QApplication.clipboard().text(QApplication.clipboard().Selection))
        elif event.button() == Qt.LeftButton:
            point = self._cell(event.position())
            self.selection = (point, point)
            self.viewport().update()
        elif event.button() == Qt.RightButton:
            menu = QMenu(self)
            menu.addAction('Kopiuj', self.copy).setEnabled(bool(self.selectedText()))
            menu.addAction('Wklej', self.paste)
            menu.addAction('Zaznacz wszystko', self.selectAll)
            menu.addAction('Wyczyść terminal', self.clear)
            menu.popup(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._report_mouse(event, motion=True):
            return
        if event.buttons() & Qt.LeftButton and self.selection:
            self.selection = (self.selection[0], self._cell(event.position()))
            self.viewport().update()

    def mouseReleaseEvent(self, event):
        if self._report_mouse(event, release=True):
            return
        if self.selectedText() and QApplication.clipboard().supportsSelection():
            QApplication.clipboard().setText(self.selectedText(), QApplication.clipboard().Selection)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            font = QFont(self.font())
            font.setPointSize(max(6, min(24, font.pointSize() + (1 if event.angleDelta().y() > 0 else -1))))
            self.setFont(font)
        elif not self._report_mouse(event, button=64 if event.angleDelta().y() > 0 else 65):
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y() // 40)
        event.accept()

    def selectedText(self):
        if not self.selection:
            return ''
        start, end = sorted(self.selection)
        lines = self.terminal.emulator.lines()
        result = []
        for row in range(start[0], min(end[0] + 1, len(lines))):
            left = start[1] if row == start[0] else 0
            right = end[1] if row == end[0] else self.terminal.emulator.screen.columns
            result.append(''.join(lines[row][column].data for column in range(left, right)).rstrip())
        return '\n'.join(result)

    def selectAll(self):
        self.selection = ((0, 0), (len(self.terminal.emulator.lines()) - 1, self.terminal.emulator.screen.columns))
        self.viewport().update()

    def copy(self):
        if self.selectedText():
            QApplication.clipboard().setText(self.selectedText())

    cut = copy

    def paste(self):
        self.terminal.paste_text(QApplication.clipboard().text())

    def toPlainText(self):
        return self.terminal.emulator.plain_text()

    def clear(self):
        screen = self.terminal.emulator.screen
        screen.erase_in_display(2)
        screen.erase_in_display(3)
        screen.cursor_position()
        self.selection = None
        self.refresh()

    def setPlainText(self, text):
        self.clear()
        self.terminal.emulator.feed(str(text).encode('utf-8'))
        self.refresh()

    def setText(self, text):
        self._legacy_text = str(text)

    def text(self):
        return self._legacy_text


class TerminalWidget(QWidget):
    MAX_BLOCKS = 5000
    command_state_changed = Signal(bool)

    def __init__(self, parent=None, *, clean=False, directory=None):
        super().__init__(parent)
        self.setObjectName('terminal')
        self.backend = PtyBackend()
        self.directory = str(directory) if directory else str(Path.home())
        self.clean = clean or os.environ.get('FILEEXPLORER_TERMINAL_CLEAN') == '1'
        self._at_prompt = False
        self.ready = False
        self._line_has_input = False
        self._pending_directory = None
        self._queued = bytearray()
        self._startup_input = bytearray()
        self._marker_tail = b''
        self._read_notifier = self._write_notifier = None
        self.emulator = TerminalEmulator(history=self.MAX_BLOCKS, reply=self._queue_write)
        self.view = TerminalView(self)
        self.input = self.output = self.view  # legacy automation API, one visible terminal surface
        self.history = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.setFocusProxy(self.view)
        self.setAcceptDrops(True)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(100)
        self.poll_timer.timeout.connect(self._check_process)
        self.destroyed.connect(lambda *_: self.backend.close())

    @property
    def process(self):
        return self.backend.process

    def ensure_running(self):
        if self.backend.running():
            return True
        self.shutdown()
        self.ready = self._at_prompt = self._line_has_input = False
        self._marker_tail = b''
        try:
            self.backend.start(self.directory, *self.view.grid_size(), clean=self.clean)
        except OSError as exc:
            self.append(f'Nie można uruchomić terminala: {exc}\r\n')
            return False
        self._read_notifier = QSocketNotifier(self.backend.master, QSocketNotifier.Read, self)
        self._read_notifier.activated.connect(self.read_out)
        self._write_notifier = QSocketNotifier(self.backend.master, QSocketNotifier.Write, self)
        self._write_notifier.setEnabled(False)
        self._write_notifier.activated.connect(self._flush_write)
        self.poll_timer.start()
        return True

    def showEvent(self, event):
        super().showEvent(event)
        self.ensure_running()

    def resize_terminal(self, columns, rows):
        self.emulator.resize(columns, rows)
        self.backend.resize(columns, rows)

    def append(self, text):
        self.emulator.feed(str(text).encode('utf-8'))
        self.view.refresh()

    def _queue_write(self, data):
        if self.backend.master is None:
            return
        self._queued.extend(data)
        self._flush_write()

    def _flush_write(self, *args):
        while self._queued and self.backend.master is not None:
            try:
                written = os.write(self.backend.master, self._queued)
                del self._queued[:written]
            except BlockingIOError:
                break
            except OSError:
                self._queued.clear()
                break
        if self._write_notifier is not None:
            self._write_notifier.setEnabled(bool(self._queued))

    def send_input(self, data, *, pasted=False):
        if not self.ensure_running():
            return
        if not self.ready:
            self._startup_input.extend(data)
            return
        if not pasted and (b'\r' in data or b'\n' in data):
            self._at_prompt = False
            self._line_has_input = False
            self.command_state_changed.emit(True)
        elif data == b'\x15' and self._at_prompt:
            self._line_has_input = False
        elif any(byte >= 32 for byte in data):
            self._line_has_input = True
        self._queue_write(data)

    def read_out(self, *args):
        if self.backend.master is None:
            return
        # Bound each delivery so a verbose process cannot starve the Qt event loop.
        chunks = []
        for _ in range(16):
            try:
                data = os.read(self.backend.master, 16384)
                if not data:
                    break
                chunks.append(data)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF) and self._read_notifier is not None:
                    self._read_notifier.setEnabled(False)
                break
        if not chunks:
            return
        data = b''.join(chunks)
        self.emulator.feed(data)
        combined = self._marker_tail + data
        found = PROMPT_MARKER in combined
        # Preserve only an incomplete prefix, never a marker already acted upon.
        self._marker_tail = next((combined[-size:] for size in range(len(PROMPT_MARKER) - 1, 0, -1)
                                  if combined.endswith(PROMPT_MARKER[:size])), b'')
        if found:
            self.ready = self._at_prompt = True
            self._line_has_input = False
            self.command_state_changed.emit(False)
            if self._startup_input:
                pending = bytes(self._startup_input)
                self._startup_input.clear()
                self.send_input(pending)
            else:
                self._apply_directory()
        self.view.refresh()

    def _check_process(self):
        if self.process is not None and self.process.poll() is not None:
            code = self.process.returncode
            self.read_out()
            self.shutdown()
            self.append(f'\r\n--- Powłoka zakończona (kod {code}); Enter uruchamia ją ponownie ---\r\n')

    def paste_text(self, text):
        text = str(text).replace('\r\n', '\n').replace('\r', '\n')
        if not text:
            return
        if self.emulator.bracketed_paste:
            # Do not allow clipboard data to terminate the bracketed-paste frame.
            text = text.replace('\x1b', '').replace('\x00', '')
            self.send_input(b'\x1b[200~' + text.encode('utf-8') + b'\x1b[201~', pasted=True)
        else:
            self.send_input(text.replace('\n', '\r').encode('utf-8'))

    def run_cmd(self):
        command = self.view._legacy_text
        self.view._legacy_text = ''
        if command.strip():
            self.history.append(command)
        self.send_input(command.encode('utf-8') + b'\r')

    def set_directory(self, path):
        if not path or str(path) == self.directory or not Path(path).is_dir():
            return
        self.directory = str(path)
        if self.backend.running():
            self._pending_directory = self.directory
            self._apply_directory()

    def _apply_directory(self):
        if self._pending_directory and self._at_prompt and not self._line_has_input and self.backend.foreground() == self.process.pid:
            path, self._pending_directory = self._pending_directory, None
            self.send_input(('cd -- ' + shlex.quote(path) + '\r').encode('utf-8'))

    def is_busy(self):
        if not self.backend.running():
            return False
        return (not self._at_prompt or self.backend.foreground() not in (None, self.process.pid)
                or bool(session_processes(self.process.pid) - {self.process.pid}))

    has_running_command = is_busy

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls():
            text = ' '.join(shlex.quote(url.toLocalFile() if url.isLocalFile() else url.toString()) for url in mime.urls())
        else:
            text = mime.text()
        self.paste_text(text)
        self.view.setFocus()
        event.acceptProposedAction()

    def shutdown(self):
        self.poll_timer.stop()
        for name in ('_read_notifier', '_write_notifier'):
            notifier = getattr(self, name)
            if notifier is not None:
                notifier.setEnabled(False)
                notifier.deleteLater()
                setattr(self, name, None)
        self.backend.close()
        self._queued.clear()
        self._startup_input.clear()
        self.ready = self._at_prompt = False
