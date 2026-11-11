"""Shared color palette for the file manager and CachyOS settings window."""
from pathlib import Path
from string import Template

from PySide6.QtGui import QColor, QPalette


THEMES = {
    'light': dict(text='#202020', window='#f3f3f3', surface='#ffffff', panel='#fafafa',
                  outline='#bcbcbc', border='#e5e5e5', hover='#e7e7e7', pressed='#dcdcdc',
                  disabled='#aaaaaa', focus='#8b8b8b', accent='#0067c0',
                  selection='#e5f3ff', selection_border='#cce8ff', muted='#737373',
                  header='#606060', status='#555555', separator='#dedede', menu='#f9f9f9',
                  scroll='#c2c2c2', scroll_hover='#999999', progress='#e6e6e6', fill='#0078d4'),
    'dark': dict(text='#f2f2f2', window='#202020', surface='#191919', panel='#252525',
                 outline='#484848', border='#383838', hover='#333333', pressed='#404040',
                 disabled='#787878', focus='#a0a0a0', accent='#60cdff',
                 selection='#243b4b', selection_border='#41677e', muted='#b0b0b0',
                 header='#c5c5c5', status='#bfbfbf', separator='#383838', menu='#282828',
                 scroll='#666666', scroll_hover='#929292', progress='#404040', fill='#60cdff'),
}
THEMES['oled'] = {
    **THEMES['dark'], 'window': '#000000', 'surface': '#000000', 'panel': '#000000',
    'menu': '#000000', 'outline': '#303030', 'border': '#242424', 'separator': '#242424',
    'hover': '#171717', 'pressed': '#282828', 'selection': '#102633',
    'selection_border': '#30536a', 'progress': '#242424',
}


def apply_palette(app, name, stylesheet=None):
    """Apply the same QSS and palette to either standalone Qt application."""
    name = name if name in THEMES else 'light'
    colors = THEMES[name]
    palette = QPalette()
    for role, key in [(QPalette.Window, 'window'), (QPalette.Base, 'surface'),
                      (QPalette.AlternateBase, 'panel'), (QPalette.Button, 'panel'),
                      (QPalette.ToolTipBase, 'menu'), (QPalette.ToolTipText, 'text'),
                      (QPalette.WindowText, 'text'), (QPalette.Text, 'text'),
                      (QPalette.ButtonText, 'text'), (QPalette.PlaceholderText, 'muted'),
                      (QPalette.Highlight, 'selection'), (QPalette.HighlightedText, 'text'),
                      (QPalette.Link, 'accent'), (QPalette.Accent, 'accent'),
                      (QPalette.Light, 'outline'), (QPalette.Mid, 'border'),
                      (QPalette.Dark, 'border'), (QPalette.Shadow, 'window')]:
        palette.setColor(role, QColor(colors[key]))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(colors['disabled']))
    app.setProperty('theme', name)
    app.setPalette(palette)
    stylesheet = Path(stylesheet or Path(__file__).resolve().parent / 'style.qss')
    app.setStyleSheet(Template(stylesheet.read_text()).substitute(colors))
    return name
