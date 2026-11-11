"""Focused checks for the native Plasma settings hub; no system values are changed."""
from __future__ import annotations

import copy
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QApplication, QFrame
from PySide6.QtSvg import QSvgRenderer

from icons import SETTINGS_ART, svg
from settings_backend import SettingsBackend
from settings_catalog import CATEGORIES, MODULE_INFO
from settings_panel import SettingsDialog


class BackendTests(unittest.TestCase):
    def test_exact_module_launch_including_non_underscore_name(self):
        backend = SettingsBackend()
        backend._module_cache = {'kcmspellchecking'}
        with patch.object(backend, 'has', return_value=True), patch('subprocess.Popen') as process:
            self.assertTrue(backend.launch('kcmspellchecking'))
            process.assert_called_once_with(['kcmshell6', 'kcmspellchecking'], start_new_session=True)
        self.assertIsNone(backend.module_for('kcm_missing'))

    def test_pactl_fallback_reads_both_volume_and_mute(self):
        backend = SettingsBackend()
        outputs = {
            'get-sink-volume': (True, 'Volume: front-left: 45875 / 70% / -9.29 dB'),
            'get-sink-mute': (True, 'Mute: yes'),
        }
        with patch.object(backend, 'has', side_effect=lambda cmd: cmd == 'pactl'), \
             patch.object(backend, '_run', side_effect=lambda args, _: outputs[args[1]]):
            self.assertEqual(backend._audio(), {'volume': 70, 'muted': True})

    def test_ntp_switch_tracks_enabled_state_not_sync_completion(self):
        backend = SettingsBackend()
        with patch.object(backend, 'has', side_effect=lambda cmd: cmd == 'timedatectl'), \
             patch.object(backend, '_run', return_value=(True, 'Timezone=Europe/Warsaw\nNTP=yes\nNTPSynchronized=no')):
            state = backend.snapshot()
        self.assertTrue(state['ntp_enabled'])
        self.assertFalse(state['ntp'])

    def test_online_update_codes(self):
        backend = SettingsBackend()
        with patch.object(backend, 'has', return_value=True), \
             patch.object(backend, '_run_result', return_value=(0, 'mesa 1 -> 2\nqt 3 -> 4')):
            self.assertEqual(backend.check_updates_online()['count'], 2)
        with patch.object(backend, 'has', return_value=True), \
             patch.object(backend, '_run_result', return_value=(2, '')):
            self.assertEqual(backend.check_updates_online()['status'], 'none')
        with patch.object(backend, 'has', return_value=True), \
             patch.object(backend, '_run_result', return_value=(1, 'Cannot fetch updates')):
            self.assertFalse(backend.check_updates_online()['checked_online'])


class CatalogueTests(unittest.TestCase):
    def test_every_configured_module_has_a_valid_vector_icon(self):
        listed = [module for _, modules in CATEGORIES.values() for module in modules]
        self.assertEqual(set(listed), set(MODULE_INFO))
        self.assertEqual(len(listed), len(set(listed)))
        self.assertIn('appicon', SETTINGS_ART)
        self.assertTrue(QSvgRenderer(QByteArray(svg('settings-appicon').encode())).isValid())
        for module, info in MODULE_INFO.items():
            name = info['icon']
            self.assertIn(name.removeprefix('settings-'), SETTINGS_ART, module)
            self.assertTrue(QSvgRenderer(QByteArray(svg(name).encode())).isValid(), module)


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.online_checks = 0
        self._snapshot = {
            'hostname': 'test-host', 'distro': 'CachyOS', 'desktop': 'KDE Plasma', 'kernel': 'test',
            'memory': {'percent': 25}, 'home_disk': {'percent': 50},
            'network': {'wifi': True, 'networking': True, 'connection': 'Test Wi-Fi', 'interfaces': []},
            'bluetooth': {'powered': False, 'devices': 0},
            'audio': {'volume': 40, 'muted': False},
            'power': {'profile': 'balanced', 'profiles': ['balanced', 'power-saver']},
            'updates': {'status': 'none', 'count': 0, 'packages': [], 'checked_online': False},
            'timezone': 'Europe/Warsaw', 'ntp': False, 'ntp_enabled': True,
            'package_db_revision': 1,
        }

    def modules(self):
        return {'kcm_kscreen', 'kcm_access', 'kcmspellchecking'}

    def snapshot(self):
        return copy.deepcopy(self._snapshot)

    def has(self, command):
        return command == 'checkupdates'

    def launch(self, module):
        self.calls.append(('launch', module))
        return module in self.modules()

    def launch_app(self, app):
        return False

    def check_updates_online(self):
        self.online_checks += 1
        return {'status': 'available', 'count': 2, 'packages': ['mesa', 'qt'],
                'source': 'checkupdates', 'checked_online': True,
                'package_db_revision': self._snapshot['package_db_revision']}

    def set_wifi(self, value):
        self.calls.append(('wifi', value)); return True, ''

    def set_networking(self, value):
        self.calls.append(('networking', value)); return True, ''


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.backend_patch = patch('settings_panel.SettingsBackend', FakeBackend)
        self.backend_patch.start()
        self.window = SettingsDialog()
        self.window.show()
        self.wait_for_idle()
        self.assertIsNotNone(self.window._online_updates)

    def tearDown(self):
        self.window.close()
        self.backend_patch.stop()

    def wait_for_idle(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if (not self.window._task_running and not self.window._online_task_running
                    and not self.window._pending_operations):
                return
            time.sleep(0.01)
        self.fail('Zadanie ustawień nie zakończyło się w czasie testu')

    @staticmethod
    def card_count(page):
        return sum(card.objectName() == 'settingsCard' for card in page.findChildren(QFrame))

    def test_search_and_navigation_show_only_installed_modules(self):
        all_index = next(i for i, (key, _, _) in enumerate(self.window.nav) if key == 'all')
        self.window.navigate_index(all_index)
        self.assertEqual(self.card_count(self.window.stack.currentWidget()), 3)
        self.window.search.setText('ekran')
        self.assertEqual(self.card_count(self.window._search_page), 1)
        self.window.search.setText('pisowni')
        self.assertEqual(self.card_count(self.window._search_page), 1)
        self.window.search.clear()
        self.assertEqual(self.window.stack.currentIndex(), all_index)
        self.assertTrue(self.window.nav[all_index][2].isChecked())
        self.window.go_back()
        self.assertEqual(self.window.stack.currentIndex(), 0)

    def test_busy_operations_are_queued_and_online_result_is_labeled(self):
        self.window._task_running = True
        self.window.set_backend_value('wifi', False)
        self.window.set_backend_value('networking', False)
        self.assertEqual(len(self.window._pending_operations), 2)
        self.window._task_running = False
        self.window._run_next_operation()
        self.wait_for_idle()
        self.assertIn(('wifi', False), self.window.backend.calls)
        self.assertIn(('networking', False), self.window.backend.calls)
        self.window.check_online_updates()
        self.wait_for_idle()
        self.assertIn('po sprawdzeniu online', self.window.update_text(self.window._state))

    def test_slow_online_check_does_not_block_wifi_control(self):
        release = threading.Event()
        original = self.window.backend.check_updates_online
        def slow_check():
            release.wait(2)
            return original()
        with patch.object(self.window.backend, 'check_updates_online', side_effect=slow_check):
            self.window.check_online_updates()
            self.window.set_backend_value('wifi', True)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and ('wifi', True) not in self.window.backend.calls:
                self.app.processEvents(); time.sleep(.01)
            self.assertIn(('wifi', True), self.window.backend.calls)
            self.assertTrue(self.window._online_task_running)
            release.set()
            self.wait_for_idle()

    def test_package_change_expires_online_result(self):
        before = self.window.backend.online_checks
        self.window.backend._snapshot['package_db_revision'] = 2
        self.window.refresh_state()
        self.wait_for_idle()
        self.assertGreater(self.window.backend.online_checks, before)
        self.assertEqual(self.window._online_updates[1]['package_db_revision'], 2)


if __name__ == '__main__':
    unittest.main()
