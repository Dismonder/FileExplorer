#!/usr/bin/env python3
"""Standalone CachyOS/Plasma settings application."""
import argparse
import sys

from PySide6.QtCore import QTimer, QSettings
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication

from icons import icon
from settings_panel import SettingsDialog
from settings_theme import apply_palette


def create_app():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('CachyOSSettings')
    app.setApplicationDisplayName('Ustawienia — CachyOS')
    app.setDesktopFileName('cachyos-fluent-settings')
    app.setWindowIcon(icon('settings-appicon'))
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    saved = QSettings('Damian', 'FileExplorer').value('theme')
    system_theme = 'dark' if app.palette().color(QPalette.Window).lightness() < 128 else 'light'
    apply_palette(app, saved or system_theme)
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description='Centrum ustawień CachyOS / KDE Plasma')
    parser.add_argument('--screenshot', metavar='PNG', help='zapisz zrzut ekranu i zakończ')
    parser.add_argument('--category', default='home', help='pokaż wybraną kategorię')
    args = parser.parse_args(argv)
    app = create_app()
    window = SettingsDialog()
    for index, (key, _, _) in enumerate(window.nav):
        if key == args.category:
            window.navigate_index(index)
            window.nav[index][2].setChecked(True)
            break
    window.show()
    if args.screenshot:
        def capture():
            window.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(1400, capture)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
