#!/usr/bin/env python3
"""Install the standalone settings launcher into the current Plasma account."""
from pathlib import Path
import shlex
import shutil
import subprocess

from icons import svg


def install():
    project = Path(__file__).resolve().parent
    root = Path.home()
    bin_dir = root / '.local' / 'bin'
    app_dir = root / '.local' / 'share' / 'applications'
    icon_dir = root / '.local' / 'share' / 'icons' / 'hicolor' / 'scalable' / 'apps'
    for directory in (bin_dir, app_dir, icon_dir):
        directory.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / 'cachyos-fluent-settings'
    launcher.write_text('#!/bin/sh\nexec /usr/bin/python '
                        + shlex.quote(str(project / 'settings_app.py')) + ' "$@"\n')
    launcher.chmod(0o755)
    icon_file = icon_dir / 'cachyos-fluent-settings.svg'
    icon_file.write_text(svg('settings-appicon', '#0078d4'))
    entry = app_dir / 'cachyos-fluent-settings.desktop'
    entry.write_text('[Desktop Entry]\nType=Application\nName=Ustawienia — CachyOS\n'
                     'Comment=Centrum ustawień KDE Plasma w stylu Windows 11\n'
                     f'Exec={launcher}\nIcon=cachyos-fluent-settings\n'
                     'Terminal=false\nCategories=Settings;DesktopSettings;\nStartupNotify=true\n')
    validator = shutil.which('desktop-file-validate')
    if validator:
        subprocess.run([validator, str(entry)], check=True)
    cache = shutil.which('kbuildsycoca6')
    if cache:
        subprocess.run([cache, '--noincremental'], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f'Zainstalowano: {entry}')
    print(f'Polecenie: {launcher}')


if __name__ == '__main__':
    install()
