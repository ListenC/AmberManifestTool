"""主题：跟随 Windows 明暗，给出整套调色板 + 两套 QSS 配色令牌。

为什么必须在代码里设 QPalette：QSS 只覆盖它写到的控件，没写到的（QTreeView/QTreeWidget 视口、
菜单、提示框、原生子控件）会落回系统调色板。Windows 11 暗色模式下系统 `Window` 是 #1e1e1e，
于是"半套 QSS 浅色 + 半套系统暗色"就出现明暗混杂（依赖树的视口正是这种）。
这里按 `colorScheme()` 显式固定整套调色板，QSS 里的颜色令牌由同一份字典提供，两边不会再打架。

style.qss 里所有颜色都写成被 at 号包住的大写令牌，改配色只需改本文件的 TOKENS。
"""

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette


class _Notifier(QObject):
    """配色切换广播：静态着色的面板（概览页那些 item）收到后自行重建。

    表格模型走 data() 取色、样式表会自己重绘，都不需要这个信号。
    """

    changed = Signal(str)


notifier = _Notifier()

# 令牌 -> 取值。浅色沿用原 style.qss 的数值；深色向 Windows 11 暗色调色板靠拢（Window #1e1e1e）。
TOKENS = {
    'light': {
        'FG': '#1f2933',
        'BG': '#f4f5f7',
        'SURFACE': '#ffffff',
        'BORDER': '#d9dde3',
        'BORDER_SOFT': '#dfe3e9',
        'SEPARATOR': '#dde2e9',
        'BTN_HOVER_BG': '#eef2f7',
        'BTN_HOVER_BORDER': '#cdd5df',
        'BTN_PRESSED_BG': '#e2e8f0',
        'DISABLED_FG': '#9aa5b1',
        'TAB_BG': '#e9edf2',
        'TAB_SEL_BG': '#ffffff',
        'TAB_HOVER_BG': '#f2f5f9',
        'ACCENT': '#1a56db',
        'TABLE_BG': '#ffffff',
        'TABLE_ALT': '#f7f9fc',
        'SEL_BG': '#cfe0fb',
        'SEL_FG': '#10233f',
        'HEADER_BG': '#eef1f5',
        'GRID': '#d8d8d8',
        'FOCUS': '#4c7fe0',
        'BTN_BORDER': '#c7ced8',
        'GROUP_TITLE': '#334e68',
        'PUSH_HOVER_BG': '#f3f6fa',
        'PUSH_HOVER_BORDER': '#a9b4c2',
        'PUSH_PRESSED_BG': '#e6ebf2',
        'PUSH_DISABLED_BORDER': '#e2e6ec',
        'PROGRESS_BG': '#f0f2f5',
        'CHUNK': '#4c7fe0',
        'SCROLL_HANDLE': '#c2cad6',
        'SCROLL_HANDLE_HOVER': '#a8b3c2',
        'BANNER_BG': '#fff8e1',
        'BANNER_BORDER': '#ffe0a3',
        'BANNER_FG': '#6b4e00',
        'HINT_FG': '#627d98',
        'OK_FG': '#1b7f3b',
        'BAD_FG': '#c0392b',
        'TOOLTIP_BG': '#ffffff',
        'TOOLTIP_BORDER': '#d9dde3',
    },
    'dark': {
        'FG': '#e4e6eb',
        'BG': '#1e1e1e',
        'SURFACE': '#2b2b2b',
        'BORDER': '#3c3f44',
        'BORDER_SOFT': '#35383d',
        'SEPARATOR': '#3c3f44',
        'BTN_HOVER_BG': '#383b40',
        'BTN_HOVER_BORDER': '#4a4e55',
        'BTN_PRESSED_BG': '#43474d',
        'DISABLED_FG': '#6f747c',
        'TAB_BG': '#2b2b2b',
        'TAB_SEL_BG': '#1e1e1e',
        'TAB_HOVER_BG': '#33373c',
        'ACCENT': '#6ea8fe',
        'TABLE_BG': '#232323',
        'TABLE_ALT': '#2a2a2a',
        'SEL_BG': '#2f5d9e',
        'SEL_FG': '#ffffff',
        'HEADER_BG': '#2f2f2f',
        'GRID': '#34373c',
        'FOCUS': '#6ea8fe',
        'BTN_BORDER': '#4a4e55',
        'GROUP_TITLE': '#9db8d6',
        'PUSH_HOVER_BG': '#35383d',
        'PUSH_HOVER_BORDER': '#5a5f67',
        'PUSH_PRESSED_BG': '#3f434a',
        'PUSH_DISABLED_BORDER': '#3a3d42',
        'PROGRESS_BG': '#2b2b2b',
        'CHUNK': '#4c7fe0',
        'SCROLL_HANDLE': '#4a4e55',
        'SCROLL_HANDLE_HOVER': '#5d6269',
        'BANNER_BG': '#3a3320',
        'BANNER_BORDER': '#5c4d24',
        'BANNER_FG': '#e8c87a',
        'HINT_FG': '#9aa5b1',
        'OK_FG': '#4ec97a',
        'BAD_FG': '#ff6f61',
        'TOOLTIP_BG': '#2f2f2f',
        'TOOLTIP_BORDER': '#4a4e55',
    },
}

# 模型层直接画在单元格上的底色 / 文字（QSS 管不到，必须跟着主题走）。
# 深色下若还用浅色底纹，配上浅色文字就是"浅字浅底"，整张表会糊成一片。
CELL_COLORS = {
    'light': {
        'KEY_DUP_EVEN': '#e8f0fb',    # 键表：同键组（>1 条）的奇偶底色
        'KEY_DUP_ODD': '#dce8f7',
        'KEY_ROW_EVEN': '#f3f4f6',    # 键表：唯一键的奇偶底色
        'KEY_ROW_ODD': '#ffffff',
        'CHECK_OK': '#e6f4ea',        # 自校验：通过 / 不通过
        'CHECK_BAD': '#fce8e6',
        'INK': '#1f2933',             # 上述底纹上的文字色
    },
    'dark': {
        'KEY_DUP_EVEN': '#2b3f57',
        'KEY_DUP_ODD': '#24354a',
        'KEY_ROW_EVEN': '#262626',
        'KEY_ROW_ODD': '#212121',
        'CHECK_OK': '#1e3326',
        'CHECK_BAD': '#3a2220',
        'INK': '#e4e6eb',
    },
}

# 段布局条与段明细首列：每段一套 (底色, 描边, 文字)。
# 浅色沿用原来的亮色粉彩 + 深色文字；深色换成同色系的暗调 + 浅色文字，整页才统一。
SEGMENT_COLORS = {
    'light': (('#8c9eff', '#5c6bc0', '#12232e'),    # 表头
              ('#80cbc4', '#00897b', '#12232e'),    # 键表
              ('#a5d6a7', '#43a047', '#12232e'),    # 偏移表
              ('#ffcc80', '#ef6c00', '#12232e'),    # 记录区
              ('#f48fb1', '#c2185b', '#12232e')),   # 前缀池
    'dark': (('#3d4a76', '#6b7cb8', '#e9edf8'),
             ('#2d5753', '#5f9e97', '#e2f0ee'),
             ('#345c39', '#68a56f', '#e6f2e7'),
             ('#6b4a20', '#c08a3c', '#f6e7cd'),
             ('#66334f', '#b8709a', '#f7e3ee')),
}

_active_scheme = 'light'


def style_path():
    """style.qss 与 theme.py 同目录；冻结后 __file__ 已在 _internal/amber_gui/，与 --add-data 目标一致。"""
    return Path(__file__).resolve().parent / 'style.qss'


def current_scheme():
    """'dark' 或 'light'，取自 Windows 应用主题（不是我们自己的偏好）。"""
    dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    return 'dark' if dark else 'light'


def build_palette(scheme):
    """整套调色板，覆盖 QSS 没写到的控件（视口、菜单、提示框、禁用态文字）。"""
    c = TOKENS[scheme]
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c['BG']))
    pal.setColor(QPalette.WindowText, QColor(c['FG']))
    pal.setColor(QPalette.Base, QColor(c['TABLE_BG']))
    pal.setColor(QPalette.AlternateBase, QColor(c['TABLE_ALT']))
    pal.setColor(QPalette.Text, QColor(c['FG']))
    pal.setColor(QPalette.Button, QColor(c['SURFACE']))
    pal.setColor(QPalette.ButtonText, QColor(c['FG']))
    pal.setColor(QPalette.Highlight, QColor(c['SEL_BG']))
    pal.setColor(QPalette.HighlightedText, QColor(c['SEL_FG']))
    pal.setColor(QPalette.Link, QColor(c['FOCUS']))
    pal.setColor(QPalette.ToolTipBase, QColor(c['TOOLTIP_BG']))
    pal.setColor(QPalette.ToolTipText, QColor(c['FG']))
    pal.setColor(QPalette.PlaceholderText, QColor(c['HINT_FG']))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c['DISABLED_FG']))
    return pal


def read_stylesheet(scheme):
    path = style_path()
    if not path.is_file():
        return ''
    text = path.read_text(encoding='utf-8')
    for key, value in TOKENS[scheme].items():
        text = text.replace(f'@{key}@', value)
    return text


def activate(app, scheme=None):
    """按 scheme（缺省取系统明暗）设调色板与样式表，并记下当前配色供模型层取色。"""
    global _active_scheme
    _active_scheme = scheme or current_scheme()
    app.setPalette(build_palette(_active_scheme))
    app.setStyleSheet(read_stylesheet(_active_scheme))
    app.setProperty('amberTheme', _active_scheme)
    notifier.changed.emit(_active_scheme)
    return _active_scheme


def install(app):
    """应用主题并订阅系统明暗变化（用户在设置里切主题时界面即时跟着变）。返回当前配色名。"""
    scheme = activate(app)
    app.styleHints().colorSchemeChanged.connect(lambda *_args: activate(app))
    return scheme


def cells():
    """模型层画在单元格上的底色 / 文字色（不随 QSS 走）。"""
    return CELL_COLORS[_active_scheme]


def segments():
    """段布局条与段明细首列的每段 (底色, 描边, 文字)。"""
    return SEGMENT_COLORS[_active_scheme]


def tok(key):
    """按当前明暗主题取一个 TOKENS 值（如 'FG'、'ACCENT'、'SURFACE'）。供自绘控件用。"""
    return TOKENS[_active_scheme][key]


if __name__ == '__main__':        # 直接运行本文件可看令牌是否齐备（QSS 里没有漏替换的 @X@）
    import re
    for name in TOKENS:
        text = read_stylesheet(name)
        left = sorted(set(re.findall(r'@[A-Z_]+@', text)))
        print(f'{name:6} 未替换令牌 {left or "无"}')
    for name, table in (('TOKENS', TOKENS), ('CELL_COLORS', CELL_COLORS)):
        diff = {k for k in table['light']} ^ {k for k in table['dark']}
        print(f'{name:12} 两套键差集：', diff or '无')
    print('SEGMENT_COLORS 段数：', len(SEGMENT_COLORS['light']), len(SEGMENT_COLORS['dark']))
    print('调色板抽样：', build_palette('light').color(QPalette.Base).name(),
          build_palette('dark').color(QPalette.Base).name())
    sys.exit(0)