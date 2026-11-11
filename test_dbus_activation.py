"""Exercise real D-Bus activation and forwarding on a private bus, never the desktop bus."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def probe(directory):
    from install_integration import dbus_values
    from linux_integration import SERVICE, INTERFACE, OBJECT, CONTROL_OBJECT

    def call(destination, path, method, *arguments):
        result = subprocess.run(['gdbus', 'call', '--session', '--timeout', '8', '--dest', destination,
                                 '--object-path', path, '--method', method, *arguments],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return dbus_values(result.stdout)

    def state():
        return json.loads(call(SERVICE, CONTROL_OBJECT, SERVICE + '.GetState')[0])

    def wait_for(condition):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            value = state()
            if condition(value):
                return value
            time.sleep(.05)
        raise AssertionError('D-Bus state did not reach expected result: ' + repr(value))

    first = Path(directory) / '100% zażółć #?.txt'
    second = Path(directory) / '.hidden.txt'
    first.write_text('first')
    second.write_text('second')
    initial = None
    try:
        print('Activating FileManager1', flush=True)
        # This activates FileManager1 without an explicit launch of the application.
        call('org.freedesktop.DBus', '/org/freedesktop/DBus',
             'org.freedesktop.DBus.StartServiceByName', INTERFACE, '0')
        initial = state()
        print('Activated ' + repr(initial), flush=True)
        assert initial['windows'] == [], initial
        standard_owner = call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                              'org.freedesktop.DBus.GetNameOwner', INTERFACE)
        private_owner = call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                             'org.freedesktop.DBus.GetNameOwner', SERVICE)
        assert standard_owner == private_owner
        call(INTERFACE, OBJECT, INTERFACE + '.ShowItems', repr([first.as_uri(), second.as_uri()]), '')
        print('ShowItems returned', flush=True)
        wait_for(lambda s: any(set(paths) == {str(first), str(second)} for paths in s['selections']))
        before = state()['pid']
        result = subprocess.run([str(ROOT / 'fileexplorer'), '--select', str(first)],
                                capture_output=True, text=True, timeout=10)
        print('CLI returned', flush=True)
        assert result.returncode == 0, result.stderr
        wait_for(lambda s: [str(first)] in s['selections'])
        assert state()['pid'] == before, 'CLI created a competing instance'
        call(INTERFACE, OBJECT, INTERFACE + '.ShowFolders', "['trash:/']", '')
        wait_for(lambda s: any('trash' in locations for locations in s['windows']))
        print('D-Bus activation, ShowItems, hidden/encoded paths, CLI forwarding and trash: OK')
    finally:
        if initial:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if call(SERVICE, CONTROL_OBJECT, SERVICE + '.QuitIfIdle')[0]:
                    break
                time.sleep(.1)


@unittest.skipUnless(shutil.which('dbus-run-session') and shutil.which('gdbus'), 'D-Bus tools unavailable')
class DBusActivationTests(unittest.TestCase):
    def test_private_bus_activation_and_file_manager_methods(self):
        with tempfile.TemporaryDirectory(prefix='fileexplorer-dbus-') as directory:
            root = Path(directory)
            data, config = root / 'data', root / 'config'
            services = data / 'dbus-1/services'
            services.mkdir(parents=True)
            for name in ('org.damian.FileExplorer', 'org.freedesktop.FileManager1'):
                (services / (name + '.service')).write_text(
                    '[D-BUS Service]\nName=' + name + '\nExec=' + shlex.join([str(ROOT / 'fileexplorer'), '--dbus']) + '\n')
            environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', XDG_DATA_HOME=str(data),
                               XDG_CONFIG_HOME=str(config), XDG_STATE_HOME=str(root / 'state'),
                               XDG_CURRENT_DESKTOP='', XDG_DATA_DIRS='/usr/local/share:/usr/share')
            # D-Bus may activate unrelated accessibility helpers. Log files avoid
            # waiting for inherited pipe descriptors after the probe has exited.
            output = root / 'probe.log'
            with output.open('w') as log:
                try:
                    result = subprocess.run(['dbus-run-session', '--', sys.executable, str(__file__), '--probe', directory],
                                            env=environment, stdout=log, stderr=log, timeout=40)
                except subprocess.TimeoutExpired:
                    self.fail('Probe timed out:\n' + output.read_text())
            self.assertEqual(result.returncode, 0, output.read_text())


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        probe(sys.argv[2])
    else:
        unittest.main()
