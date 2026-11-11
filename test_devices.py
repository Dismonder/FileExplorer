"""Device discovery and commands use fixtures; no real mount state is changed."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import devices


FIXTURE = {'blockdevices': [
    {'name': 'nvme0n1', 'path': '/dev/nvme0n1', 'type': 'disk', 'tran': 'nvme', 'rm': False,
     'hotplug': False, 'children': [
         {'name': 'nvme0n1p1', 'path': '/dev/nvme0n1p1', 'type': 'part', 'fstype': 'btrfs',
          'size': 100000000, 'mountpoints': ['/', '/home'], 'label': 'System'},
         {'name': 'nvme0n1p2', 'path': '/dev/nvme0n1p2', 'type': 'part', 'fstype': 'vfat',
          'size': 50000000, 'mountpoints': ['/boot/efi']},
         {'name': 'nvme0n1p3', 'path': '/dev/nvme0n1p3', 'type': 'part', 'fstype': 'ntfs',
          'size': 200000000, 'mountpoints': ['/mnt/windows'], 'label': 'Windows'}]},
    {'name': 'sdb', 'path': '/dev/sdb', 'type': 'disk', 'tran': 'usb', 'rm': 1, 'hotplug': 1,
     'children': [
         {'name': 'sdb1', 'path': '/dev/sdb1', 'type': 'part', 'fstype': 'exfat', 'label': 'Pamięć USB',
          'uuid': 'USB-UUID', 'size': '64000000000', 'mountpoints': ['/run/media/user/Pamięć USB']},
         {'name': 'sdb2', 'path': '/dev/sdb2', 'type': 'part', 'fstype': 'ext4',
          'size': 8000000000, 'mountpoints': [None]},
         {'name': 'sdb3', 'path': '/dev/sdb3', 'type': 'part', 'fstype': 'swap',
          'size': 1000000000, 'mountpoints': [None]}]},
    {'name': 'loop0', 'path': '/dev/loop0', 'type': 'loop', 'fstype': 'ext4',
     'mountpoints': ['/mnt/loop'], 'children': [
         {'name': 'loop0p1', 'path': '/dev/loop0p1', 'type': 'part', 'fstype': 'ext4'}]},
    {'name': 'sdc', 'path': '/dev/sdc', 'type': 'disk', 'tran': 'usb', 'rm': True,
     'fstype': None, 'size': 2000000000},
]}


def result(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class DeviceTests(unittest.TestCase):
    def test_discovery_preserves_mounted_and_unmounted_filesystem_volumes(self):
        entries = {entry.path: entry for entry in devices.parse_devices(FIXTURE)}
        self.assertEqual(set(entries), {'/dev/nvme0n1p1', '/dev/nvme0n1p2', '/dev/nvme0n1p3',
                                       '/dev/sdb1', '/dev/sdb2', '/dev/sdc'})
        self.assertEqual(entries['/dev/sdb1'].size, 64000000000)
        self.assertEqual(entries['/dev/sdb1'].uuid, 'USB-UUID')
        self.assertEqual(entries['/dev/sdb1'].title, 'Pamięć USB')
        self.assertTrue(entries['/dev/sdb2'].can_mount)
        self.assertTrue(entries['/dev/sdb1'].can_unmount)
        self.assertTrue(entries['/dev/sdb2'].can_power_off)
        self.assertFalse(entries['/dev/sdc'].can_mount)

    def test_system_mounts_are_protected_and_windows_partition_cannot_power_off_system_disk(self):
        entries = {entry.path: entry for entry in devices.parse_devices(FIXTURE)}
        self.assertFalse(entries['/dev/nvme0n1p1'].can_unmount)
        self.assertFalse(entries['/dev/nvme0n1p2'].can_unmount)
        self.assertTrue(entries['/dev/nvme0n1p3'].can_unmount)
        self.assertFalse(entries['/dev/nvme0n1p3'].can_power_off)
        self.assertTrue(entries['/dev/nvme0n1p3'].disk_protected)

    def test_legacy_mountpoint_and_string_boolean_fields(self):
        payload = {'blockdevices': [{'name': 'sdd', 'path': '/dev/sdd', 'type': 'disk',
                                    'rm': '0', 'hotplug': '1', 'fstype': 'ext4',
                                    'mountpoint': '/media/disk', 'size': '123'}]}
        entry, = devices.parse_devices(payload)
        self.assertEqual(entry.mountpoints, ('/media/disk',))
        self.assertFalse(entry.removable)
        self.assertTrue(entry.hotplug)

    def test_primary_mountpoint_prefers_root_over_bind_mounts(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture['blockdevices'][0]['children'][0]['mountpoints'] = ['/var/log', '/home', '/']
        entry = next(d for d in devices.parse_devices(fixture) if d.path == '/dev/nvme0n1p1')
        self.assertEqual(entry.mountpoint, '/')

    def test_active_swap_is_hidden_but_prevents_powering_off_its_disk(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture['blockdevices'][1]['children'][2]['mountpoints'] = ['[SWAP]']
        entries = {entry.path: entry for entry in devices.parse_devices(fixture)}
        self.assertNotIn('/dev/sdb3', entries)
        self.assertFalse(entries['/dev/sdb1'].can_power_off)
        runner = Mock(return_value=result(json.dumps(fixture)))
        with self.assertRaises(OSError):
            devices.operate('/dev/sdb2', 'power-off', runner)
        self.assertEqual(runner.call_count, 1)

    def test_swap_on_removable_disk_is_never_exposed(self):
        payload = {'blockdevices': [{'name': 'sdd', 'type': 'disk', 'rm': True, 'fstype': 'swap'}]}
        self.assertEqual(devices.parse_devices(payload), ())

    def test_discovery_falls_back_to_legacy_mountpoint_column(self):
        runner = Mock(side_effect=[result(stderr='lsblk: unknown column: MOUNTPOINTS', returncode=1),
                                   result(json.dumps(FIXTURE))])
        self.assertTrue(devices.discover(runner))
        self.assertIn('MOUNTPOINTS', runner.call_args_list[0].args[0][-1])
        self.assertNotIn('MOUNTPOINTS', runner.call_args_list[1].args[0][-1])

    def test_invalid_json_and_timeouts_are_reported(self):
        for response in (result('not json'), result('[]'), result('{}')):
            with self.subTest(response=response.stdout), self.assertRaises(OSError):
                devices.discover(Mock(return_value=response))
        with self.assertRaisesRegex(OSError, 'nie odpowiedział'):
            devices.discover(Mock(side_effect=subprocess.TimeoutExpired('lsblk', 12)))

    def test_mount_command_uses_user_session_and_refreshes_mountpoints(self):
        mounted = copy.deepcopy(FIXTURE)
        mounted['blockdevices'][1]['children'][1]['mountpoints'] = ['/run/media/user/new']
        runner = Mock(side_effect=[result(json.dumps(FIXTURE)), result('mounted'), result(json.dumps(mounted))])
        entries = devices.operate('/dev/sdb2', 'mount', runner)
        self.assertEqual(runner.call_args_list[1].args[0], ['udisksctl', 'mount', '--block-device', '/dev/sdb2'])
        self.assertEqual(next(d for d in entries if d.path == '/dev/sdb2').mountpoints,
                         ('/run/media/user/new',))
        self.assertFalse(any('sudo' in call.args[0] or 'pkexec' in call.args[0] for call in runner.call_args_list))

    def test_unmount_system_mount_is_refused_before_any_mutation(self):
        for path in ('/dev/nvme0n1p1', '/dev/nvme0n1p2'):
            with self.subTest(path=path):
                runner = Mock(return_value=result(json.dumps(FIXTURE)))
                with self.assertRaisesRegex(OSError, 'systemowych'):
                    devices.operate(path, 'unmount', runner)
                self.assertEqual(runner.call_count, 1)

    def test_power_off_unmounts_all_partitions_and_targets_the_parent_disk(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture['blockdevices'][1]['children'][1]['mountpoints'] = ['/media/second']
        runner = Mock(side_effect=[result(json.dumps(fixture)), result(), result(), result(), result('{' + '"blockdevices": []}')])
        self.assertEqual(devices.operate('/dev/sdb2', 'power-off', runner), ())
        commands = [call.args[0] for call in runner.call_args_list[1:-1]]
        self.assertEqual({tuple(command) for command in commands[:2]},
                         {('udisksctl', 'unmount', '--block-device', '/dev/sdb1'),
                          ('udisksctl', 'unmount', '--block-device', '/dev/sdb2')})
        self.assertEqual(commands[-1], ['udisksctl', 'power-off', '--block-device', '/dev/sdb'])

    def test_power_off_stops_when_one_partition_cannot_unmount(self):
        runner = Mock(side_effect=[result(json.dumps(FIXTURE)), result(stderr='device is busy', returncode=1)])
        with self.assertRaisesRegex(OSError, 'device is busy'):
            devices.operate('/dev/sdb2', 'power-off', runner)
        self.assertEqual(runner.call_count, 2)

    def test_fresh_discovery_prevents_powering_off_a_newly_system_mounted_usb_disk(self):
        fixture = copy.deepcopy(FIXTURE)
        fixture['blockdevices'][1]['children'][0]['mountpoints'] = ['/home']
        runner = Mock(return_value=result(json.dumps(fixture)))
        with self.assertRaisesRegex(OSError, 'systemowy'):
            devices.operate('/dev/sdb2', 'power-off', runner)
        self.assertEqual(runner.call_count, 1)

    def test_disconnected_device_and_invalid_paths_do_not_run_udisks(self):
        runner = Mock(return_value=result(json.dumps(FIXTURE)))
        with self.assertRaisesRegex(OSError, 'odłączony'):
            devices.operate('/dev/disconnected', 'mount', runner)
        self.assertEqual(runner.call_count, 1)
        for path in ('/etc/passwd', '/dev/../etc/passwd', '/dev/a\x00b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                devices.operate(path, 'mount', runner)
        self.assertEqual(runner.call_count, 1)


class DeviceInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication, QPushButton
        import main
        cls.main = main
        cls.read_mounted_volumes = staticmethod(main.mounted_volumes)
        cls.QTest, cls.QPushButton = QTest, QPushButton
        cls.config = tempfile.TemporaryDirectory(prefix='fileexplorer-device-config-')
        cls.settings = patch('main.QSettings', side_effect=lambda *_args, **_kwargs:
                             QSettings(str(Path(cls.config.name) / 'settings.ini'), QSettings.IniFormat))
        cls.settings.start()
        cls.app = QApplication.instance() or main.create_app()

    @classmethod
    def tearDownClass(cls):
        cls.settings.stop()
        cls.config.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='fileexplorer-device-test-')
        self.root = Path(self.work.name)
        self.entries = devices.parse_devices(FIXTURE)
        self.discovery = patch('main.devices.discover', return_value=self.entries)
        self.discovery.start()
        self.volumes = patch('main.mounted_volumes', return_value=[])
        self.volumes.start()
        self.window = self.main.Explorer(str(self.root))
        self.assertTrue(self.wait_until(lambda: not self.window.device_scan_pending))

    def tearDown(self):
        self.window.busy = False
        self.window.close()
        self.main.QThreadPool.globalInstance().waitForDone(2000)
        self.QTest.qWait(20)
        self.volumes.stop()
        self.discovery.stop()
        self.work.cleanup()

    def wait_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            self.QTest.qWait(10)
        return predicate()

    def test_sidebar_and_computer_show_unmounted_media_and_safe_actions(self):
        buttons = self.window.mount_widget.findChildren(self.QPushButton)
        self.assertTrue(any(button.property('device_path') == '/dev/sdb2'
                            and 'niezamontowany' in button.toolTip().lower() + button.accessibleName().lower()
                            for button in buttons))
        self.window.page.navigate('computer')
        actions = {(button.property('device_path'), button.property('device_operation'))
                   for button in self.window.page.computer.findChildren(self.QPushButton)
                   if button.property('device_operation')}
        self.assertIn(('/dev/sdb2', 'mount'), actions)
        self.assertIn(('/dev/sdb1', 'unmount'), actions)
        self.assertIn(('/dev/sdb1', 'power-off'), actions)
        self.assertNotIn(('/dev/nvme0n1p1', 'unmount'), actions)

    def test_busy_file_operation_blocks_safe_eject_before_worker_dispatch(self):
        self.window.page.navigate('computer')
        self.window.busy = True
        self.window.update_status()
        eject_buttons = [button for button in self.window.page.computer.findChildren(self.QPushButton)
                         if button.property('device_operation') == 'power-off']
        self.assertTrue(eject_buttons)
        self.assertTrue(all(not button.isEnabled() for button in eject_buttons))
        with patch('main.devices.operate') as operation:
            self.window.device_operation('/dev/sdb2', 'power-off')
            operation.assert_not_called()
        self.assertIn('Poczekaj', self.window.status_text.text())

    def test_busy_operation_in_another_window_also_blocks_safe_eject(self):
        other_window = Mock(spec=self.main.Explorer)
        other_window.busy = True
        with patch('main.QApplication.topLevelWidgets', return_value=[self.window, other_window]), \
             patch('main.devices.operate') as operation:
            self.window.device_operation('/dev/sdb2', 'power-off')
            operation.assert_not_called()

    def test_existing_qstorageinfo_windows_mount_is_preserved_without_duplicate(self):
        volume = SimpleNamespace(rootPath=lambda: '/mnt/windows', name=lambda: 'Windows',
                                 bytesAvailable=lambda: 100000000, bytesTotal=lambda: 200000000)
        with patch('main.mounted_volumes', return_value=[volume]):
            self.window.refresh_mounts(scan=False)
            destinations = [destination for _, destination in self.window.nav_buttons]
            self.assertEqual(destinations.count('/mnt/windows'), 1)
            self.assertTrue(self.window.device_entry(mountpoint='/mnt/windows'))
            self.window.page.navigate('computer')
            self.assertTrue(any(button.toolTip() == '/dev/nvme0n1p3'
                                for button in self.window.page.computer.findChildren(self.QPushButton)))

    def test_qstorageinfo_loop_mounts_are_hidden_while_windows_mounts_remain(self):
        def volume(path, block_device):
            return SimpleNamespace(rootPath=lambda: path, device=lambda: block_device,
                                   isValid=lambda: True, isReady=lambda: True, bytesTotal=lambda: 200000000)
        windows, loop = volume('/mnt/windows', b'/dev/sda1'), volume('/mnt/loop', b'/dev/loop0')
        with patch('main.QStorageInfo.mountedVolumes', return_value=[windows, loop]):
            self.assertEqual(self.read_mounted_volumes(), [windows])

    def test_discovery_runs_in_background_without_blocking_gui(self):
        entered, release = threading.Event(), threading.Event()
        caller = threading.get_ident()
        worker_threads = []
        def discovery():
            worker_threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return self.entries
        with patch('main.devices.discover', side_effect=discovery):
            try:
                before = time.monotonic()
                self.window.scan_devices(force=True)
                self.assertLess(time.monotonic() - before, 0.2)
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.window.device_scan_pending)
                self.window.sync()
                self.assertTrue(worker_threads and worker_threads[0] != caller)
            finally:
                release.set()
            self.assertTrue(self.wait_until(lambda: not self.window.device_scan_pending))

    def test_mount_operation_runs_in_worker_and_opens_the_discovered_mountpoint(self):
        mounted_path = self.root / 'mounted'
        mounted_path.mkdir()
        mounted = tuple(replace(entry, mountpoints=(str(mounted_path),))
                        if entry.path == '/dev/sdb2' else entry for entry in self.entries)
        caller = threading.get_ident()
        worker_threads = []
        def mount(path, action):
            worker_threads.append(threading.get_ident())
            self.assertEqual((path, action), ('/dev/sdb2', 'mount'))
            return mounted
        with patch('main.devices.operate', side_effect=mount):
            self.window.device_operation('/dev/sdb2', 'mount')
            self.assertTrue(self.wait_until(lambda: not self.window.busy))
        self.assertEqual(self.window.page.location, str(mounted_path))
        self.assertTrue(worker_threads and worker_threads[0] != caller)


if __name__ == '__main__':
    unittest.main()
