"""Read-only block-device discovery and user-session UDisks operations."""
from dataclasses import dataclass, replace
import json
import os
import subprocess


COLUMNS = 'NAME,PATH,TYPE,TRAN,RM,HOTPLUG,FSTYPE,LABEL,UUID,SIZE,MOUNTPOINTS'
CONTAINER_FILESYSTEMS = {'lvm2_member', 'linux_raid_member', 'zfs_member'}
UNMOUNTABLE_FILESYSTEMS = CONTAINER_FILESYSTEMS | {'swap', 'crypto_luks'}


def protected_mount(path):
    normalized = os.path.normpath(path)
    return normalized in {'/', '/home', '/boot'} or normalized.startswith('/boot/')


@dataclass(frozen=True)
class Device:
    path: str
    name: str
    kind: str
    filesystem: str
    label: str
    uuid: str
    size: int
    mountpoints: tuple
    disk_path: str
    transport: str
    removable: bool
    hotplug: bool
    disk_protected: bool = False

    @property
    def title(self):
        return self.label or self.name

    @property
    def mountpoint(self):
        # lsblk can list bind mounts before the filesystem's main mount.
        return min(self.mountpoints, key=lambda path: (path.count('/'), len(path), path)) if self.mountpoints else None

    @property
    def protected(self):
        return any(protected_mount(path) for path in self.mountpoints)

    @property
    def can_mount(self):
        return (not self.mountpoints and bool(self.filesystem)
                and self.filesystem.lower() not in UNMOUNTABLE_FILESYSTEMS)

    @property
    def can_unmount(self):
        return bool(self.mountpoints) and not self.protected

    @property
    def can_power_off(self):
        return (bool(self.disk_path) and not self.disk_protected
                and (self.removable or self.hotplug or self.transport == 'usb'))


def _flag(value):
    return value is True or str(value).lower() in {'1', 'true', 'yes'}


def _device_path(value):
    return isinstance(value, str) and value.startswith('/dev/') and '\x00' not in value and '..' not in value.split('/')


def parse_devices(payload):
    """Keep filesystem volumes and empty removable drives; never expose swap or loops."""
    if not isinstance(payload, dict) or not isinstance(payload.get('blockdevices'), list):
        raise ValueError('Nieprawidłowa lista nośników zwrócona przez lsblk.')
    found = {}
    protected_disks = set()

    def visit(node, disk='', transport='', removable=False, hotplug=False):
        if not isinstance(node, dict):
            return
        kind = str(node.get('type') or '')
        if kind in {'loop', 'ram', 'zram'}:
            return
        name = str(node.get('name') or '')
        path = node.get('path') or ('/dev/' + name)
        if not _device_path(path):
            return
        transport = str(node.get('tran') or transport)
        removable = removable or _flag(node.get('rm'))
        hotplug = hotplug or _flag(node.get('hotplug'))
        if kind == 'disk':
            disk = path
        mounts = node.get('mountpoints', node.get('mountpoint')) or ()
        if isinstance(mounts, str):
            mounts = [mounts]
        active_swap = node.get('fstype') == 'swap' and '[SWAP]' in mounts
        mounts = tuple(dict.fromkeys(p for p in mounts if isinstance(p, str) and p.startswith('/')))
        if active_swap or any(protected_mount(p) for p in mounts):
            protected_disks.add(disk)
        filesystem = str(node.get('fstype') or '')
        children = node.get('children') or []
        include = (filesystem and filesystem.lower() not in CONTAINER_FILESYSTEMS | {'swap'})
        include = include or (kind == 'disk' and not children and (removable or hotplug or transport == 'usb'))
        if filesystem.lower() == 'swap':
            include = False
        if include and kind in {'disk', 'part', 'crypt', 'lvm', 'rom', 'md', 'raid0', 'raid1', 'raid5', 'raid6', 'raid10'}:
            try:
                size = max(0, int(node.get('size') or 0))
            except (TypeError, ValueError):
                size = 0
            entry = Device(path, name, kind, filesystem, str(node.get('label') or ''),
                           str(node.get('uuid') or ''), size, mounts, disk,
                           transport, removable, hotplug)
            previous = found.get(path)
            if previous:
                entry = replace(entry, mountpoints=tuple(dict.fromkeys(previous.mountpoints + mounts)),
                                disk_protected=previous.disk_protected or previous.disk_path != disk)
            found[path] = entry
        for child in children:
            visit(child, disk, transport, removable, hotplug)

    for node in payload['blockdevices']:
        visit(node)
    return tuple(sorted((replace(entry, disk_protected=entry.disk_protected or entry.disk_path in protected_disks)
                         for entry in found.values()), key=lambda entry: (entry.title.casefold(), entry.path)))


def _run(arguments, runner, timeout):
    try:
        result = runner(arguments, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise OSError(f'Brak programu {arguments[0]}.') from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError(f'Program {arguments[0]} nie odpowiedział w wymaganym czasie.') from exc
    if result.returncode:
        details = (result.stderr or result.stdout or '').strip()
        raise OSError(details or f'Program {arguments[0]} zakończył się błędem {result.returncode}.')
    return result.stdout


def discover(runner=None):
    runner = runner or subprocess.run
    command = ['lsblk', '--json', '--bytes', '--output', COLUMNS]
    try:
        output = _run(command, runner, 12)
    except OSError as exc:
        if 'MOUNTPOINTS' not in str(exc):
            raise
        output = _run(command[:-1] + [COLUMNS.replace('MOUNTPOINTS', 'MOUNTPOINT')], runner, 12)
    try:
        return parse_devices(json.loads(output))
    except (ValueError, TypeError) as exc:
        raise OSError(f'Nie udało się odczytać listy nośników: {exc}') from exc


def operate(path, action, runner=None):
    """Recheck current mount state before each operation; Polkit handles authorization."""
    if action not in {'mount', 'unmount', 'power-off'} or not _device_path(path):
        raise ValueError('Nieprawidłowa operacja na nośniku.')
    runner = runner or subprocess.run
    current = discover(runner)
    device = next((entry for entry in current if entry.path == path), None)
    if device is None:
        raise OSError('Nośnik został odłączony lub nie jest już dostępny.')
    if action == 'mount':
        if device.mountpoints:
            return current
        if not device.can_mount:
            raise OSError('Ten nośnik nie ma systemu plików, który można zamontować.')
        _run(['udisksctl', 'mount', '--block-device', device.path], runner, 120)
    elif action == 'unmount':
        if device.protected:
            raise OSError('Nie można odmontować systemowych lokalizacji /, /home lub /boot.')
        if device.mountpoints:
            _run(['udisksctl', 'unmount', '--block-device', device.path], runner, 120)
    else:
        if not device.can_power_off:
            raise OSError('Nie można bezpiecznie wyłączyć tego nośnika: jest systemowy lub niewymienny.')
        siblings = [entry for entry in current if entry.disk_path == device.disk_path]
        if any(entry.protected for entry in siblings):
            raise OSError('Nośnik zawiera zamontowaną partycję systemową i nie może zostać wyłączony.')
        for entry in siblings:
            if entry.mountpoints:
                _run(['udisksctl', 'unmount', '--block-device', entry.path], runner, 120)
        _run(['udisksctl', 'power-off', '--block-device', device.disk_path], runner, 120)
    return discover(runner)
