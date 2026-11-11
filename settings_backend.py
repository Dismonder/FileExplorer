"""Linux/KDE backend used by the Fluent settings window.

The settings window deliberately keeps all privileged operations behind the native
desktop tools.  Read-only information is collected without a shell; changes are sent
to NetworkManager, BlueZ, PipeWire/WirePlumber, systemd and Power Profiles Daemon.
This makes the UI useful on CachyOS installations with different optional packages.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess
from pathlib import Path


class SettingsBackend:
    """Small, dependency-free adapter for the services present on KDE Plasma."""

    MODULES = {
        # General Plasma modules
        'display': 'kcm_kscreen',
        'sound': 'kcm_pulseaudio',
        'notifications': 'kcm_notifications',
        'power': 'kcm_powerdevilprofilesconfig',
        'storage': 'kcm_memory',
        'about': 'kcm_about-distro',
        'bluetooth': 'kcm_bluetooth',
        'printers': 'kcm_printer_manager',
        'mouse': 'kcm_mouse',
        'touchpad': 'kcm_touchpad',
        'network': 'kcm_networkmanagement',
        'wifi': 'kcm_networkmanagement',
        'vpn': 'kcm_networkmanagement',
        'hotspot': 'kcm_mobile_hotspot',
        'wallpaper': 'kcm_wallpaper',
        'colors': 'kcm_colors',
        'desktop_theme': 'kcm_desktoptheme',
        'global_theme': 'kcm_lookandfeel',
        'icons': 'kcm_icons',
        'cursor': 'kcm_cursortheme',
        'fonts': 'kcm_fonts',
        'window_decorations': 'kcm_kwindecoration',
        'animations': 'kcm_animations',
        'taskbar': 'kcm_workspace',
        'defaults': 'kcm_componentchooser',
        'file_associations': 'kcm_filetypes',
        'startup': 'kcm_autostart',
        'apps': 'kcm_webshortcuts',
        'accounts': 'kcm_users',
        'online_accounts': 'kcm_kaccounts',
        'keyboard': 'kcm_keyboard',
        'language': 'kcm_regionandlang',
        'date_time': 'kcm_clock',
        'accessibility': 'kcm_access',
        'privacy': 'kcm_recentFiles',
        'search': 'kcm_baloofile',
        'firewall': 'kcm_firewall',
        'screen_lock': 'kcm_screenlocker',
        'wallet': 'kcm_kwallet5',
        'gaming': 'kcm_gamecontroller',
        'updates': 'kcm_updates',
        'plasma_style': 'kcm_style',
        'shortcuts': 'kcm_keys',
        'paths': 'kcm_desktoppaths',
    }

    CATEGORIES = {
        'system': ('System', ('display', 'sound', 'notifications', 'power', 'storage', 'about')),
        'bluetooth': ('Bluetooth i urządzenia', ('bluetooth', 'printers', 'mouse', 'touchpad')),
        'network': ('Sieć i internet', ('network', 'wifi', 'vpn', 'hotspot')),
        'personalization': ('Personalizacja', ('wallpaper', 'colors', 'desktop_theme', 'global_theme', 'icons', 'cursor', 'fonts', 'window_decorations', 'animations', 'taskbar')),
        'apps': ('Aplikacje', ('defaults', 'file_associations', 'startup', 'apps')),
        'accounts': ('Konta', ('accounts', 'online_accounts', 'wallet')),
        'time': ('Czas i język', ('date_time', 'language', 'keyboard')),
        'gaming': ('Granie', ('gaming',)),
        'accessibility': ('Ułatwienia dostępu', ('accessibility',)),
        'privacy': ('Prywatność i zabezpieczenia', ('privacy', 'search', 'firewall', 'screen_lock')),
        'update': ('Aktualizacje', ('updates',)),
    }

    def __init__(self):
        self._module_cache: set[str] | None = None

    @staticmethod
    def _run_result(args: list[str], timeout: float = 4.0) -> tuple[int | None, str]:
        """Run a local command in a machine-readable locale.

        Plasma can be used in any language, while NetworkManager and pacman print
        localized status text.  The command's output is only parsed internally, so
        forcing the C locale makes its contract stable without changing the user's
        desktop language.
        """
        try:
            environment = os.environ.copy()
            environment.update({'LC_ALL': 'C', 'LANGUAGE': 'C'})
            result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout,
                                    check=False, env=environment)
        except (OSError, subprocess.SubprocessError):
            return None, ''
        return result.returncode, result.stdout.strip()

    @classmethod
    def _run(cls, args: list[str], timeout: float = 4.0) -> tuple[bool, str]:
        code, output = cls._run_result(args, timeout)
        return code == 0, output

    @staticmethod
    def has(command: str) -> bool:
        return shutil.which(command) is not None

    @staticmethod
    def package_db_revision() -> int | None:
        """Change marker for installed packages, used to expire online results."""
        try:
            return Path('/var/lib/pacman/local').stat().st_mtime_ns
        except OSError:
            return None

    def modules(self) -> set[str]:
        """Return installed KCM names, cached for the lifetime of the dialog."""
        if self._module_cache is not None:
            return self._module_cache
        if not self.has('kcmshell6'):
            self._module_cache = set()
            return self._module_cache
        ok, output = self._run(['kcmshell6', '--list'], 8)
        found = set(re.findall(r'^\s*(kcm[\w.-]+)\s+-', output, re.MULTILINE)) if ok else set()
        self._module_cache = found
        return found

    def module_for(self, key: str) -> str | None:
        module = self.MODULES.get(key, key if key.startswith('kcm') else None)
        return module if module and module in self.modules() else None

    def available(self, key: str) -> bool:
        return self.module_for(key) is not None or any(self.has(c) for c in self.command_for(key))

    def command_for(self, key: str) -> tuple[str, ...]:
        return {
            'network': ('nmcli',), 'wifi': ('nmcli',), 'vpn': ('nmcli',),
            'bluetooth': ('bluetoothctl',), 'sound': ('wpctl', 'pactl'),
            'power': ('powerprofilesctl',), 'updates': ('pacman', 'plasma-discover'),
            'storage': ('filelight',), 'gaming': ('gamemoded', 'gamemoderun'),
            'firewall': ('ufw', 'firewall-cmd'),
        }.get(key, ())

    def launch(self, key: str, parent=None) -> bool:
        """Open an installed KCM in its native KDE window."""
        module = self.module_for(key)
        commands: list[list[str]] = []
        if module and self.has('kcmshell6'):
            commands.append(['kcmshell6', module])
        if module and self.has('systemsettings'):
            commands.append(['systemsettings', module])
        if key == 'systemsettings' and self.has('systemsettings'):
            commands.append(['systemsettings'])
        for command in commands:
            try:
                subprocess.Popen(command, start_new_session=True)
                return True
            except OSError:
                continue
        return self.launch_app(key)

    def launch_update_terminal(self) -> bool:
        """Open a visible terminal for a user-approved system update."""
        terminal = next((c for c in ('konsole', 'yakuake', 'alacritty', 'kitty', 'xterm') if self.has(c)), None)
        if not terminal:
            return False
        if terminal == 'konsole':
            command = [terminal, '--hold', '-e', 'sudo', 'pacman', '-Syu']
        elif terminal in ('alacritty', 'kitty'):
            command = [terminal, '-e', 'sudo', 'pacman', '-Syu']
        elif terminal == 'xterm':
            command = [terminal, '-hold', '-e', 'sudo', 'pacman', '-Syu']
        else:
            command = [terminal, '-e', 'sudo', 'pacman', '-Syu']
        try:
            subprocess.Popen(command, start_new_session=True)
            return True
        except OSError:
            return False

    @staticmethod
    def _disk(path: str) -> dict[str, int | float]:
        try:
            stat = os.statvfs(path)
            total = stat.f_frsize * stat.f_blocks
            free = stat.f_frsize * stat.f_bavail
            return {'total': total, 'free': free, 'used': max(0, total - free),
                    'percent': round((total - free) * 100 / total, 1) if total else 0}
        except OSError:
            return {'total': 0, 'free': 0, 'used': 0, 'percent': 0}

    @staticmethod
    def _mem() -> dict[str, int | float]:
        values: dict[str, int] = {}
        try:
            for line in Path('/proc/meminfo').read_text().splitlines():
                key, value = line.split(':', 1)
                values[key] = int(value.strip().split()[0]) * 1024
        except (OSError, ValueError):
            pass
        total = values.get('MemTotal', 0)
        available = values.get('MemAvailable', values.get('MemFree', 0))
        return {'total': total, 'available': available, 'used': max(0, total - available),
                'percent': round((total - available) * 100 / total, 1) if total else 0}

    def _network(self) -> dict[str, object]:
        result: dict[str, object] = {'wifi': None, 'networking': None, 'connection': '', 'state': ''}
        if not self.has('nmcli'):
            return result
        ok, value = self._run(['nmcli', '-g', 'WIFI', 'general'], 3)
        if ok:
            result['wifi'] = value.strip().lower() == 'enabled'
        ok, value = self._run(['nmcli', '-g', 'NETWORKING', 'general'], 3)
        if ok:
            result['networking'] = value.strip().lower() == 'enabled'
        ok, value = self._run(['nmcli', '-t', '-f', 'DEVICE,STATE,CONNECTION', 'device', 'status'], 3)
        if ok:
            interfaces = []
            for line in value.splitlines():
                fields = re.split(r'(?<!\\):', line, maxsplit=2)
                if len(fields) < 2:
                    continue
                device, state = fields[:2]
                connection = fields[2].replace(r'\:', ':') if len(fields) > 2 else ''
                interfaces.append({'device': device, 'state': state, 'connection': connection})
            result['interfaces'] = interfaces
            active = next((entry for entry in interfaces if entry['state'] == 'connected'), None)
            if active:
                result['state'] = active['state']
                result['connection'] = active['connection']
        return result

    def _bluetooth(self) -> dict[str, object]:
        if not self.has('bluetoothctl'):
            return {'powered': None, 'devices': 0}
        ok, value = self._run(['bluetoothctl', 'show'], 3)
        powered = None
        if ok:
            match = re.search(r'Powered:\s*(yes|no)', value, re.I)
            powered = match.group(1).lower() == 'yes' if match else None
        ok, devices = self._run(['bluetoothctl', 'devices'], 3)
        return {'powered': powered, 'devices': len(devices.splitlines()) if ok and devices else 0}

    def _audio(self) -> dict[str, object]:
        if self.has('wpctl'):
            ok, value = self._run(['wpctl', 'get-volume', '@DEFAULT_AUDIO_SINK@'], 3)
            if ok:
                match = re.search(r'Volume:\s*([0-9]+(?:\.[0-9]+)?)', value)
                if match:
                    return {'volume': min(100, round(float(match.group(1)) * 100)),
                            'muted': 'MUTED' in value.upper()}
        if self.has('pactl'):
            ok, value = self._run(['pactl', 'get-sink-volume', '@DEFAULT_SINK@'], 3)
            muted_ok, muted_value = self._run(['pactl', 'get-sink-mute', '@DEFAULT_SINK@'], 3)
            match = re.search(r'/\s*(\d+)%', value) if ok else None
            return {'volume': int(match.group(1)) if match else None,
                    'muted': muted_value.strip().lower().endswith('yes') if muted_ok else None}
        return {'volume': None, 'muted': None}

    def _power(self) -> dict[str, object]:
        if not self.has('powerprofilesctl'):
            return {'profile': None, 'profiles': []}
        ok, current = self._run(['powerprofilesctl', 'get'], 3)
        ok2, available = self._run(['powerprofilesctl', 'list'], 3)
        profiles = re.findall(r'^\s*(?:\*\s*)?([\w-]+):\s*$', available, re.MULTILINE) if ok2 else []
        return {'profile': current if ok else None, 'profiles': list(dict.fromkeys(profiles))}

    def _updates(self) -> dict[str, object]:
        if not self.has('pacman'):
            return {'count': None, 'packages': [], 'status': 'unavailable', 'source': 'pacman-local-database'}
        code, output = self._run_result(['pacman', '-Qu'], 20)
        # pacman returns 1 with empty output when no packages are pending; that is
        # a valid result, whereas non-empty output on failure is an unknown state.
        if code == 0:
            packages = output.splitlines() if output else []
            return {'count': len(packages), 'packages': packages[:8],
                    'status': 'available' if packages else 'none', 'source': 'pacman-local-database',
                    'checked_online': False}
        if code == 1 and not output:
            return {'count': 0, 'packages': [], 'status': 'none',
                    'source': 'pacman-local-database', 'checked_online': False}
        return {'count': None, 'packages': [], 'status': 'unknown',
                'source': 'pacman-local-database', 'checked_online': False, 'error': output}

    def check_updates_online(self) -> dict[str, object]:
        """Check current repositories without changing pacman's system sync database."""
        if not self.has('checkupdates'):
            return {'count': None, 'packages': [], 'status': 'unavailable',
                    'source': 'checkupdates', 'checked_online': False}
        code, output = self._run_result(['checkupdates', '--nocolor'], 120)
        if code in (0, 2):
            packages = output.splitlines() if code == 0 and output else []
            return {'count': len(packages), 'packages': packages[:8],
                    'status': 'available' if packages else 'none',
                    'source': 'checkupdates', 'checked_online': True,
                    'package_db_revision': self.package_db_revision()}
        return {'count': None, 'packages': [], 'status': 'unknown',
                'source': 'checkupdates', 'checked_online': False, 'error': output}

    def snapshot(self) -> dict[str, object]:
        release = 'Linux'
        try:
            for line in Path('/etc/os-release').read_text().splitlines():
                if line.startswith('PRETTY_NAME='):
                    release = line.split('=', 1)[1].strip().strip('"')
                    break
        except OSError:
            pass
        timezone = ''
        ntp = None
        ntp_enabled = None
        if self.has('timedatectl'):
            ok, value = self._run(['timedatectl', 'show', '-p', 'Timezone', '-p', 'NTP', '-p', 'NTPSynchronized'], 3)
            if ok:
                timezone = next((x.split('=', 1)[1] for x in value.splitlines() if x.startswith('Timezone=')), '')
                ntp_value = next((x.split('=', 1)[1] for x in value.splitlines() if x.startswith('NTPSynchronized=')), '')
                enabled_value = next((x.split('=', 1)[1] for x in value.splitlines() if x.startswith('NTP=')), '')
                ntp = ntp_value.lower() == 'yes' if ntp_value else None
                ntp_enabled = enabled_value.lower() == 'yes' if enabled_value else None
        return {
            'hostname': socket.gethostname(), 'distro': release, 'kernel': platform.release(),
            'desktop': os.environ.get('XDG_CURRENT_DESKTOP') or os.environ.get('XDG_SESSION_DESKTOP') or 'KDE Plasma',
            'cpu': platform.processor() or 'Nieznany procesor', 'memory': self._mem(),
            'root_disk': self._disk('/'), 'home_disk': self._disk(str(Path.home())),
            'network': self._network(), 'bluetooth': self._bluetooth(), 'audio': self._audio(),
            'power': self._power(), 'updates': self._updates(), 'timezone': timezone,
            'ntp': ntp, 'ntp_enabled': ntp_enabled,
            'package_db_revision': self.package_db_revision(),
            'gamemode': self.has('gamemoded'), 'flatpak': self.has('flatpak'),
        }

    def set_wifi(self, enabled: bool) -> tuple[bool, str]:
        return self._run(['nmcli', 'radio', 'wifi', 'on' if enabled else 'off'], 8)

    def set_networking(self, enabled: bool) -> tuple[bool, str]:
        return self._run(['nmcli', 'networking', 'on' if enabled else 'off'], 8)

    def set_bluetooth(self, enabled: bool) -> tuple[bool, str]:
        return self._run(['bluetoothctl', 'power', 'on' if enabled else 'off'], 8)

    def set_volume(self, percent: int) -> tuple[bool, str]:
        value = max(0, min(100, int(percent)))
        if self.has('wpctl'):
            return self._run(['wpctl', 'set-volume', '@DEFAULT_AUDIO_SINK@', f'{value / 100:.2f}'], 5)
        if self.has('pactl'):
            return self._run(['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f'{value}%'], 5)
        return False, 'Nie znaleziono PipeWire ani PulseAudio.'

    def set_muted(self, muted: bool) -> tuple[bool, str]:
        if self.has('wpctl'):
            return self._run(['wpctl', 'set-mute', '@DEFAULT_AUDIO_SINK@', '1' if muted else '0'], 5)
        if self.has('pactl'):
            return self._run(['pactl', 'set-sink-mute', '@DEFAULT_SINK@', '1' if muted else '0'], 5)
        return False, 'Nie znaleziono PipeWire ani PulseAudio.'

    def set_power_profile(self, profile: str) -> tuple[bool, str]:
        return self._run(['powerprofilesctl', 'set', profile], 5)

    def launch_app(self, key: str) -> bool:
        """Launch an optional native utility without claiming that it is installed."""
        apps = {
            'storage': ('filelight',), 'updates': ('plasma-discover', 'discover'),
            'screenshot': ('spectacle',), 'terminal': ('konsole', 'yakuake', 'alacritty', 'kitty'),
            'gaming': ('steam',), 'firewall': ('firewall-config',),
        }
        for command in apps.get(key, ()):
            if not self.has(command):
                continue
            try:
                subprocess.Popen([command], start_new_session=True)
                return True
            except OSError:
                continue
        return False

    def command_availability(self) -> dict[str, bool]:
        names = ('kcmshell6', 'systemsettings', 'nmcli', 'bluetoothctl', 'wpctl',
                 'pactl', 'powerprofilesctl', 'timedatectl', 'localectl', 'pacman',
                 'plasma-discover', 'filelight', 'pavucontrol', 'gamemoded', 'flatpak')
        return {name: self.has(name) for name in names}

    def set_ntp(self, enabled: bool) -> tuple[bool, str]:
        command = ['timedatectl', 'set-ntp', 'true' if enabled else 'false']
        ok, output = self._run(command, 8)
        if ok or not self.has('pkexec'):
            return ok, output
        return self._run(['pkexec', *command], 30)

    def firewall_status(self) -> dict[str, object]:
        if self.has('ufw'):
            ok, value = self._run(['ufw', 'status'], 5)
            return {'available': True, 'enabled': bool(ok and re.search(r'^Status:\s*active', value, re.I)), 'backend': 'ufw'}
        if self.has('firewall-cmd'):
            ok, value = self._run(['firewall-cmd', '--state'], 5)
            return {'available': True, 'enabled': ok and value.strip() == 'running', 'backend': 'firewalld'}
        return {'available': False, 'enabled': None, 'backend': ''}
