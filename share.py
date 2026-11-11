"""Real sharing back-ends: KDE Connect devices, e-mail, Bluetooth and a local-network download link.

Everything here is Qt-free so it can be unit-tested and reused; the dialog lives in main.py.
"""
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote


def lan_address():
    """Best-effort LAN IPv4 of this machine (no packets are sent)."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('10.255.255.255', 1))
        return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


def lan_addresses():
    """All non-loopback IPv4 addresses as (interface, address); Wi-Fi first, then the default-route one."""
    result = []
    try:
        import json
        data = json.loads(subprocess.run(['ip', '-j', '-4', '-br', 'addr'], capture_output=True, text=True, timeout=3).stdout)
        for entry in data:
            name = entry.get('ifname', '')
            if name == 'lo' or entry.get('operstate') not in ('UP', 'UNKNOWN'):
                continue
            for info in entry.get('addr_info', []):
                address = info.get('local', '')
                if address and not address.startswith('127.'):
                    result.append((name, address))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    default = lan_address()
    result.sort(key=lambda item: (not item[0].startswith(('wl', 'wifi')), item[1] != default, item[0]))
    if not result:
        result.append(('', default))
    return result


QUICKSHARE_SERVICE = '_FC9F5ED42C8A._tcp'
QUICKSHARE_TYPES = {1: 'telefon', 2: 'tablet', 3: 'komputer'}


def decode_endpoint(encoded):
    """Decode the Quick Share endpoint info from the mDNS TXT record: (name or '', hidden flag, device kind)."""
    import base64
    try:
        raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
    except (ValueError, TypeError):
        return '', True, ''
    if not raw:
        return '', True, ''
    hidden = bool((raw[0] >> 4) & 1)
    kind = QUICKSHARE_TYPES.get((raw[0] >> 1) & 7, 'urządzenie')
    name = ''
    if len(raw) > 17:
        length = raw[17]
        name = raw[18:18 + length].decode('utf-8', 'replace')
    return name, hidden, kind


def nearby_quickshare(timeout=4):
    """Quick Share endpoints visible via mDNS (needs avahi-browse): list of dicts with host, address, name, hidden, kind, iface."""
    if not shutil.which('avahi-browse'):
        return None
    try:
        output = subprocess.run(['avahi-browse', '-rtp', QUICKSHARE_SERVICE], capture_output=True, text=True, timeout=timeout + 6).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    devices = {}
    for line in output.splitlines():
        if not line.startswith('='):
            continue
        parts = line.split(';')
        if len(parts) < 10 or parts[2] != 'IPv4':
            continue
        iface, host, address, port, txt = parts[1], parts[6], parts[7], parts[8], parts[9]
        match = re.search(r'"n=([A-Za-z0-9_-]+)"', txt)
        name, hidden, kind = decode_endpoint(match.group(1)) if match else ('', True, '')
        devices[address] = {'iface': iface, 'host': host.removesuffix('.local'), 'address': address, 'port': port,
                            'name': name, 'hidden': hidden, 'kind': kind}
    return list(devices.values())


def unique_names(paths):
    """Map display names to paths, de-duplicating names like a file manager would."""
    result = {}
    for path in map(Path, paths):
        name = path.name or 'element'
        candidate, number = name, 1
        while candidate in result:
            number += 1
            candidate = f'{path.stem} ({number}){path.suffix}'
        result[candidate] = path
    return result


def pack_paths(paths, target_dir, name='udostępnione', should_stop=None):
    """Zip files and folders into `target_dir` (folders recursively, symlinks followed) and return the archive path."""
    import stat
    import tempfile
    import zipfile

    if not name or Path(name).name != name or name in ('.', '..'):
        raise ValueError('Nieprawidłowa nazwa archiwum.')
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=f'{name}-', suffix='.zip', dir=target_dir)
    os.close(descriptor)
    archive = Path(filename)
    archive_identity = archive.stat().st_dev, archive.stat().st_ino

    def check_cancelled():
        if should_stop and should_stop():
            raise InterruptedError('Przerwano pakowanie.')

    try:
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
            def add(path, display, ancestors):
                check_cancelled()
                info = path.stat()  # Follow links, including links to directories.
                identity = info.st_dev, info.st_ino
                if identity == archive_identity:
                    return  # The output directory may be inside the selected folder.
                if stat.S_ISDIR(info.st_mode):
                    if identity in ancestors:
                        raise OSError(f'Wykryto pętlę dowiązań: {path}')
                    bundle.write(path, display)
                    for child in path.iterdir():
                        add(child, f'{display}/{child.name}', ancestors | {identity})
                elif stat.S_ISREG(info.st_mode):
                    entry = zipfile.ZipInfo.from_file(path, display)
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    with path.open('rb') as source, bundle.open(entry, 'w', force_zip64=True) as target:
                        while True:
                            check_cancelled()
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            target.write(chunk)
                else:
                    raise shutil.SpecialFileError(f'Nie można spakować pliku specjalnego: {path}')
            for display, path in unique_names(paths).items():
                add(path, display, set())
            check_cancelled()
    except BaseException:
        archive.unlink(missing_ok=True)
        raise
    return archive


class ShareServer:
    """Serve the given files over HTTP on the LAN under a random token path, in a daemon thread."""

    def __init__(self, paths, port=0, address=None):
        import http.server
        self.files = unique_names(paths)
        self.token = secrets.token_urlsafe(6)
        self.address = address or lan_address()
        self.downloads = 0
        self.started = time.time()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.handle_request(head=True)

            def do_GET(self):
                self.handle_request()

            def handle_request(self, head=False):
                parts = [unquote(p) for p in self.path.split('?')[0].split('/') if p]
                if not parts or parts[0] != server.token:
                    self.send_error(404)
                    return
                if len(parts) == 1:
                    body = server.index_html().encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                    return
                path = server.files.get(parts[1])
                if path is None or not path.is_file():
                    self.send_error(404)
                    return
                size = path.stat().st_size
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(size))
                self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{quote(parts[1])}")
                self.end_headers()
                if head:
                    return
                with path.open('rb') as source:
                    shutil.copyfileobj(source, self.wfile, 1024 * 1024)
                server.downloads += 1

        self.server = http.server.ThreadingHTTPServer(('0.0.0.0', port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='share-server')
        self.thread.start()

    @property
    def url(self):
        return f'http://{self.address}:{self.port}/{self.token}/'

    def index_html(self):
        import html
        rows = []
        for name, path in self.files.items():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            rows.append(f'<li><a href="{quote(name)}" download>{html.escape(name)}</a> <small>{size / 1048576:.1f} MB</small></li>')
        return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Udostępnione pliki</title><style>body{font-family:sans-serif;margin:2rem;background:#f3f3f3;color:#202020}'
                'li{margin:.6rem 0;font-size:1.1rem}a{color:#0067c0;text-decoration:none}small{color:#737373}</style>'
                f'<h2>Udostępnione pliki ({len(self.files)})</h2><ul>{"".join(rows)}</ul>')

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def qr_png(text):
    """PNG bytes of a QR code for `text`, or None when qrencode is not installed."""
    if not shutil.which('qrencode'):
        return None
    try:
        result = subprocess.run(['qrencode', '-o', '-', '-t', 'PNG', '-s', '6', '-m', '2', text], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def kdeconnect_devices():
    """Reachable KDE Connect devices as (id, name)."""
    if not shutil.which('kdeconnect-cli'):
        return []
    try:
        output = subprocess.run(['kdeconnect-cli', '-a', '--id-name-only'], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    devices = []
    for line in output.splitlines():
        line = line.strip()
        device_id, _, name = line.partition(' ')
        # Real ids look like "_e3f1c2a7_6b1d_4f0e_..." ; the localized "Found N devices" summary does not.
        if re.fullmatch(r'[A-Za-z0-9_-]{8,}', device_id) and not re.search(r'\d+ (urządze|device)', line):
            devices.append((device_id, name.strip() or device_id))
    return devices


def launch(command):
    subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def send_kdeconnect(device_id, paths):
    for path in paths:
        launch(['kdeconnect-cli', '-d', device_id, '--share', str(path)])


def send_email(paths):
    command = ['xdg-email']
    for path in paths:
        command += ['--attach', str(path)]
    launch(command)


def send_bluetooth(paths):
    command = ['bluedevil-sendfile']
    for path in paths:
        command += ['-f', str(path)]
    launch(command)


QUICKSHARE_BINARIES = ('rquickshare', 'r-quick-share', 'quickshare', 'rqs')
QUICKSHARE_FLATPAK = 'com.github.martichou.rquickshare'


def quickshare_command():
    """Command prefix that starts Quick Share (Samsung/Google Nearby Share) on this machine, or None."""
    for binary in QUICKSHARE_BINARIES:
        path = shutil.which(binary)
        if path:
            return [path]
    for candidate in Path('/opt').glob('*uick*hare*/*'):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
    for appimage in list(Path.home().glob('Applications/*uick*hare*.AppImage')) + list(Path.home().glob('*uick*hare*.AppImage')):
        if os.access(appimage, os.X_OK):
            return [str(appimage)]
    if shutil.which('flatpak'):
        try:
            output = subprocess.run(['flatpak', 'list', '--app', '--columns=application'], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            output = ''
        if QUICKSHARE_FLATPAK in output:
            return ['flatpak', 'run', QUICKSHARE_FLATPAK]
    return None


def quickshare_running():
    try:
        return subprocess.run(['pgrep', '-x', 'rquickshare'], capture_output=True, timeout=2).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def send_quickshare(paths):
    """Start or raise the Quick Share window (rquickshare is single-instance, so a second launch just focuses it).

    rquickshare ignores command-line paths but accepts files dropped onto its window, so the caller
    offers a drag handle with the selected files. Returns True when the app was already running.
    """
    command = quickshare_command()
    if command is None:
        raise OSError('Quick Share nie jest zainstalowany')
    running = quickshare_running()
    launch(command)
    return running


def targets():
    """Available share targets: (key, title, description, icon name, needs_plain_files)."""
    result = []
    for device_id, name in kdeconnect_devices():
        result.append((f'kdeconnect:{device_id}', name, 'Wyślij przez KDE Connect', 'phone', True))
    if quickshare_command():
        result.append(('quickshare:app', 'Quick Share', 'Telefony Samsung i Android w pobliżu (rquickshare)', 'wifi', True))
    else:
        result.append(('quickshare:missing', 'Quick Share', 'Niedostępne — zainstaluj r-quick-share (AUR) lub AppImage', 'wifi', True))
    if shutil.which('xdg-email'):
        result.append(('email', 'E-mail', 'Nowa wiadomość z załącznikami', 'mail', True))
    if shutil.which('bluedevil-sendfile'):
        result.append(('bluetooth', 'Bluetooth', 'Wyślij do sparowanego urządzenia', 'bluetooth', True))
    result.append(('lan', 'Łącze w sieci lokalnej', 'Pobierz w przeglądarce lub zeskanuj kod QR', 'wifi', True))
    result.append(('copy', 'Kopiuj ścieżki', 'Ścieżki plików do schowka', 'path', False))
    result.append(('uri', 'Kopiuj adresy file://', 'Adresy URI do schowka', 'link', False))
    return result
