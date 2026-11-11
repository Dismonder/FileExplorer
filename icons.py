from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QIconEngine, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer


PATHS = {
    'back': '<path d="m14 5-7 7 7 7M7 12h14"/>',
    'forward': '<path d="m10 5 7 7-7 7M3 12h14"/>',
    'up': '<path d="m5 10 7-7 7 7M12 3v18"/>',
    'down': '<path d="m7 10 5 5 5-5"/>',
    'right': '<path d="m10 7 5 5-5 5"/>',
    'refresh': '<path d="M19 8a8 8 0 1 0 1 7M19 3v5h-5"/>',
    'search': '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'close': '<path d="m6 6 12 12M6 18 18 6"/>',
    'minimize': '<path d="M5 12h14"/>',
    'maximize': '<rect x="5" y="5" width="14" height="14" rx="1"/>',
    'restore': '<path d="M8 5V3h13v13h-2"/><rect x="3" y="8" width="13" height="13" rx="1"/>',
    'cut': '<circle cx="6" cy="17" r="3"/><circle cx="18" cy="17" r="3"/><path d="m4 3 12 12M20 3 8 15"/>',
    'copy': '<rect x="8" y="7" width="12" height="14" rx="2"/><path d="M16 7V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h4"/>',
    'paste': '<rect x="5" y="5" width="14" height="16" rx="2"/><rect x="9" y="3" width="6" height="4" rx="1"/>',
    'rename': '<rect x="3" y="7" width="18" height="11" rx="2"/><path d="M10 3h6M13 3v19M10 22h6M6 11v3"/>',
    'share': '<path d="M12 15V3m-5 5 5-5 5 5M6 10H4v11h16V10h-2"/>',
    'trash': '<path d="M3 6h18M9 3h6M6 6l1 15h10l1-15M10 10v7M14 10v7"/>',
    'sort': '<path d="M7 3v18m-4-4 4 4 4-4M14 5h7M14 10h5M14 15h3"/>',
    'view': '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 9h18M3 14h18M8 4v16"/>',
    'xlarge': '<rect x="4" y="4" width="16" height="16" rx="2"/>',
    'medium': '<rect x="3" y="3" width="5" height="5" rx="1"/><rect x="9.5" y="3" width="5" height="5" rx="1"/><rect x="16" y="3" width="5" height="5" rx="1"/><rect x="3" y="9.5" width="5" height="5" rx="1"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><rect x="16" y="9.5" width="5" height="5" rx="1"/><rect x="3" y="16" width="5" height="5" rx="1"/><rect x="9.5" y="16" width="5" height="5" rx="1"/><rect x="16" y="16" width="5" height="5" rx="1"/>',
    'small': '<rect x="3" y="4" width="4" height="4" rx="1"/><path d="M10 6h11"/><rect x="3" y="10" width="4" height="4" rx="1"/><path d="M10 12h11"/><rect x="3" y="16" width="4" height="4" rx="1"/><path d="M10 18h11"/>',
    'list': '<rect x="3" y="4" width="3" height="3" rx="0.5"/><path d="M8 5.5h3"/><rect x="3" y="10.5" width="3" height="3" rx="0.5"/><path d="M8 12h3"/><rect x="3" y="17" width="3" height="3" rx="0.5"/><path d="M8 18.5h3"/><rect x="13" y="4" width="3" height="3" rx="0.5"/><path d="M18 5.5h3"/><rect x="13" y="10.5" width="3" height="3" rx="0.5"/><path d="M18 12h3"/><rect x="13" y="17" width="3" height="3" rx="0.5"/><path d="M18 18.5h3"/>',
    'tiles': '<rect x="3" y="4" width="18" height="7" rx="1"/><path d="M6 6.5h3M12 6.5h6M12 9h4"/><rect x="3" y="13" width="18" height="7" rx="1"/><path d="M6 15.5h3M12 15.5h6M12 18h4"/>',
    'content': '<path d="M3 5h18M3 9h10M3 15h18M3 19h10"/>',
    'grid': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    'details': '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M15 4v16M18 8h1M18 12h1M18 16h1"/>',
    'more': '<circle cx="5" cy="12" r=".8"/><circle cx="12" cy="12" r=".8"/><circle cx="19" cy="12" r=".8"/>',
    'pin': '<path d="m14 3 7 7-4 1-3 5-3-3-7 7M8 10l5-3 1-4M8 10l6 6"/>',
    'star': '<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.3-5.7-3-5.7 3 1.1-6.3-4.6-4.5 6.4-.9Z"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'network': '<rect x="7" y="2" width="10" height="7" rx="1"/><rect x="2" y="17" width="7" height="5" rx="1"/><rect x="15" y="17" width="7" height="5" rx="1"/><path d="M12 9v4M5 17v-4h14v4"/>',
    'openwith': '<path d="M11 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-6"/><path d="M14 3h7v7M21 3 11 13"/>',
    'undo': '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-4"/>',
    'redo': '<path d="M15 14l5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h4"/>',
    'link': '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1.5 1.5"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5"/>',
    'compare': '<rect x="3" y="4" width="8" height="16" rx="1"/><rect x="13" y="4" width="8" height="16" rx="1"/><path d="M5.5 9h3M5.5 12h3M15.5 9h3M15.5 12h3M15.5 15h3"/>',
    'select': '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="m8 12 3 3 5-6"/>',
    'eye': '<path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    'duplicate': '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3M14.5 12v5M12 14.5h5"/>',
    'extract': '<path d="M3 5h10v4M3 5v14h10v-4"/><path d="M13 12h8m-3-3 3 3-3 3"/>',
    'move': '<path d="M2 6a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z"/><path d="M8 13h8m-3-3 3 3-3 3"/>',
    'split': '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M12 4v16"/>',
    'path': '<path d="M4 7h4l3 5-3 5H4M11 7h9M11 17h9"/>',
    'phone': '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    'mail': '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    'bluetooth': '<path d="m7 7 10 10-5 5V2l5 5L7 17"/>',
    'wifi': '<path d="M2 9a15 15 0 0 1 20 0M5.5 12.5a10 10 0 0 1 13 0M9 16a5 5 0 0 1 6 0"/><circle cx="12" cy="19.5" r="1"/>',
    'qr': '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><path d="M14 14h3v3h-3zM19 14h2M14 19h2M19 19h2v2"/>',
    'stop': '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    'settings': '<path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z"/><path d="m19 12 2-1-1-3-2 .2a7 7 0 0 0-1.5-1.5L17 4l-3-1-1 2a7 7 0 0 0-2 0L10 3 7 4l.5 2.7A7 7 0 0 0 6 8.2L3.8 8 3 11l2 1a7 7 0 0 0 0 2l-2 1 1 3 2.2-.2a7 7 0 0 0 1.5 1.5L7 21l3 1 1-2a7 7 0 0 0 2 0l1 2 3-1-.5-2.7a7 7 0 0 0 1.5-1.5l2.2.2 1-3-2-1a7 7 0 0 0 0-2Z"/>',
    'devices': '<rect x="3" y="4" width="14" height="11" rx="1"/><path d="M7 20h6M10 15v5M19 8v9M17 10h4"/>',
    'paint': '<path d="M12 3a9 9 0 1 0 0 18h2a2 2 0 0 0 0-4h-1a2 2 0 0 1 0-4h3a5 5 0 0 0 5-5 9 9 0 0 0-9-5Z"/><circle cx="7.5" cy="10" r=".8"/><circle cx="10" cy="7" r=".8"/><circle cx="14" cy="7" r=".8"/>',
    'apps': '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    'user': '<circle cx="12" cy="8" r="3.5"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'accessibility': '<circle cx="12" cy="4" r="2"/><path d="M5 8h14M12 8v13M8 21l4-7 4 7"/>',
}

SPECIAL = {
    'home': '<path d="m3 11 9-8 9 8" stroke="#c97b36" stroke-width="2" fill="none"/><path d="M5 10v11h14V10l-7-6Z" fill="#e6ab68"/><path d="M10 14h4v7h-4Z" fill="#fff3dc"/>',
    'pc': '<rect x="2" y="3" width="20" height="14" rx="1.5" fill="#718695"/><rect x="3.5" y="4.5" width="17" height="11" fill="#64c4ef"/><path d="M10 17h4v3h4v1H6v-1h4Z" fill="#8398a5"/>',
    'gallery': '<rect x="3" y="3" width="16" height="18" rx="2" fill="#c375de"/><rect x="6" y="5" width="16" height="16" rx="2" fill="#9464d5"/><path d="m8 18 4-5 3 3 3-4 3 6" fill="#efdbff"/><circle cx="11" cy="9" r="2" fill="#ffdd70"/>',
    'desktop': '<rect x="2" y="3" width="20" height="15" rx="1.5" fill="#42b6e8"/><path d="M3 15 21 5v12H3Z" fill="#188cce"/><path d="M10 18h4v3H7v-1h3Z" fill="#87939f"/>',
    'downloads': '<path d="M4 15v6h16v-6" stroke="#1a9c91" stroke-width="2" fill="none"/><path d="M9 2h6v10h5l-8 8-8-8h5Z" fill="#28ad9e"/>',
    'documents': '<path d="M5 2h10l5 5v15H5Z" fill="#d5eaf8"/><path d="M15 2v6h5" fill="#8bc2e7"/><path d="M8 11h9M8 14h9M8 17h7" stroke="#4587b4" stroke-width="1.3"/>',
    'pictures': '<rect x="2" y="3" width="20" height="18" rx="2" fill="#46a8df"/><path d="m4 18 5-7 5 5 3-4 4 6" fill="#bceafb"/><circle cx="17" cy="8" r="2.5" fill="#f8e180"/>',
    'music': '<path d="M9 6v12M9 6l11-3v12M9 9l11-3" stroke="#dd7999" stroke-width="2.5" fill="none"/><ellipse cx="6" cy="18" rx="4" ry="3" fill="#de7498"/><ellipse cx="17" cy="15" rx="4" ry="3" fill="#de7498"/>',
    'videos': '<rect x="3" y="3" width="18" height="18" rx="2" fill="#9e67ce"/><path d="m10 8 7 4-7 4Z" fill="white"/><path d="M5 5h2M5 9h2M5 13h2M5 17h2M18 5h1M18 18h1" stroke="#dfc7f3"/>',
    'zip': '<path d="M2 5a2 2 0 0 1 2-2h5l3 3h8a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z" fill="#dc9d31"/><path d="M2 8h20v11a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z" fill="#ffd86b"/><path d="M11 8h2v2h-2zM13 10h2v2h-2zM11 12h2v2h-2zM13 14h2v2h-2zM11 16h2v2h-2zM13 18h2v2h-2z" fill="#8a6520"/>',
    'terminal': '<rect x="2" y="4" width="20" height="16" rx="2" fill="#2b2b2b"/><rect x="2" y="4" width="20" height="4" rx="2" fill="#444"/><path d="m6 11 3 3-3 3" stroke="#62d26f" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M11 17h6" stroke="#f2f2f2" stroke-width="1.8" stroke-linecap="round"/>',
    'shield': '<path d="M12 2 4 5v6c0 5 3.5 9 8 11 4.5-2 8-6 8-11V5Z" fill="#3f8ed8"/><path d="M12 2v20c4.5-2 8-6 8-11V5Z" fill="#2266b8"/><path d="m8.5 12 2.5 2.5 4.5-5" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    'favorite': '<path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.3-5.7-3-5.7 3 1.1-6.3-4.6-4.5 6.4-.9Z" fill="#ffc83d" stroke="#e0a300" stroke-width=".8" stroke-linejoin="round"/>',
    'drive': '<path d="M5 5h14l4 12v4H1v-4Z" fill="#bfc8cf"/><path d="M5 6h14l3 10H2Z" fill="#e4e9ed"/><rect x="2" y="17" width="20" height="3" rx="1" fill="#8395a5"/><circle cx="19" cy="18.5" r=".8" fill="#66edaf"/>',
}

# Dedicated settings artwork: independent of the file-manager's .ico overrides.
# Coordinates remain on a 24 px grid for crisp 20/24/32 px Qt rendering.
SETTINGS_ART = {
    'home': '<path d="M4 10 12 3l8 7v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1Z" fill="url(#accent)"/><path d="m2 11 10-9 10 9" fill="none" stroke="#67bdff" stroke-width="2" stroke-linejoin="round"/><rect x="10" y="14" width="4" height="7" rx="1" fill="#def3ff"/>',
    'system': '<rect x="2" y="3" width="20" height="14" rx="2" fill="#7b8796"/><rect x="3.5" y="4.5" width="17" height="11" rx=".7" fill="url(#accent)"/><path d="m4 14 16-9v9Z" fill="#68d8ff" opacity=".55"/><path d="M10 17h4v3h4v1H6v-1h4Z" fill="#8794a3"/>',
    'bluetooth': '<rect x="5" y="1" width="14" height="22" rx="6" fill="url(#accent)"/><path d="m9 7 7 10-4 3V4l4 3-7 10" stroke="white" stroke-width="1.4" fill="none" stroke-linejoin="round"/>',
    'network': '<path d="M2 8a16 16 0 0 1 20 0M5.5 12a10.5 10.5 0 0 1 13 0M9 16a5 5 0 0 1 6 0" stroke="url(#accent)" stroke-width="2.7" fill="none" stroke-linecap="round"/><circle cx="12" cy="20" r="1.6" fill="#1986de"/>',
    'personalization': '<rect x="2" y="3" width="19" height="15" rx="2" fill="url(#accent)"/><path d="M4 15 10 8l5 6 4-4v6H4Z" fill="#e1d1ff"/><path d="m18 10 3 2-7 9-4 1 1-4Z" fill="#faaf43"/><path d="m11 18 3 3-4 1Z" fill="#70503b"/>',
    'apps': '<rect x="2" y="2" width="9" height="9" rx="2" fill="#31a5f2"/><rect x="13" y="2" width="9" height="9" rx="2" fill="#44c3e3"/><rect x="2" y="13" width="9" height="9" rx="2" fill="#8180e8"/><rect x="13" y="13" width="9" height="9" rx="2" fill="#9e61d9"/>',
    'accounts': '<circle cx="12" cy="7" r="4.5" fill="#ffbf79"/><path d="M3 21v-2a9 8 0 0 1 18 0v2Z" fill="url(#accent)"/>',
    'time': '<circle cx="10" cy="11" r="9" fill="url(#accent)"/><circle cx="10" cy="11" r="6.7" fill="#f3fbff"/><path d="M10 6v5l3 2" stroke="#426078" stroke-width="1.5" fill="none" stroke-linecap="round"/><rect x="13" y="14" width="10" height="9" rx="2" fill="#7966d5"/><path d="M15 17h6m-4-1v1c0 2 1 3 3 4m-1-4c0 2-1 3-3 4" fill="none" stroke="white" stroke-width=".9"/>',
    'gaming': '<path d="M7 6h10c3 0 4 3 5 10 .6 4-2 5-4 2l-2-2H8l-2 2c-2 3-4.6 2-4-2C3 9 4 6 7 6Z" fill="url(#accent)"/><path d="M7 9v5m-2.5-2.5h5" fill="none" stroke="#f3fbff" stroke-width="1.5" stroke-linecap="round"/><circle cx="17" cy="10" r="1.2" fill="#62dcaa"/><circle cx="19" cy="13" r="1.2" fill="#f5b4d3"/>',
    'accessibility': '<circle cx="12" cy="12" r="10.5" fill="url(#accent)"/><circle cx="12" cy="6" r="2" fill="white"/><path d="M6 10h12m-6 0v5m-3 5 3-5 3 5" fill="none" stroke="white" stroke-width="1.7" stroke-linecap="round"/>',
    'privacy': '<path d="M12 2 3 5v6c0 5 4 9 9 11 5-2 9-6 9-11V5Z" fill="url(#accent)"/><path d="M12 3v17c4-2 7-5 7-9V6Z" fill="#65c5ff" opacity=".6"/><path d="m7 12 3 3 7-7" stroke="white" stroke-width="1.6" fill="none" stroke-linecap="round"/>',
    'update': '<path d="M20 9a8 8 0 0 0-14-3L3 9m0-5v5h5M4 15a8 8 0 0 0 14 3l3-3m0 5v-5h-5" stroke="url(#accent)" stroke-width="2.3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    'storage': '<rect x="3" y="3" width="18" height="18" rx="3" fill="#9aa7b7"/><rect x="4.5" y="4.5" width="15" height="11" rx="2" fill="#dce8f3"/><path d="M6 18h8" stroke="#51637b" stroke-width="1.5" stroke-linecap="round"/><circle cx="18" cy="18" r="1.2" fill="#31cf8b"/>',
    'theme': '<circle cx="12" cy="12" r="10" fill="#f9ca5d"/><path d="M12 2a10 10 0 0 1 0 20Z" fill="#6874ce"/><circle cx="12" cy="12" r="5" fill="#fff0b6"/><path d="M12 7a5 5 0 0 1 0 10Z" fill="#b4b8f5"/>',
    'colors': '<circle cx="9" cy="8" r="6" fill="#f48b96"/><circle cx="16" cy="11" r="6" fill="#60b8ef" opacity=".92"/><circle cx="9" cy="17" r="6" fill="#b684e2" opacity=".92"/>',
    'visibility': '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12Z" fill="url(#accent)"/><circle cx="12" cy="12" r="4.5" fill="#dcf4ff"/><circle cx="12" cy="12" r="2.2" fill="#28659d"/>',
    'startup': '<path d="M12 2v10M6 5a9 9 0 1 0 12 0" fill="none" stroke="url(#accent)" stroke-width="2.5" stroke-linecap="round"/>',
    'capture': '<rect x="2" y="4" width="20" height="16" rx="3" fill="url(#accent)"/><path d="M5 9V7h3m8 0h3v2M5 15v2h3m8 0h3v-2" stroke="white" stroke-width="1.3" fill="none"/><circle cx="12" cy="12" r="3" fill="#fb9b9b"/>',
    'animations': '<rect x="2" y="4" width="12" height="14" rx="2" fill="#b6b6f0"/><rect x="6" y="6" width="12" height="14" rx="2" fill="#9290e8"/><rect x="10" y="8" width="12" height="14" rx="2" fill="url(#accent)"/><path d="m14 12 5 3-5 3Z" fill="white"/>',
    'defaults': '<rect x="2" y="3" width="17" height="17" rx="3" fill="url(#accent)"/><path d="M3 8h15" stroke="#c6e5ff" stroke-width="1.2"/><circle cx="17" cy="17" r="6" fill="#3faa83"/><path d="m14 17 2 2 4-4" stroke="white" stroke-width="1.5" fill="none"/>',
    'features': '<path d="m12 2 10 5v11l-10 5-10-5V7Z" fill="url(#accent)"/><path d="m2 7 10 5 10-5M12 12v11M7 4.5l10 5" stroke="#dfd4ff" stroke-width="1.2" fill="none"/>',
}
SETTINGS_ART.update({
    'sound': '<path d="M3 9h4l5-4v14l-5-4H3Z" fill="url(#accent)"/><path d="M16 8a6 6 0 0 1 0 8m2-11a10 10 0 0 1 0 14" fill="none" stroke="#7acaff" stroke-width="2" stroke-linecap="round"/>',
    'chip': '<rect x="5" y="5" width="14" height="14" rx="2" fill="url(#accent)"/><rect x="9" y="9" width="6" height="6" rx="1" fill="#d8f4ff"/><path d="M8 2v3m4-3v3m4-3v3M8 19v3m4-3v3m4-3v3M2 8h3m-3 4h3m-3 4h3m14-8h3m-3 4h3m-3 4h3" stroke="#73bdf0" stroke-width="1.5"/>',
    'display': '<rect x="2" y="3" width="20" height="15" rx="2" fill="#7d8998"/><rect x="3.5" y="4.5" width="17" height="11" rx="1" fill="url(#accent)"/><path d="m4 14 16-9v9Z" fill="#8ee4ff" opacity=".45"/><path d="M10 18v3m4-3v3M7 21h10" stroke="#9ba9ba" stroke-width="1.5"/>',
    'power': '<rect x="2" y="6" width="19" height="12" rx="2" fill="#637c92"/><rect x="4" y="8" width="14" height="8" rx="1" fill="url(#accent)"/><rect x="21" y="10" width="2" height="4" rx="1" fill="#7fd5ff"/><path d="m12 7-3 5h3l-1 5 5-7h-3l2-3Z" fill="#fff7c4"/>',
    'notifications': '<path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5Z" fill="url(#accent)"/><circle cx="12" cy="20" r="2" fill="#73b9ed"/><path d="M3 8c.2-2 1-3 2-4m14 0c1 1 1.8 2 2 4" fill="none" stroke="#83cfff" stroke-width="1.4"/>',
    'keyboard': '<rect x="1.5" y="6" width="21" height="13" rx="2" fill="url(#accent)"/><path d="M4 9h2m2 0h2m2 0h2m2 0h2M4 12h2m2 0h2m2 0h2m2 0h2M5 16h14" stroke="#e7f6ff" stroke-width="1.3" stroke-linecap="round"/>',
    'mouse': '<rect x="6" y="2" width="12" height="20" rx="6" fill="url(#accent)"/><path d="M12 3v9M6 12h12" stroke="#ddf4ff" stroke-width="1.5"/><circle cx="12" cy="8" r="1" fill="white"/>',
    'touch': '<rect x="2" y="3" width="20" height="17" rx="2" fill="url(#accent)"/><path d="M5 15h4M12 22v-8a2 2 0 0 1 4 0v3l2-1 3 3v3" stroke="#e9f6ff" stroke-width="1.7" fill="none" stroke-linecap="round"/>',
    'lock': '<rect x="5" y="10" width="14" height="12" rx="2" fill="url(#accent)"/><path d="M8 10V7a4 4 0 0 1 8 0v3" fill="none" stroke="#8dcbf3" stroke-width="2"/><circle cx="12" cy="16" r="1.5" fill="#effaff"/>',
    'search': '<circle cx="10" cy="10" r="7" fill="url(#accent)"/><circle cx="10" cy="10" r="4" fill="#e5f6ff"/><path d="m15 15 6 6" stroke="#78b7e7" stroke-width="3" stroke-linecap="round"/>',
    'firewall': '<path d="M12 2 3 5v6c0 5 4 9 9 11 5-2 9-6 9-11V5Z" fill="url(#accent)"/><path d="M6 10h12M6 14h12M10 6v4m5 0v4m-5 0v4" stroke="#e4f3ff" stroke-width="1.2"/>',
    'language': '<circle cx="12" cy="12" r="10" fill="url(#accent)"/><path d="M2 12h20M12 2c-4 4-4 16 0 20m0-20c4 4 4 16 0 20" stroke="#e6f7ff" stroke-width="1.3" fill="none"/><path d="M4 17h16" stroke="#e6f7ff" stroke-width="1.1"/>',
    'night': '<path d="M17 3A9 9 0 1 0 21 18 9 9 0 0 1 17 3Z" fill="url(#accent)"/><path d="m17 5 .5 1.5L19 7l-1.5.5L17 9l-.5-1.5L15 7l1.5-.5Z" fill="#ffed9c"/>',
    'wallpaper': '<rect x="2" y="3" width="20" height="18" rx="2" fill="url(#accent)"/><circle cx="17" cy="8" r="2.3" fill="#ffe18b"/><path d="m3 18 6-7 4 4 3-3 5 6Z" fill="#b2e9d8"/><path d="M3 18h18v2H3Z" fill="#3b9d92"/>',
    'usb': '<path d="M12 21V5m0 2L8 3m4 6 5-5m-5 9-5 4" stroke="url(#accent)" stroke-width="2.4" fill="none" stroke-linecap="round"/><path d="M10 2h4v4h-4Z" fill="#7ac8f2"/><circle cx="18" cy="3" r="2" fill="#7ac8f2"/><path d="M5 16h4v4H5Z" fill="#7ac8f2"/>',
    'cloud': '<path d="M6 19a5 5 0 0 1-.3-10A7 7 0 0 1 19 11a4 4 0 0 1-1 8Z" fill="url(#accent)"/><path d="M9 14h6m-3-3v6" stroke="#eaf8ff" stroke-width="1.8" stroke-linecap="round"/>',
    'sensor': '<circle cx="12" cy="12" r="10" fill="url(#accent)"/><path d="M5 15a7 7 0 0 1 14 0M12 14l4-5" fill="none" stroke="#e5f7ff" stroke-width="1.6" stroke-linecap="round"/><circle cx="12" cy="14" r="1.5" fill="white"/>',
    'font': '<path d="M4 20 11 3h2l7 17h-3l-2-5H9l-2 5Zm6-8h4l-2-5Z" fill="url(#accent)"/><path d="M18 5h4" stroke="#8acaf1" stroke-width="1.5"/>',
    'window': '<rect x="2" y="3" width="20" height="18" rx="2" fill="url(#accent)"/><path d="M2 8h20M7 8v13" stroke="#d8f3ff" stroke-width="1.3"/><circle cx="5" cy="5.5" r=".7" fill="white"/>',
})
SETTINGS_ART['appicon'] = ('<rect x="1" y="1" width="22" height="22" rx="5" fill="url(#accent)"/>'
                           '<g transform="translate(3 3) scale(.75)" fill="none" stroke="white" '
                           'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                           + PATHS['settings'] + '</g>')
SETTINGS_TONES = {
    'personalization': ('#b08af4', '#7753ba'), 'gaming': ('#858d9d', '#4a5367'),
    'accessibility': ('#ac8df3', '#7759cb'), 'accounts': ('#63b5f3', '#3879ce'),
    'update': ('#5dc6a1', '#218b64'), 'startup': ('#62b9f6', '#3176d2'),
    'animations': ('#9b83ed', '#6751b9'), 'features': ('#b8a0ed', '#8261c3'),
}


def svg(name, color='#454545'):
    if name.startswith('settings-') and name[9:] in SETTINGS_ART:
        key = name[9:]
        light, dark = SETTINGS_TONES.get(key, ('#60b9fb', '#246ccb'))
        body = (f'<defs><linearGradient id="accent" x1="0" y1="0" x2="0.8" y2="1">'
                f'<stop stop-color="{light}"/><stop offset="1" stop-color="{dark}"/>'
                f'</linearGradient></defs>{SETTINGS_ART[key]}')
    elif name == 'folder' or name.startswith('folder-'):
        badge = name.removeprefix('folder-') if name != 'folder' else ''
        body = ('<defs><linearGradient id="f" x2="0" y2="1"><stop stop-color="#ffdb75"/><stop offset="1" stop-color="#f7bf44"/></linearGradient></defs>'
                '<path d="M2 5a2 2 0 0 1 2-2h5l3 3h8a2 2 0 0 1 2 2v11H2Z" fill="#dc9d31"/>'
                '<path d="M2 8h20v11a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z" fill="url(#f)"/>'
                '<path d="M3 8h18" stroke="#ffe6a4" stroke-width=".7"/>')
        if badge in SPECIAL:
            body += f'<g transform="translate(10 10) scale(.55)">{SPECIAL[badge]}</g>'
    elif name in SPECIAL:
        body = SPECIAL[name]
    elif name == 'file':
        body = '<path d="M5 2h10l5 5v15H5Z" fill="#fafafa" stroke="#aab2bb"/><path d="M15 2v6h5M8 12h9M8 15h9M8 18h6" fill="none" stroke="#adb6bf"/>'
    else:
        body = f'<g fill="none" stroke="{color}" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round">{PATHS.get(name, PATHS["info"])}</g>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">{body}</svg>'


class GlyphIcon(QIconEngine):
    """Render command icons with the current palette, including existing buttons.

    Rendered pixmaps are cached per (name, colour, size, device pixel ratio); the SVG is
    parsed once per combination instead of on every paint event.
    """
    _cache = {}

    def __init__(self, name, color):
        super().__init__()
        self.name, self.color = name, color

    def clone(self):
        return GlyphIcon(self.name, self.color)

    def current_color(self, mode):
        palette = QGuiApplication.palette()
        if mode == QIcon.Disabled:
            return palette.color(QPalette.Disabled, QPalette.Text).name()
        if palette.color(QPalette.Window).lightness() < 128:
            role = QPalette.Text if self.color == '#454545' else QPalette.PlaceholderText
            return palette.color(role).name()
        return self.color

    def render(self, width, height, ratio, color):
        key = (self.name, color, width, height, ratio)
        pixmap = self._cache.get(key)
        if pixmap is None:
            pixmap = QPixmap(int(width * ratio), int(height * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            QSvgRenderer(QByteArray(svg(self.name, color).encode())).render(painter, QRectF(0, 0, width, height))
            painter.end()
            if len(self._cache) > 2048:
                self._cache.clear()
            self._cache[key] = pixmap
        return pixmap

    def paint(self, painter, rect, mode, state):
        ratio = painter.device().devicePixelRatio() if painter.device() else 1.0
        pixmap = self.render(rect.width(), rect.height(), ratio, self.current_color(mode))
        painter.drawPixmap(rect.topLeft(), pixmap)

    def pixmap(self, size, mode, state):
        return self.render(size.width(), size.height(), 1.0, self.current_color(mode))


@lru_cache(maxsize=192)
def icon(name, color='#454545'):
    native = Path(__file__).resolve().parent / 'assets' / f'{name.removeprefix("folder-")}.ico'
    if native.is_file():
        return QIcon(str(native))
    if name in PATHS or (name.startswith('settings-') and name[9:] in SETTINGS_ART):
        return QIcon(GlyphIcon(name, color))
    result = QIcon()
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode()))
    for size in (16, 20, 24, 32, 48, 64, 96, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result
