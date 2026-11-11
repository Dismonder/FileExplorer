"""Real kernel PTY/readline/job-control checks; shell configuration is isolated."""
import json
import os
import shlex
import shutil
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
_config = tempfile.TemporaryDirectory(prefix='fileexplorer-terminal-config-')
os.environ['XDG_CONFIG_HOME'] = _config.name

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from terminal_panel import TerminalEmulator, TerminalWidget, session_processes


class EmulatorTests(unittest.TestCase):
    def test_split_utf8_colors_cursor_queries_and_alternate_restore(self):
        replies = []
        emulator = TerminalEmulator(20, 4, reply=replies.append)
        emulator.feed(b'MAIN')
        cursor = (emulator.screen.cursor.x, emulator.screen.cursor.y)
        emulator.feed(b'\x1b[?104')
        emulator.feed(b'9h\x1b[38;5;196mR\x1b[38;2;1;2;3mT')
        self.assertTrue(emulator.in_alternate_screen)
        self.assertEqual(emulator.screen.buffer[0][0].fg, 'ff0000')
        self.assertEqual(emulator.screen.buffer[0][1].fg, '010203')
        encoded = 'żółć'.encode()
        emulator.feed(encoded[:1])
        emulator.feed(encoded[1:])
        self.assertIn('żółć', emulator.plain_text())
        emulator.feed(b'\x1b[2;4H\x1b[6n')
        self.assertEqual(replies[-1], b'\x1b[2;4R')
        emulator.feed(b'\x1b[?1049l')
        self.assertFalse(emulator.in_alternate_screen)
        self.assertEqual(emulator.plain_text(), 'MAIN')
        self.assertEqual((emulator.screen.cursor.x, emulator.screen.cursor.y), cursor)
        emulator.feed(b'\x1b[?2004h\x1b[?1h')
        self.assertTrue(emulator.bracketed_paste)
        self.assertTrue(emulator.application_cursor)


class TerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='fileexplorer-pty-')
        self.root = Path(self.work.name)
        self.terminal = TerminalWidget(clean=True, directory=self.root)
        self.terminal.resize(900, 420)
        self.terminal.show()
        self.terminal.view.setFocus()
        self.assertTrue(self.wait_until(lambda: self.terminal.ready and not self.terminal.is_busy()))

    def tearDown(self):
        self.terminal.shutdown()
        self.terminal.close()
        self.app.processEvents()
        self.work.cleanup()

    def wait_until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            QTest.qWait(10)
        return predicate()

    def command(self, command):
        self.terminal.input.setText(command)
        self.terminal.run_cmd()

    def python_command(self, code):
        self.command('python -c ' + shlex.quote(code))

    def test_all_descriptors_are_tty_and_child_owns_foreground_group(self):
        result = self.root / 'tty.json'
        self.python_command("import os,json; open('tty.json','w').write(json.dumps({'tty':[os.isatty(i) for i in range(3)],'name':os.ttyname(0),'foreground':os.tcgetpgrp(0)==os.getpgrp()}))")
        self.assertTrue(self.wait_until(result.exists))
        state = json.loads(result.read_text())
        self.assertEqual(state['tty'], [True, True, True])
        self.assertTrue(state['name'].startswith('/dev/pts/'))
        self.assertTrue(state['foreground'])

    def test_readline_arrows_word_editing_and_history(self):
        view = self.terminal.view
        QTest.keyClicks(view, 'echo REED')
        QTest.keyClick(view, Qt.Key_Left)
        QTest.keyClick(view, Qt.Key_Backspace)
        QTest.keyClick(view, Qt.Key_Return)
        self.assertTrue(self.wait_until(lambda: '\nRED\n' in view.toPlainText()))
        self.assertTrue(self.wait_until(lambda: not self.terminal.is_busy()))
        QTest.keyClick(view, Qt.Key_Up)
        QTest.keyClick(view, Qt.Key_Return)
        self.assertTrue(self.wait_until(lambda: view.toPlainText().count('\nRED\n') == 2))
        QTest.keyClicks(view, 'invalid prefix')
        QTest.keyClick(view, Qt.Key_A, Qt.ControlModifier)
        QTest.keyClick(view, Qt.Key_K, Qt.ControlModifier)
        QTest.keyClicks(view, "printf 'READLINE_OK\\n'")
        QTest.keyClick(view, Qt.Key_Return)
        self.assertTrue(self.wait_until(lambda: '\nREADLINE_OK\n' in view.toPlainText()))

    def test_resize_ioctl_and_sigwinch_reach_running_program(self):
        result = self.root / 'resize.json'
        code = "import os,signal,fcntl,termios,struct,json,time; write=lambda *args:open('resize.json','w').write(json.dumps(list(struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'\\0'*8)))[:2])); signal.signal(signal.SIGWINCH,write); write(); time.sleep(60)"
        self.python_command(code)
        self.assertTrue(self.wait_until(result.exists))
        self.terminal.resize(720, 290)
        rows, columns = self.terminal.emulator.screen.lines, self.terminal.emulator.screen.columns
        def received():
            try:
                return json.loads(result.read_text()) == [rows, columns]
            except (ValueError, OSError):
                return False
        self.assertTrue(self.wait_until(received))
        QTest.keyClick(self.terminal.view, Qt.Key_C, Qt.ControlModifier)
        self.assertTrue(self.wait_until(lambda: not self.terminal.is_busy()))

    def test_ctrl_c_interrupts_foreground_job_and_returns_shell(self):
        self.python_command('import time; time.sleep(60)')
        self.assertTrue(self.wait_until(lambda: self.terminal.backend.foreground() != self.terminal.process.pid))
        self.assertTrue(self.terminal.is_busy())
        QTest.keyClick(self.terminal.view, Qt.Key_C, Qt.ControlModifier)
        self.assertTrue(self.wait_until(lambda: not self.terminal.is_busy()))
        self.assertIn('KeyboardInterrupt', self.terminal.output.toPlainText())

    def test_bracketed_multiline_paste_waits_for_enter(self):
        self.assertTrue(self.wait_until(lambda: self.terminal.emulator.bracketed_paste))
        self.terminal.paste_text("printf 'one' > first.txt\nprintf 'two' > second.txt")
        QTest.qWait(100)
        self.assertFalse((self.root / 'first.txt').exists())
        self.assertFalse((self.root / 'second.txt').exists())
        QTest.keyClick(self.terminal.view, Qt.Key_Return)
        self.assertTrue(self.wait_until(lambda: (self.root / 'second.txt').exists()))
        self.assertEqual((self.root / 'first.txt').read_text(), 'one')
        self.assertEqual((self.root / 'second.txt').read_text(), 'two')

    def test_folder_sync_never_writes_cd_to_foreground_program(self):
        target = self.root / 'other'
        target.mkdir()
        response = self.root / 'response.txt'
        self.python_command("value=input('ASK:'); open('response.txt','w').write(value)")
        self.assertTrue(self.wait_until(lambda: self.terminal.backend.foreground() != self.terminal.process.pid))
        self.terminal.set_directory(target)
        QTest.keyClicks(self.terminal.view, 'response')
        QTest.keyClick(self.terminal.view, Qt.Key_Return)
        self.assertTrue(self.wait_until(response.exists))
        self.assertEqual(response.read_text(), 'response')
        self.assertTrue(self.wait_until(lambda: not self.terminal.is_busy()))
        self.command('pwd > cwd.txt')
        self.assertTrue(self.wait_until(lambda: (target / 'cwd.txt').exists()))
        self.assertEqual((target / 'cwd.txt').read_text().strip(), str(target))

    def test_vim_and_top_use_alternate_screen_and_restore_shell(self):
        if not shutil.which('vim') or not shutil.which('top'):
            self.skipTest('Vim/top nie są zainstalowane')
        path = self.root / 'document.txt'
        path.write_text('INTERACTIVE_VIM_CONTENT\n')
        self.command('vim -Nu NONE -n --noplugin -R document.txt')
        self.assertTrue(self.wait_until(lambda: self.terminal.emulator.in_alternate_screen))
        self.assertTrue(self.wait_until(lambda: 'INTERACTIVE_VIM_CONTENT' in self.terminal.output.toPlainText()))
        self.terminal.send_input(b':q!\r')
        self.assertTrue(self.wait_until(lambda: not self.terminal.emulator.in_alternate_screen and not self.terminal.is_busy()))
        self.assertEqual(path.read_text(), 'INTERACTIVE_VIM_CONTENT\n')
        self.command('top -d 0.2')
        self.assertTrue(self.wait_until(lambda: self.terminal.emulator.in_alternate_screen))
        self.assertTrue(self.wait_until(lambda: 'PID' in self.terminal.output.toPlainText()))
        self.terminal.send_input(b'q')
        self.assertTrue(self.wait_until(lambda: not self.terminal.emulator.in_alternate_screen and not self.terminal.is_busy()))

    def test_scrollback_is_bounded_and_shutdown_kills_all_session_groups(self):
        self.command('seq 1 5300')
        self.assertTrue(self.wait_until(lambda: not self.terminal.is_busy(), timeout=10))
        self.assertIn('5300', self.terminal.output.toPlainText())
        self.assertLessEqual(len(self.terminal.emulator.primary.history.top), self.terminal.MAX_BLOCKS)
        self.assertGreater(self.terminal.view.verticalScrollBar().maximum(), 0)
        self.command('sleep 60 & sleep 60')
        session_id = self.terminal.process.pid
        self.assertTrue(self.wait_until(lambda: len(session_processes(session_id)) >= 3))
        self.terminal.shutdown()
        self.assertEqual(session_processes(session_id), set())


if __name__ == '__main__':
    unittest.main()
