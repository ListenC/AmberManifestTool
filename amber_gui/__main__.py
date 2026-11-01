"""入口：QApplication、主题（见 theme.py）、全局异常兜底、命令行/拖拽传入的文件。

打包后（--windowed）控制台不可见，因此这里必须装 sys.excepthook，
把异常写进崩溃日志并弹对话框，否则故障会表现为"点了没反应"。
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

# 允许 `python amber_gui\__main__.py` 直接运行（非 -m 方式时 sys.path[0] 是 amber_gui 目录）
if not getattr(sys, 'frozen', False) and __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amber_gui import APP_NAME, __version__, theme     # noqa: E402 —— 必须先修正 sys.path
from amber_gui.main_window import MainWindow           # noqa: E402


def crash_log_path():
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path.cwd()
    return base / 'AmberManifestTool_crash.log'


def install_excepthook():
    def hook(exc_type, exc_value, exc_tb):
        text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        path = crash_log_path()
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(f'\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n{text}')
        except OSError:
            path = '(写入失败)'
        app = QApplication.instance()
        if app is not None:
            QMessageBox.critical(None, APP_NAME,
                                 f'发生未处理的异常：\n\n{exc_value}\n\n详细信息已写入：\n{path}')
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    # QSettings 存自定义扰动流（游戏）用；组织名不设会落到不确定的注册表位置
    app.setOrganizationName('AmberManifestTool')

    theme.install(app)

    install_excepthook()

    window = MainWindow()
    window.show()

    for arg in sys.argv[1:]:
        if not arg.startswith('-') and Path(arg).is_file():
            window.load_path(arg)
            break
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())