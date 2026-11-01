"""自定义扰动流（游戏）对话框。

`storage_path` 的前缀由 256 字节扰动流决定，而这张表是分游戏的：内置两份
（幻想收束 / Engage Kill），其他同框架游戏可以在这里注册进来 —— 粘贴十六进制文本，
或从文件导入（正好 256 字节按原始表读，否则当 hex 文本解析）。
注册只改 hashcode.STREAMS（进程内全局），落盘与刷新下拉框由 main_window 负责。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from hashcode import (BUILTIN_STREAMS, STREAM_SIZE, STREAMS, load_stream_file,
                      parse_stream_text, register_stream, stream_to_text,
                      unregister_stream)


class StreamDialog(QDialog):
    """管理扰动流：列出全部游戏，新增（hex 粘贴 / 文件导入）、删除自定义项。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('自定义扰动流（游戏）')
        self.resize(620, 560)
        self._build()
        self._refresh_list()

    # ---------------------------------------------------------------- 界面
    def _build(self):
        hint = QLabel(
            f'存储路径前缀由 {STREAM_SIZE} 字节扰动流决定，每个游戏一份表。\n'
            '在下面粘贴十六进制文本（支持 56 91 BC…／0x56,0x91,…／5691bc… 三种写法），'
            '或点「从文件导入…」选择 .bin 原始表 / 含 hex 的 .txt、.json。')
        hint.setWordWrap(True)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_item_changed)
        self.list.itemDoubleClicked.connect(self._load_selected_into_form)

        self.delete_button = QPushButton('删除选中的自定义游戏')
        self.delete_button.clicked.connect(self._on_delete)
        self.delete_button.setEnabled(False)

        list_row = QHBoxLayout()
        list_row.addWidget(self.delete_button)
        list_row.addStretch(1)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText('例如 某游戏名（用作下拉框显示名与 --stream 的名字）')

        self.hex_edit = QPlainTextEdit()
        self.hex_edit.setPlaceholderText('256 字节的十六进制，例如 56 91 BC DF …')
        self.hex_edit.setFixedHeight(150)

        import_button = QPushButton('从文件导入…')
        import_button.clicked.connect(self._on_import)

        form = QFormLayout()
        form.addRow('游戏名', self.name_edit)
        form.addRow(import_button, self.hex_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('添加/覆盖')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('关闭')
        buttons.accepted.connect(self._on_add)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(QLabel('已注册的游戏：'))
        layout.addWidget(self.list, 1)
        layout.addLayout(list_row)
        layout.addLayout(form)
        layout.addWidget(buttons)

    # ---------------------------------------------------------------- 列表
    def _refresh_list(self):
        current = self.list.currentItem()
        want = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.list.clear()
        for name, entry in STREAMS.items():
            tag = '内置' if name in BUILTIN_STREAMS else '自定义'
            item = QListWidgetItem(f'{entry["label"]}　—　{tag}（--stream {name}）')
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
            if name == want:
                self.list.setCurrentItem(item)

    def _on_item_changed(self, item, _previous):
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.delete_button.setEnabled(bool(name) and name not in BUILTIN_STREAMS)

    def _load_selected_into_form(self, _item):
        """双击已有游戏：把名字与表填回表单，改完点「添加/覆盖」即可覆盖。"""
        name = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        entry = STREAMS[name]
        self.name_edit.setText(entry['label'])
        self.hex_edit.setPlainText(stream_to_text(entry['table']))

    # ---------------------------------------------------------------- 增删
    def _on_import(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, '选择扰动流文件（256 字节原始表，或含 hex 的文本）', '',
            '所有文件 (*);;原始表 (*.bin);;文本 (*.txt *.json)')
        if not path:
            return
        try:
            table = load_stream_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, self.windowTitle(), f'无法导入该文件：\n{exc}')
            return
        self.hex_edit.setPlainText(stream_to_text(table))
        if not self.name_edit.text().strip():
            self.name_edit.setText(Path(path).stem)

    def _on_add(self):
        label = self.name_edit.text().strip()
        if not label:
            QMessageBox.warning(self, self.windowTitle(), '请先填一个游戏名。')
            return
        key = label.lower()
        if key in BUILTIN_STREAMS:
            QMessageBox.warning(self, self.windowTitle(),
                                f'「{STREAMS[key]["label"]}」是内置游戏名，请换一个。')
            return
        try:
            table = parse_stream_text(self.hex_edit.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, self.windowTitle(), f'扰动流不合法：\n{exc}')
            return
        existed = key in STREAMS
        register_stream(key, table, label)
        self._refresh_list()
        self.name_edit.clear()
        self.hex_edit.clear()
        QMessageBox.information(self, self.windowTitle(),
                                f'已{"覆盖" if existed else "添加"}游戏「{label}」，'
                                '关掉本对话框后即可在下拉框里选中。')

    def _on_delete(self):
        item = self.list.currentItem()
        name = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not name:
            return
        label = STREAMS[name]['label']
        answer = QMessageBox.question(self, self.windowTitle(),
                                      f'删除自定义游戏「{label}」？\n（本机保存的这条扰动流会一并移除）')
        if answer != QMessageBox.StandardButton.Yes:
            return
        unregister_stream(name)
        self._refresh_list()