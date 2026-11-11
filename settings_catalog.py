"""Polski katalog natywnych modułów ustawień KDE Plasma.

Ten moduł nie uruchamia poleceń systemowych.  Jest wspólnym źródłem nazw,
opisów, kategorii i ikon dla okna ustawień.  Lista odpowiada modułom zwracanym
przez ``kcmshell6 --list`` na docelowym CachyOS/KDE Plasma.
"""
from __future__ import annotations

from typing import Final


__all__ = ("CATEGORIES", "MODULE_INFO", "module_info")


# Dwanaście głównych miejsc w nawigacji SettingsDialog.  Pozycja ``all`` jest
# dynamicznym widokiem wyszukiwania i celowo nie jest osobną kategorią katalogu.
CATEGORIES: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "home": (
        "Strona główna",
        ("kcm_landingpage",),
    ),
    "system": (
        "System",
        (
            "kcm_about-distro", "kcm_audio_information", "kcm_block_devices",
            "kcm_bolt", "kcm_cpu", "kcm_device_automounter", "kcm_edid",
            "kcm_egl", "kcm_energyinfo", "kcm_firmware_security", "kcm_glx",
            "kcm_interrupts", "kcm_kscreen", "kcm_kwinsupportinfo", "kcm_memory",
            "kcm_mobile_power", "kcm_notifications", "kcm_opencl", "kcm_pci",
            "kcm_powerdevilprofilesconfig", "kcm_pulseaudio", "kcm_sensors",
            "kcm_smserver", "kcm_soundtheme", "kcm_usb", "kcm_vulkan",
            "kcm_wayland", "kcm_xserver",
        ),
    ),
    "bluetooth": (
        "Bluetooth i urządzenia",
        (
            "kcm_bluetooth", "kcm_mouse", "kcm_solid_actions", "kcm_tablet",
            "kcm_touchpad", "kcm_touchscreen",
        ),
    ),
    "network": (
        "Sieć i internet",
        (
            "kcm_cellular_network", "kcm_mobile_hotspot", "kcm_mobile_wifi",
            "kcm_mobile_wired", "kcm_netpref", "kcm_network",
            "kcm_networkmanagement", "kcm_proxy", "kcm_samba",
        ),
    ),
    "personalization": (
        "Personalizacja",
        (
            "kcm_animations", "kcm_colors", "kcm_cursortheme", "kcm_desktoptheme",
            "kcm_icons", "kcm_kwin_effects", "kcm_kwin_scripts",
            "kcm_kwin_virtualdesktops", "kcm_kwindecoration", "kcm_kwinoptions",
            "kcm_kwinrules", "kcm_kwinscreenedges", "kcm_kwintabbox",
            "kcm_kwintouchscreen", "kcm_kwinxwayland", "kcm_lookandfeel",
            "kcm_nightlight", "kcm_nighttime", "kcm_plasmalogin", "kcm_plymouth",
            "kcm_splashscreen", "kcm_style", "kcm_wallpaper", "kcm_workspace",
        ),
    ),
    "apps": (
        "Aplikacje",
        (
            "kcm_autostart", "kcm_componentchooser", "kcm_desktoppaths",
            "kcm_filetypes", "kcm_fontinst", "kcm_fonts", "kcm_kded",
            "kcm_qtquicksettings", "kcm_webshortcuts", "kcmspellchecking",
        ),
    ),
    "accounts": (
        "Konta",
        ("kcm_kaccounts", "kcm_users"),
    ),
    "time": (
        "Czas i język",
        ("kcm_clock", "kcm_keyboard", "kcm_keys", "kcm_regionandlang", "kcm_virtualkeyboard"),
    ),
    "gaming": (
        "Granie",
        ("kcm_gamecontroller",),
    ),
    "accessibility": (
        "Ułatwienia dostępu",
        ("kcm_access",),
    ),
    "privacy": (
        "Prywatność i zabezpieczenia",
        (
            "kcm_activities", "kcm_baloofile", "kcm_feedback", "kcm_firewall",
            "kcm_kwallet5", "kcm_plasmasearch", "kcm_recentFiles", "kcm_screenlocker",
        ),
    ),
    "update": (
        "Aktualizacje",
        ("kcm_updates",),
    ),
}


def _entry(title: str, description: str, category: str, icon: str) -> dict[str, str]:
    """Create one immutable-by-convention catalogue record."""
    return {
        "title": title,
        "description": description,
        "category": category,
        "icon": icon,
    }


# ``icon`` always names an icon already defined in icons.py.  Keeping the full
# ``settings-*`` names makes this mapping usable outside SettingsDialog as well.
MODULE_INFO: Final[dict[str, dict[str, str]]] = {
    "kcm_about-distro": _entry(
        "Informacje o systemie", "Sprawdź wersję CachyOS, jądro i środowisko Plasma.", "system", "settings-system"),
    "kcm_access": _entry(
        "Dostępność", "Dostosuj działanie pulpitu do swoich potrzeb.", "accessibility", "settings-accessibility"),
    "kcm_activities": _entry(
        "Aktywności", "Zarządzaj aktywnościami oraz ich historią.", "privacy", "settings-privacy"),
    "kcm_animations": _entry(
        "Animacje", "Ustaw szybkość i styl animacji Plasma.", "personalization", "settings-animations"),
    "kcm_audio_information": _entry(
        "Informacje o urządzeniach audio", "Wyświetl techniczne informacje o urządzeniach dźwiękowych.", "system", "settings-system"),
    "kcm_autostart": _entry(
        "Autostart", "Wybierz aplikacje i skrypty uruchamiane po zalogowaniu.", "apps", "settings-startup"),
    "kcm_baloofile": _entry(
        "Wyszukiwanie plików", "Skonfiguruj indeksowanie plików przez Baloo.", "privacy", "settings-privacy"),
    "kcm_block_devices": _entry(
        "Urządzenia blokowe", "Przejrzyj podłączone dyski i nośniki danych.", "system", "settings-storage"),
    "kcm_bluetooth": _entry(
        "Bluetooth", "Paruj urządzenia Bluetooth i zarządzaj ich połączeniami.", "bluetooth", "settings-bluetooth"),
    "kcm_bolt": _entry(
        "Thunderbolt", "Zarządzaj autoryzacją urządzeń Thunderbolt.", "system", "settings-system"),
    "kcm_cellular_network": _entry(
        "Sieć komórkowa", "Skonfiguruj połączenie modemowe i kartę SIM.", "network", "settings-network"),
    "kcm_clock": _entry(
        "Data i godzina", "Ustaw datę, godzinę, strefę czasową i synchronizację czasu.", "time", "settings-time"),
    "kcm_colors": _entry(
        "Kolory", "Wybierz schemat kolorów aplikacji i pulpitu.", "personalization", "settings-colors"),
    "kcm_componentchooser": _entry(
        "Aplikacje domyślne", "Wybierz programy domyślne do najczęstszych zadań.", "apps", "settings-defaults"),
    "kcm_cpu": _entry(
        "Informacje o procesorze", "Wyświetl szczegółowe informacje o procesorze.", "system", "settings-system"),
    "kcm_cursortheme": _entry(
        "Kursor myszy", "Wybierz motyw i rozmiar kursora.", "personalization", "settings-theme"),
    "kcm_desktoppaths": _entry(
        "Foldery osobiste", "Zmień położenie folderów Dokumenty, Pobrane i innych.", "apps", "settings-storage"),
    "kcm_desktoptheme": _entry(
        "Styl Plasma", "Wybierz wygląd paneli, menu i widżetów Plasma.", "personalization", "settings-theme"),
    "kcm_device_automounter": _entry(
        "Automatyczne montowanie", "Ustaw automatyczne montowanie dysków i nośników.", "system", "settings-storage"),
    "kcm_edid": _entry(
        "Informacje o monitorze", "Wyświetl dane identyfikacyjne podłączonych ekranów.", "system", "settings-system"),
    "kcm_egl": _entry(
        "Informacje EGL", "Wyświetl informacje o bibliotece grafiki EGL.", "system", "settings-system"),
    "kcm_energyinfo": _entry(
        "Zużycie energii", "Sprawdź statystyki zużycia energii urządzeń.", "system", "settings-system"),
    "kcm_feedback": _entry(
        "Opinie dla KDE", "Wybierz zakres informacji zwrotnych wysyłanych do KDE.", "privacy", "settings-privacy"),
    "kcm_filetypes": _entry(
        "Skojarzenia plików", "Ustal, które aplikacje otwierają poszczególne typy plików.", "apps", "settings-defaults"),
    "kcm_firewall": _entry(
        "Zapora sieciowa", "Zarządzaj regułami ochrony połączeń sieciowych.", "privacy", "settings-privacy"),
    "kcm_firmware_security": _entry(
        "Bezpieczeństwo firmware", "Sprawdź stan zabezpieczeń oprogramowania układowego.", "system", "settings-privacy"),
    "kcm_fontinst": _entry(
        "Czcionki", "Instaluj, usuwaj i podglądaj czcionki.", "apps", "settings-theme"),
    "kcm_fonts": _entry(
        "Czcionki interfejsu", "Dostosuj czcionki używane przez aplikacje Plasma.", "apps", "settings-theme"),
    "kcm_gamecontroller": _entry(
        "Kontrolery gier", "Testuj i konfiguruj gamepady oraz kontrolery.", "gaming", "settings-gaming"),
    "kcm_glx": _entry(
        "Informacje GLX", "Wyświetl informacje o obsłudze OpenGL przez X11.", "system", "settings-system"),
    "kcm_icons": _entry(
        "Ikony", "Wybierz motyw ikon pulpitu i aplikacji.", "personalization", "settings-theme"),
    "kcm_interrupts": _entry(
        "Przerwania", "Wyświetl informacje diagnostyczne o przerwaniach sprzętowych.", "system", "settings-system"),
    "kcm_kaccounts": _entry(
        "Konta online", "Dodaj i zarządzaj kontami internetowymi.", "accounts", "settings-accounts"),
    "kcm_kded": _entry(
        "Usługi w tle", "Włącz lub wyłącz usługi działające w tle KDE.", "apps", "settings-apps"),
    "kcm_keyboard": _entry(
        "Klawiatura", "Skonfiguruj układy, powtarzanie klawiszy i sprzęt klawiatury.", "time", "settings-time"),
    "kcm_keys": _entry(
        "Skróty klawiszowe", "Zmień globalne i aplikacyjne skróty klawiszowe.", "time", "settings-time"),
    "kcm_kscreen": _entry(
        "Ekran i monitory", "Skonfiguruj rozdzielczość, układ i skalowanie ekranów.", "system", "settings-system"),
    "kcm_kwallet5": _entry(
        "Portfel KDE", "Zarządzaj bezpiecznym przechowywaniem haseł i sekretów.", "privacy", "settings-privacy"),
    "kcm_kwin_effects": _entry(
        "Efekty pulpitu", "Włącz i dostosuj efekty kompozytora KWin.", "personalization", "settings-animations"),
    "kcm_kwin_scripts": _entry(
        "Skrypty KWin", "Zarządzaj skryptami automatyzującymi zachowanie okien.", "personalization", "settings-features"),
    "kcm_kwin_virtualdesktops": _entry(
        "Wirtualne pulpity", "Ustaw liczbę, nazwy i układ wirtualnych pulpitów.", "personalization", "settings-personalization"),
    "kcm_kwindecoration": _entry(
        "Dekoracje okien", "Wybierz wygląd pasków tytułu i obramowań okien.", "personalization", "settings-theme"),
    "kcm_kwinoptions": _entry(
        "Zachowanie okien", "Dostosuj reakcje i akcje wykonywane na oknach.", "personalization", "settings-personalization"),
    "kcm_kwinrules": _entry(
        "Reguły okien", "Twórz reguły dla konkretnych aplikacji i okien.", "personalization", "settings-features"),
    "kcm_kwinscreenedges": _entry(
        "Narożniki ekranu", "Skonfiguruj aktywne narożniki i krawędzie ekranu.", "personalization", "settings-personalization"),
    "kcm_kwinsupportinfo": _entry(
        "Informacje o KWin", "Wyświetl dane diagnostyczne menedżera okien KWin.", "system", "settings-system"),
    "kcm_kwintabbox": _entry(
        "Przełączanie okien", "Dostosuj widok i kolejność przełączania między oknami.", "personalization", "settings-personalization"),
    "kcm_kwintouchscreen": _entry(
        "Gesty na ekranie dotykowym", "Skonfiguruj gesty przesuwania dla KWin.", "personalization", "settings-capture"),
    "kcm_kwinxwayland": _entry(
        "XWayland", "Wybierz klawisze dostępne globalnie dla starszych aplikacji X11.", "personalization", "settings-features"),
    "kcm_landingpage": _entry(
        "Strona startowa Plasma", "Otwórz podstawowe ustawienia i skróty Plasma.", "home", "settings-home"),
    "kcm_lookandfeel": _entry(
        "Globalny motyw", "Zastosuj kompletny motyw wyglądu Plasma.", "personalization", "settings-theme"),
    "kcm_memory": _entry(
        "Pamięć", "Wyświetl informacje o użyciu pamięci operacyjnej.", "system", "settings-storage"),
    "kcm_mobile_hotspot": _entry(
        "Hotspot Wi-Fi", "Udostępniaj połączenie internetowe przez punkt dostępu Wi-Fi.", "network", "settings-network"),
    "kcm_mobile_power": _entry(
        "Zarządzanie energią", "Dostosuj oszczędzanie energii na urządzeniach mobilnych.", "system", "settings-system"),
    "kcm_mobile_wifi": _entry(
        "Wi-Fi", "Połącz się z siecią bezprzewodową i zarządzaj jej ustawieniami.", "network", "settings-network"),
    "kcm_mobile_wired": _entry(
        "Sieć przewodowa", "Skonfiguruj połączenie Ethernet.", "network", "settings-network"),
    "kcm_mouse": _entry(
        "Mysz", "Dostosuj przyciski, szybkość i przewijanie myszy.", "bluetooth", "settings-bluetooth"),
    "kcm_netpref": _entry(
        "Preferencje sieci", "Skonfiguruj ogólne preferencje połączeń sieciowych.", "network", "settings-network"),
    "kcm_network": _entry(
        "Interfejsy sieciowe", "Wyświetl informacje o kartach i interfejsach sieciowych.", "network", "settings-network"),
    "kcm_networkmanagement": _entry(
        "Połączenia sieciowe", "Zarządzaj Wi-Fi, Ethernetem, VPN i zapisanymi połączeniami.", "network", "settings-network"),
    "kcm_nightlight": _entry(
        "Światło nocne", "Dostosuj temperaturę barw ekranu po zmroku.", "personalization", "settings-colors"),
    "kcm_nighttime": _entry(
        "Harmonogram dnia i nocy", "Ustaw godziny używane przez funkcje zależne od pory dnia.", "personalization", "settings-time"),
    "kcm_notifications": _entry(
        "Powiadomienia", "Zmień sposób wyświetlania powiadomień i wykonywane akcje.", "system", "settings-system"),
    "kcm_opencl": _entry(
        "Informacje OpenCL", "Wyświetl informacje o obsłudze obliczeń OpenCL.", "system", "settings-system"),
    "kcm_pci": _entry(
        "Urządzenia PCI", "Wyświetl informacje o urządzeniach podłączonych przez PCI.", "system", "settings-system"),
    "kcm_plasmalogin": _entry(
        "Ekran logowania", "Dostosuj ekran logowania Plasma.", "personalization", "settings-personalization"),
    "kcm_plasmasearch": _entry(
        "Wyszukiwanie Plasma", "Skonfiguruj dostawców wyników wyszukiwania w Plasma.", "privacy", "settings-privacy"),
    "kcm_plymouth": _entry(
        "Ekran uruchamiania", "Wybierz motyw ekranu startowego Plymouth.", "personalization", "settings-theme"),
    "kcm_powerdevilprofilesconfig": _entry(
        "Profile zasilania", "Wybierz profil wydajności i oszczędzania energii.", "system", "settings-system"),
    "kcm_proxy": _entry(
        "Serwer proxy", "Skonfiguruj serwery proxy używane przez system.", "network", "settings-network"),
    "kcm_pulseaudio": _entry(
        "Dźwięk", "Wybierz urządzenia audio oraz dostosuj głośność i wejścia.", "system", "settings-system"),
    "kcm_qtquicksettings": _entry(
        "Ustawienia Qt Quick", "Skonfiguruj dostępne ustawienia aplikacji Qt Quick.", "apps", "settings-apps"),
    "kcm_recentFiles": _entry(
        "Ostatnie pliki", "Zarządzaj historią ostatnio używanych plików i aktywności.", "privacy", "settings-privacy"),
    "kcm_regionandlang": _entry(
        "Region i język", "Ustaw język, format liczb, walut i dat.", "time", "settings-time"),
    "kcm_samba": _entry(
        "Samba", "Sprawdź stan udostępniania plików w sieci Windows/Samba.", "network", "settings-network"),
    "kcm_screenlocker": _entry(
        "Blokada ekranu", "Ustaw automatyczną blokadę i jej zachowanie.", "privacy", "settings-privacy"),
    "kcm_sensors": _entry(
        "Czujniki", "Wyświetl odczyty czujników sprzętowych komputera.", "system", "settings-system"),
    "kcm_smserver": _entry(
        "Sesja pulpitu", "Skonfiguruj logowanie, wylogowanie i przywracanie sesji.", "system", "settings-system"),
    "kcm_solid_actions": _entry(
        "Działania urządzeń", "Wybierz akcje wykonywane po podłączeniu urządzeń.", "bluetooth", "settings-bluetooth"),
    "kcm_soundtheme": _entry(
        "Motyw dźwięków", "Wybierz dźwięki używane przez powiadomienia.", "system", "settings-system"),
    "kcm_splashscreen": _entry(
        "Ekran powitalny", "Wybierz motyw ekranu powitalnego Plasma.", "personalization", "settings-theme"),
    "kcm_style": _entry(
        "Styl aplikacji", "Dostosuj styl i zachowanie aplikacji Qt.", "personalization", "settings-theme"),
    "kcm_tablet": _entry(
        "Tablet graficzny", "Skonfiguruj tablet, pióro i mapowanie ekranu.", "bluetooth", "settings-bluetooth"),
    "kcm_touchpad": _entry(
        "Touchpad", "Dostosuj gesty, przewijanie i czułość touchpada.", "bluetooth", "settings-bluetooth"),
    "kcm_touchscreen": _entry(
        "Ekran dotykowy", "Skonfiguruj urządzenia z ekranem dotykowym.", "bluetooth", "settings-bluetooth"),
    "kcm_updates": _entry(
        "Aktualizacje oprogramowania", "Skonfiguruj zachowanie aktualizacji systemu i aplikacji.", "update", "settings-update"),
    "kcm_usb": _entry(
        "Urządzenia USB", "Wyświetl urządzenia USB podłączone do komputera.", "system", "settings-bluetooth"),
    "kcm_users": _entry(
        "Użytkownicy", "Dodawaj konta użytkowników i zarządzaj ich uprawnieniami.", "accounts", "settings-accounts"),
    "kcm_virtualkeyboard": _entry(
        "Klawiatura ekranowa", "Wybierz klawiaturę wirtualną używaną przez system.", "time", "settings-time"),
    "kcm_vulkan": _entry(
        "Informacje Vulkan", "Wyświetl informacje o obsłudze Vulkan przez grafikę.", "system", "settings-system"),
    "kcm_wallpaper": _entry(
        "Tapeta", "Wybierz i skonfiguruj tapetę pulpitu.", "personalization", "settings-personalization"),
    "kcm_wayland": _entry(
        "Informacje Wayland", "Wyświetl informacje o kompozytorze Wayland.", "system", "settings-system"),
    "kcm_webshortcuts": _entry(
        "Skróty internetowe", "Zarządzaj słowami kluczowymi do wyszukiwania w sieci.", "apps", "settings-apps"),
    "kcm_workspace": _entry(
        "Zachowanie obszaru roboczego", "Dostosuj ogólne zachowanie środowiska Plasma.", "personalization", "settings-personalization"),
    "kcm_xserver": _entry(
        "Informacje serwera X", "Wyświetl informacje o serwerze X11.", "system", "settings-system"),
    "kcmspellchecking": _entry(
        "Sprawdzanie pisowni", "Zarządzaj słownikami i opcjami sprawdzania pisowni.", "apps", "settings-apps"),
}

# Distinct vector glyphs keep technical KCMs recognizable in the full catalogue.
_ICON_GROUPS = {
    'sound': ('kcm_audio_information', 'kcm_pulseaudio', 'kcm_soundtheme'),
    'chip': ('kcm_cpu', 'kcm_interrupts', 'kcm_pci', 'kcm_firmware_security'),
    'display': ('kcm_edid', 'kcm_egl', 'kcm_glx', 'kcm_kscreen',
                'kcm_opencl', 'kcm_vulkan', 'kcm_wayland', 'kcm_xserver',
                'kcm_kwinxwayland'),
    'power': ('kcm_energyinfo', 'kcm_mobile_power', 'kcm_powerdevilprofilesconfig'),
    'notifications': ('kcm_notifications',),
    'keyboard': ('kcm_keyboard', 'kcm_keys', 'kcm_virtualkeyboard'),
    'mouse': ('kcm_mouse', 'kcm_touchpad', 'kcm_tablet'),
    'touch': ('kcm_touchscreen', 'kcm_kwintouchscreen'),
    'lock': ('kcm_kwallet5', 'kcm_screenlocker', 'kcm_plasmalogin'),
    'search': ('kcm_baloofile', 'kcm_plasmasearch', 'kcm_recentFiles'),
    'firewall': ('kcm_firewall',),
    'language': ('kcm_regionandlang',),
    'night': ('kcm_nightlight', 'kcm_nighttime'),
    'wallpaper': ('kcm_wallpaper',),
    'usb': ('kcm_bolt', 'kcm_device_automounter', 'kcm_solid_actions', 'kcm_usb'),
    'cloud': ('kcm_kaccounts',),
    'sensor': ('kcm_sensors',),
    'font': ('kcm_fontinst', 'kcm_fonts', 'kcmspellchecking'),
    'window': ('kcm_kwinoptions', 'kcm_kwinrules', 'kcm_kwinscreenedges',
               'kcm_kwintabbox', 'kcm_kwindecoration', 'kcm_kwinsupportinfo',
               'kcm_kwin_virtualdesktops', 'kcm_workspace'),
}
for _glyph, _modules in _ICON_GROUPS.items():
    for _module in _modules:
        MODULE_INFO[_module]['icon'] = f'settings-{_glyph}'


def _fallback_title(module: str) -> str:
    """Return a readable Polish fallback for an optional, unknown KCM."""
    stem = module.removeprefix("kcm_").replace("_", " ").replace("-", " ").strip()
    return stem[:1].upper() + stem[1:] if stem else "Moduł ustawień"


def module_info(module: str, native_description: str = "") -> dict[str, str]:
    """Return presentation metadata for *module* without exposing shared state.

    Optional KCMs may appear after a package update.  They remain usable even
    before this catalogue is updated: their native description is preserved and
    a neutral System icon is used.
    """
    record = MODULE_INFO.get(module)
    if record is not None:
        return dict(record)
    return _entry(
        _fallback_title(module),
        native_description or "Natywny moduł ustawień KDE Plasma.",
        "system",
        "settings-system",
    )


# Fail during development if a catalogue entry disappears from its category or
# a category references a misspelled KCM.  It also documents that every locally
# listed KCM belongs to exactly one visible category.
_category_modules = tuple(module for _, (_, modules) in CATEGORIES.items() for module in modules)
if set(_category_modules) != set(MODULE_INFO) or len(_category_modules) != len(set(_category_modules)):
    raise RuntimeError("Niespójny katalog modułów ustawień KDE")
