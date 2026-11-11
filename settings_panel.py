"""Native KDE settings hub used by FileExplorer.

This is deliberately a thin shell around Plasma modules: cards never pretend to
change a setting when the desktop does not expose a safe public API.  The few
fast controls shown on Home use the real NetworkManager, BlueZ, PipeWire and
Power Profiles D-Bus command line clients through :mod:`settings_backend`.
"""
from __future__ import annotations

import time
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, QRunnable, QThreadPool, QObject, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QStackedWidget, QVBoxLayout, QWidget,
)

from icons import icon
from settings_backend import SettingsBackend
from settings_catalog import CATEGORIES, MODULE_INFO, module_info


def _fold(value):
    value = str(value).casefold().replace('ł', 'l')
    return ''.join(char for char in unicodedata.normalize('NFKD', value)
                   if not unicodedata.combining(char))


def _box(parent=None, vertical=False, margins=(0, 0, 0, 0), spacing=0):
    layout = QVBoxLayout(parent) if vertical else QHBoxLayout(parent)
    layout.setContentsMargins(*margins); layout.setSpacing(spacing)
    return layout


def _label(text, name='', wrap=False):
    label = QLabel(text); label.setObjectName(name); label.setWordWrap(wrap)
    label.setTextFormat(Qt.PlainText)
    return label


class _TaskSignals(QObject):
    done = Signal(object)


class _Task(QRunnable):
    def __init__(self, function):
        super().__init__(); self.function = function; self.signals = _TaskSignals(QApplication.instance()); self.setAutoDelete(True)
    def run(self):
        try: result = self.function()
        except Exception as exc: result = exc
        self.signals.done.emit(result)


class SettingsDialog(QDialog):
    """A complete, searchable front end for the KDE modules installed locally."""
    NAV = (
        ('home', 'Strona główna', 'home', 'Szybki dostęp i stan komputera'),
        ('system', 'System', 'system', 'Ekran, dźwięk, zasilanie, pamięć'),
        ('bluetooth', 'Bluetooth i urządzenia', 'bluetooth', 'Urządzenia, drukarki, mysz'),
        ('network', 'Sieć i internet', 'network', 'Wi-Fi, VPN, zapora'),
        ('personalization', 'Personalizacja', 'personalization', 'Motywy, kolory, pulpit'),
        ('apps', 'Aplikacje', 'apps', 'Domyślne, skojarzenia, autostart'),
        ('accounts', 'Konta', 'accounts', 'Użytkownicy, portfele, konta online'),
        ('time', 'Czas i język', 'time', 'Data, język, klawiatura'),
        ('gaming', 'Granie', 'gaming', 'Kontrolery i GameMode'),
        ('accessibility', 'Ułatwienia dostępu', 'accessibility', 'Dostępność Plasma'),
        ('privacy', 'Prywatność i zabezpieczenia', 'privacy', 'Wyszukiwanie, blokada, zapora'),
        ('update', 'Aktualizacje', 'update', 'Pakiety i aktualizacje systemu'),
        ('all', 'Wszystkie ustawienia', 'system', 'Pełny katalog zainstalowanych modułów Plasma'),
    )
    CATEGORY_KEYS = {key: (title, desc, glyph) for key, title, glyph, desc in NAV}

    def __init__(self, explorer=None):
        super().__init__(explorer)
        self.explorer = explorer
        self.backend = SettingsBackend()
        self._state = {}
        self._online_updates = None
        self._online_task_running = False
        self._task_running = False
        self._pending_operations = []
        self._refresh_pending = False
        self._nav_history = []
        self._search_origin = 0
        self.setWindowTitle('Ustawienia — CachyOS')
        self.setWindowIcon(icon('settings-appicon'))
        self.setMinimumSize(1000, 680); self.resize(1180, 760)
        root = _box(self, vertical=True)
        header = QWidget(); header.setObjectName('settingsTop'); header.setFixedHeight(78)
        header_l = _box(header, margins=(24, 15, 24, 13), spacing=14)
        self.back = QPushButton(icon('back'), ''); self.back.setObjectName('settingsBack'); self.back.setToolTip('Wstecz'); self.back.setFixedSize(36, 36); self.back.setEnabled(False); self.back.clicked.connect(self.go_back); header_l.addWidget(self.back)
        header_l.addWidget(_label('Ustawienia', 'settingsTitle')); header_l.addStretch()
        self.search = QLineEdit(); self.search.setObjectName('settingsSearch'); self.search.setPlaceholderText('Znajdź ustawienie'); self.search.setClearButtonEnabled(True); self.search.setMinimumWidth(320); self.search.addAction(icon('search'), QLineEdit.LeadingPosition); header_l.addWidget(self.search); root.addWidget(header)
        body = QWidget(); body_l = _box(body, margins=(18, 0, 18, 18), spacing=18)
        sidebar = QWidget(); sidebar.setObjectName('settingsSidebar'); sidebar.setMinimumWidth(264); sidebar.setMaximumWidth(302); side_l = _box(sidebar, vertical=True, margins=(8, 12, 8, 10), spacing=2)
        profile = QWidget(); profile_l = _box(profile, margins=(10, 8, 10, 14), spacing=10)
        username = Path.home().name
        avatar = QLabel(username[:1].upper()); avatar.setObjectName('settingsAvatar'); avatar.setAlignment(Qt.AlignCenter); avatar.setFixedSize(44, 44); profile_l.addWidget(avatar)
        who = QVBoxLayout(); who.setContentsMargins(0, 0, 0, 0); who.setSpacing(2); who.addWidget(_label(username, 'settingsName')); who.addWidget(_label('CachyOS • KDE Plasma', 'muted')); profile_l.addLayout(who); side_l.addWidget(profile)
        self.nav_group = QButtonGroup(self); self.nav_group.setExclusive(True); self.nav = []
        self.stack = QStackedWidget(); self.stack.setObjectName('settingsStack')
        for index, (key, title, glyph, desc) in enumerate(self.NAV):
            button = QPushButton(icon(f'settings-{glyph}'), title); button.setObjectName('settingsNav'); button.setCheckable(True); button.setIconSize(QSize(22, 22)); button.setToolTip(desc); self.nav_group.addButton(button, index); self.nav.append((key, title, button)); side_l.addWidget(button)
            self.stack.addWidget(self.make_page(key, title, desc))
        side_l.addStretch(); side_l.addWidget(_label(f'CachyOS • {Path.home().name}', 'muted')); body_l.addWidget(sidebar); body_l.addWidget(self.stack, 1); root.addWidget(body, 1)
        self.nav_group.idClicked.connect(self.navigate_index); self.nav[0][2].setChecked(True)
        self.search.textChanged.connect(self.search_changed)
        QShortcut(QKeySequence('Alt+Left'), self, activated=self.go_back)
        QShortcut(QKeySequence('Ctrl+E'), self, activated=self.search.setFocus)
        self.refresh_timer = QTimer(self); self.refresh_timer.setSingleShot(True); self.refresh_timer.setInterval(120); self.refresh_timer.timeout.connect(self.refresh_state)
        self.periodic_timer = QTimer(self); self.periodic_timer.setInterval(15000); self.periodic_timer.timeout.connect(self.refresh_state); self.periodic_timer.start()
        self.refresh_state()
        if self.backend.has('checkupdates'):
            self.check_online_updates(automatic=True)
            self.online_timer = QTimer(self); self.online_timer.setInterval(600000)
            self.online_timer.timeout.connect(lambda: self.check_online_updates(automatic=True))
            self.online_timer.start()

    def navigate_index(self, index):
        if index < 0 or index >= len(self.nav): return
        if self.search.text():
            self.search.blockSignals(True); self.search.clear(); self.search.blockSignals(False)
            self._clear_search()
        old = self.stack.currentIndex()
        if old != index: self._nav_history.append(old)
        self._rebuild_dynamic_page(index)
        self.stack.setCurrentIndex(index); self.nav[index][2].setChecked(True); self.back.setEnabled(bool(self._nav_history))

    def go_back(self):
        if self.search.text():
            self.search.clear()
            return
        if not self._nav_history: return
        index = self._nav_history.pop(); self._rebuild_dynamic_page(index); self.stack.setCurrentIndex(index); self.nav[index][2].setChecked(True); self.back.setEnabled(bool(self._nav_history))

    def search_changed(self, text):
        query = _fold(text).strip()
        if not query:
            self._clear_search()
            return
        if not hasattr(self, '_search_page'):
            self._search_origin = self.stack.currentIndex()
        for key, title, button in self.nav:
            button.setVisible(query in _fold(title))
        # Search opens a real module catalogue page rather than silently filtering cards.
        page = self.make_search_page(query)
        existing = self.stack.indexOf(self._search_page) if hasattr(self, '_search_page') else -1
        if existing < 0: self._search_page = page; self.stack.addWidget(page); existing = self.stack.indexOf(page)
        else:
            old = self.stack.widget(existing); self.stack.removeWidget(old); old.deleteLater(); self._search_page = page; self.stack.addWidget(page); existing = self.stack.indexOf(page)
        self.stack.setCurrentIndex(existing)
        self.back.setEnabled(True)

    def _clear_search(self):
        for _, _, button in self.nav: button.show()
        if hasattr(self, '_search_page'):
            self.stack.removeWidget(self._search_page)
            self._search_page.deleteLater()
            del self._search_page
        origin = self._search_origin if 0 <= self._search_origin < len(self.nav) else 0
        self._rebuild_dynamic_page(origin)
        self.stack.setCurrentIndex(origin)
        self.nav[origin][2].setChecked(True)
        self.back.setEnabled(bool(self._nav_history))

    def make_search_page(self, query):
        page = QWidget(); layout = _box(page, vertical=True, margins=(18, 16, 24, 16), spacing=12); layout.addWidget(_label('Wyniki wyszukiwania', 'settingsPageTitle')); layout.addWidget(_label(f'Ustawienia pasujące do „{query}”', 'muted'))
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); content = QWidget(); rows = _box(content, vertical=True, spacing=8); count = 0
        available = self.backend.modules()
        for module in sorted(available, key=lambda item: self.record(item).get('title', item)):
            info = self.record(module)
            category = CATEGORIES.get(info.get('category', ''), ('', ()))[0]
            hay = _fold(' '.join(str(info.get(k, '')) for k in ('title', 'description', 'category')) + ' ' + category)
            if query not in hay: continue
            rows.addWidget(self.module_card(module, info)); count += 1
        if not count: rows.addWidget(_label('Brak pasujących modułów.', 'empty'))
        rows.addStretch(); scroll.setWidget(content); layout.addWidget(scroll, 1); return page

    def record(self, module):
        info = MODULE_INFO.get(module)
        return info or module_info(module, 'Natywny moduł KDE Plasma. Otwiera rzeczywiste ustawienia systemu.')

    def module_card(self, module, info):
        title = info.get('title', module); description = info.get('description', 'Otwórz moduł KDE Plasma.')
        card = QFrame(); card.setObjectName('settingsCard'); card_l = _box(card, margins=(14, 11, 14, 11), spacing=12); key = info.get('icon', 'system'); picture = QLabel(); picture.setObjectName('settingsCardIcon'); picture.setFixedSize(36, 36); picture.setAlignment(Qt.AlignCenter); picture.setPixmap(icon(f'settings-{key}' if not key.startswith('settings-') else key).pixmap(QSize(28, 28))); card_l.addWidget(picture); text_l = QVBoxLayout(); text_l.setContentsMargins(0, 0, 0, 0); text_l.setSpacing(2); text_l.addWidget(_label(title, 'settingsCardTitle')); text_l.addWidget(_label(description, 'muted', True)); card_l.addLayout(text_l, 1); open_button = QPushButton('Otwórz'); open_button.clicked.connect(lambda checked=False, m=module: self.open_module(m)); card_l.addWidget(open_button); return card

    def make_page(self, key, title, desc):
        page = QWidget()
        outer = _box(page, vertical=True, margins=(18, 16, 24, 16), spacing=6)
        outer.addWidget(_label(title, 'settingsPageTitle')); outer.addWidget(_label(desc, 'muted'))
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget(); layout = _box(body, vertical=True, margins=(0, 12, 0, 0), spacing=12)
        if key == 'home': self.make_home(layout)
        else:
            if key == 'network': self.make_network_overview(layout)
            elif key == 'bluetooth': self.make_bluetooth_overview(layout)
            elif key == 'time': self.make_time_overview(layout)
            elif key == 'update': self.make_update_overview(layout)
            installed = self.backend.modules()
            if key == 'all':
                configured = tuple(sorted(installed, key=lambda module: _fold(self.record(module)['title'])))
            else:
                configured = CATEGORIES.get(key, (title, ()))[1]
                if key == 'system':
                    configured += tuple(sorted(installed - MODULE_INFO.keys()))
            listed = [module for module in configured if module in installed]
            if listed:
                for module in listed: layout.addWidget(self.module_card(module, self.record(module)))
            else: self.make_category_fallback(layout, key)
        layout.addStretch(); scroll.setWidget(body); outer.addWidget(scroll, 1)
        return page

    def make_network_overview(self, layout):
        state = self._state
        network = state.get('network', {})
        connected = network.get('connection') or 'Brak aktywnego połączenia'
        layout.addWidget(self.card('Stan połączenia', f"{connected}. NetworkManager: {'włączony' if network.get('networking') else 'wyłączony' if network.get('networking') is False else 'niedostępny'}.", self.native_button('Połączenia', 'network'), 'network'))
        networking = self.toggle('Sieć', 'network', network.get('networking'), self.toggle_networking)
        layout.addWidget(self.card('Połączenia sieciowe', 'Włącza lub wyłącza obsługę sieci przez NetworkManager.', networking, 'network'))
        wifi = self.toggle('Wi-Fi', 'network', network.get('wifi'), self.toggle_wifi)
        layout.addWidget(self.card('Wi-Fi', 'Włącza lub wyłącza radio Wi-Fi przez NetworkManager.', wifi, 'network'))
        for entry in network.get('interfaces', ()):
            if entry.get('state') == 'connected':
                layout.addWidget(self.card(entry.get('device', 'Interfejs'), entry.get('connection') or 'Połączono', None, 'network'))

    def make_bluetooth_overview(self, layout):
        state = self._state
        bluetooth = state.get('bluetooth', {})
        devices = bluetooth.get('devices', 0)
        state_text = 'włączony' if bluetooth.get('powered') else 'wyłączony' if bluetooth.get('powered') is False else 'niedostępny'
        toggle = self.toggle('Bluetooth', 'bluetooth', bluetooth.get('powered'), self.toggle_bluetooth)
        layout.addWidget(self.card('Bluetooth', f'Adapter jest {state_text}; wykryte urządzenia: {devices}.', toggle, 'bluetooth'))

    def make_time_overview(self, layout):
        state = self._state
        ntp = state.get('ntp_enabled')
        text = f"Strefa czasowa: {state.get('timezone') or 'nieznana'}. "
        text += 'Automatyczna synchronizacja jest włączona.' if ntp else 'Automatyczna synchronizacja jest wyłączona.' if ntp is False else 'Stan synchronizacji jest niedostępny.'
        if ntp and state.get('ntp') is False: text += ' Oczekiwanie na synchronizację.'
        toggle = self.toggle('Automatyczny czas', 'time', ntp, self.toggle_ntp)
        layout.addWidget(self.card('Data i godzina', text, toggle, 'time'))

    def make_update_overview(self, layout):
        state = self._state
        layout.addWidget(self.card('Aktualizacje pakietów', self.update_text(state), self.update_actions(), 'update'))
        packages = state.get('updates', {}).get('packages') or []
        if packages:
            listing = QFrame(); listing.setObjectName('settingsCard')
            lines = _box(listing, vertical=True, margins=(14, 11, 14, 11), spacing=5)
            lines.addWidget(_label(f'Pierwsze {len(packages)} pakietów', 'settingsCardTitle'))
            for package in packages: lines.addWidget(_label(package, 'muted', True))
            layout.addWidget(listing)

    def make_home(self, layout):
        state = self._state
        identity = QWidget(); identity.setObjectName('settingsHero'); hero = _box(identity, margins=(18, 14, 18, 14), spacing=14); pic = QLabel(); pic.setPixmap(icon('settings-system').pixmap(42, 42)); hero.addWidget(pic); text = QVBoxLayout(); text.setContentsMargins(0, 0, 0, 0); text.setSpacing(2); text.addWidget(_label(state.get('hostname', 'Ten komputer'), 'settingsHeroTitle')); text.addWidget(_label(f"{state.get('distro', 'Linux')} • {state.get('desktop', 'KDE Plasma')} • kernel {state.get('kernel', '')}", 'muted')); hero.addLayout(text, 1); about = QPushButton('Informacje'); about.clicked.connect(lambda: self.open_module('about')); hero.addWidget(about); layout.addWidget(identity)
        stats = QWidget(); grid = QGridLayout(stats); grid.setContentsMargins(0, 0, 0, 0); grid.setSpacing(8)
        memory = state.get('memory', {}); disk = state.get('home_disk', {}); net = state.get('network', {}); power = state.get('power', {})
        values = [('Pamięć', f"{memory.get('percent', 0)}% użyte", 'storage'), ('Dysk domowy', f"{disk.get('percent', 0)}% użyte", 'storage'), ('Sieć', str(net.get('connection') or 'Rozłączono'), 'network'), ('Profil zasilania', str(power.get('profile') or 'Nieznany'), 'system')]
        for i, (name, value, glyph) in enumerate(values): grid.addWidget(self.stat_card(name, value, glyph), 0, i)
        layout.addWidget(stats)
        self.quick = QFrame(); self.quick.setObjectName('settingsQuick'); ql = _box(self.quick, vertical=True, margins=(16, 13, 16, 13), spacing=8); ql.addWidget(_label('Szybkie ustawienia', 'settingsSectionTitle'))
        row = _box(spacing=9); self.wifi = self.toggle('Wi-Fi', 'network', net.get('wifi'), self.toggle_wifi); row.addWidget(self.wifi); self.bluetooth = self.toggle('Bluetooth', 'bluetooth', state.get('bluetooth', {}).get('powered'), self.toggle_bluetooth); row.addWidget(self.bluetooth); self.muted = self.toggle('Wycisz', 'sound', state.get('audio', {}).get('muted'), self.toggle_mute); row.addWidget(self.muted); ql.addLayout(row)
        volume_row = _box(spacing=10)
        volume_row.addWidget(_label('Głośność', 'muted'))
        self.volume_value = QLabel(); self.volume_value.setObjectName('settingsValue')
        self.volume = QSlider(Qt.Horizontal); self.volume.setRange(0, 100); self.volume.setFixedHeight(28)
        self.volume.setValue(int(state.get('audio', {}).get('volume') or 0))
        self.volume.setEnabled(state.get('audio', {}).get('volume') is not None)
        volume_timer = QTimer(self.quick); volume_timer.setSingleShot(True); volume_timer.setInterval(400)
        volume_timer.timeout.connect(lambda slider=self.volume: self.set_backend_value('volume', slider.value()))
        self.volume.valueChanged.connect(lambda value, display=self.volume_value: (display.setText(f'{value}%'), volume_timer.start()))
        self.volume.sliderReleased.connect(lambda slider=self.volume: (volume_timer.stop(), self.set_backend_value('volume', slider.value())))
        self.volume_value.setText(f'{self.volume.value()}%')
        volume_row.addWidget(self.volume, 1); volume_row.addWidget(self.volume_value)
        ql.addLayout(volume_row); layout.addWidget(self.quick)
        profiles = state.get('power', {}).get('profiles') or []
        if profiles:
            chooser = QComboBox(); chooser.setObjectName('settingsPowerProfile')
            chooser.addItems(profiles)
            current = state.get('power', {}).get('profile')
            chooser.setCurrentIndex(max(0, chooser.findText(str(current))))
            chooser.currentTextChanged.connect(lambda value: self.set_backend_value('power', value))
            layout.addWidget(self.card('Profil zasilania', 'Zmień aktywny profil usługi Power Profiles Daemon.', chooser, 'system'))
        update = self.card('Aktualizacje pakietów', self.update_text(state), self.update_actions(), 'update'); layout.addWidget(update)

    def stat_card(self, title, value, glyph):
        card = QFrame(); card.setObjectName('settingsStat'); l = _box(card, margins=(11, 9, 11, 9), spacing=8); p = QLabel(); p.setPixmap(icon(f'settings-{glyph}').pixmap(25, 25)); l.addWidget(p); t = QVBoxLayout(); t.setContentsMargins(0, 0, 0, 0); t.setSpacing(1); t.addWidget(_label(title, 'muted')); t.addWidget(_label(value, 'settingsStatValue')); l.addLayout(t); return card

    def toggle(self, text, glyph, value, callback):
        button = QPushButton(icon(f'settings-{glyph}'), text); button.setObjectName('settingsToggle'); button.setCheckable(True); button.setIconSize(QSize(20, 20)); button.setChecked(bool(value)); button.setEnabled(value is not None); button.toggled.connect(callback); return button

    def card(self, title, description, control=None, glyph='system'):
        card = QFrame(); card.setObjectName('settingsCard'); l = _box(card, margins=(14, 11, 14, 11), spacing=12); p = QLabel(); p.setFixedSize(36, 36); p.setAlignment(Qt.AlignCenter); p.setPixmap(icon(f'settings-{glyph}').pixmap(QSize(28, 28))); l.addWidget(p); t = QVBoxLayout(); t.setContentsMargins(0, 0, 0, 0); t.setSpacing(2); t.addWidget(_label(title, 'settingsCardTitle')); t.addWidget(_label(description, 'muted', True)); l.addLayout(t, 1); control and l.addWidget(control); return card

    def native_button(self, text, module):
        button = QPushButton(text); button.clicked.connect(lambda: self.open_module(module)); return button

    def app_button(self, text, app):
        button = QPushButton(text); button.clicked.connect(lambda: self.open_app(app)); return button

    def make_category_fallback(self, layout, key):
        layout.addWidget(_label('W tej kategorii nie znaleziono zainstalowanych modułów Plasma.', 'empty', True))

    def open_module(self, module):
        if self.backend.launch(module): return
        QMessageBox.warning(self, 'Moduł niedostępny', f'Nie znaleziono zainstalowanego modułu KDE: {module}')

    def open_app(self, app):
        if self.backend.launch_app(app): return
        QMessageBox.warning(self, 'Aplikacja niedostępna', f'Nie znaleziono programu systemowego dla: {app}')

    def update_text(self, state):
        update = state.get('updates', {}); status = update.get('status')
        source = f"po sprawdzeniu online o {update.get('checked_at')}" if update.get('checked_online') else 'według lokalnej bazy pakietów'
        if status == 'none': summary = f'Brak aktualizacji {source}.'
        elif status == 'available': summary = f"Dostępnych pakietów: {update.get('count', 0)} ({source})."
        elif status == 'unavailable': summary = 'Brak dostępu do lokalnej bazy pakietów.'
        else: summary = 'Stan aktualizacji jest nieznany. Otwórz Discover, aby sprawdzić.'
        return summary + (' Trwa sprawdzanie repozytoriów online…' if self._online_task_running else '')

    def update_actions(self):
        panel = QWidget(); row = _box(panel, spacing=8)
        check = QPushButton('Sprawdzanie…' if self._online_task_running else 'Sprawdź online')
        check.setEnabled(self.backend.has('checkupdates') and not self._online_task_running)
        check.setToolTip('Sprawdza repozytoria bez modyfikowania bazy systemowej pacman.')
        check.clicked.connect(self.check_online_updates)
        row.addWidget(check)
        row.addWidget(self.app_button('Otwórz Discover', 'updates'))
        return panel

    def _rebuild_current_dynamic_page(self):
        self._rebuild_dynamic_page(self.stack.currentIndex())

    def _rebuild_dynamic_page(self, index):
        if index < 0 or index >= len(self.nav): return
        key, title, button = self.nav[index]
        if key not in {'home', 'network', 'bluetooth', 'time', 'update'}: return
        was_current = self.stack.currentIndex() == index
        desc = self.CATEGORY_KEYS[key][1]
        old = self.stack.widget(index); new = self.make_page(key, title, desc)
        self.stack.removeWidget(old); self.stack.insertWidget(index, new)
        old.deleteLater(); button.setChecked(True)
        if was_current: self.stack.setCurrentIndex(index)

    def refresh_state(self):
        if self._task_running:
            self._refresh_pending = True
            return
        self._refresh_pending = False
        self._task_running = True; task = _Task(self.backend.snapshot); task.signals.done.connect(self.state_ready); self._task = task; QThreadPool.globalInstance().start(task)

    def state_ready(self, result):
        self._task_running = False
        if isinstance(result, Exception):
            if self._pending_operations: self._run_next_operation()
            return
        self._state = result
        if self._online_updates:
            checked_at, online = self._online_updates
            same_database = result.get('package_db_revision') == online.get('package_db_revision')
            if same_database and time.monotonic() - checked_at < 600:
                self._state['updates'] = online
            else:
                self._online_updates = None
                if not same_database and self.backend.has('checkupdates'):
                    self.check_online_updates(automatic=True)
        if self._pending_operations:
            self._run_next_operation()
            return
        self._rebuild_current_dynamic_page()

    def set_backend_value(self, key, value):
        self._pending_operations.append((key, value))
        if not self._task_running: self._run_next_operation()

    def check_online_updates(self, automatic=False):
        if self._online_task_running: return
        self._online_task_running = True
        self._rebuild_current_dynamic_page()
        task = _Task(self.backend.check_updates_online)
        task.signals.done.connect(lambda result: self.online_updates_ready(result, automatic))
        self._online_task = task
        QThreadPool.globalInstance().start(task)

    def online_updates_ready(self, result, automatic):
        self._online_task_running = False
        if isinstance(result, dict) and result.get('checked_online'):
            result['checked_at'] = time.strftime('%H:%M')
            self._online_updates = (time.monotonic(), result)
            self._state['updates'] = result
        elif not automatic:
            error = result.get('error', '') if isinstance(result, dict) else str(result)
            QMessageBox.warning(self, 'Aktualizacje', f'Nie udało się sprawdzić repozytoriów online. {error}')
        self._rebuild_current_dynamic_page()

    def _run_next_operation(self):
        if not self._pending_operations: return
        key, value = self._pending_operations.pop(0)
        methods = {
            'volume': lambda: self.backend.set_volume(value),
            'wifi': lambda: self.backend.set_wifi(value),
            'networking': lambda: self.backend.set_networking(value),
            'bluetooth': lambda: self.backend.set_bluetooth(value),
            'muted': lambda: self.backend.set_muted(value),
            'power': lambda: self.backend.set_power_profile(value),
            'ntp': lambda: self.backend.set_ntp(value),
        }
        function = methods.get(key)
        if not function:
            self._run_next_operation()
            return
        self._task_running = True; task = _Task(function); task.signals.done.connect(lambda result: self.operation_ready(result, key)); self._task = task; QThreadPool.globalInstance().start(task)

    def operation_ready(self, result, key):
        self._task_running = False
        if isinstance(result, Exception) or (isinstance(result, tuple) and not result[0]):
            detail = str(result) if isinstance(result, Exception) else result[1]
            QMessageBox.warning(self, 'Ustawienia systemowe', f'Nie udało się zmienić ustawienia. {detail}')
        if self._pending_operations: self._run_next_operation()
        else: self.refresh_timer.start()

    def toggle_wifi(self, enabled): self.set_backend_value('wifi', enabled)
    def toggle_networking(self, enabled): self.set_backend_value('networking', enabled)
    def toggle_bluetooth(self, enabled): self.set_backend_value('bluetooth', enabled)
    def toggle_mute(self, enabled): self.set_backend_value('muted', enabled)
    def toggle_ntp(self, enabled): self.set_backend_value('ntp', enabled)
