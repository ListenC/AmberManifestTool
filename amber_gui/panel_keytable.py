"""键表 Tab：nameHash 的升序键表（键序）与它对应的记录偏移。

键表不是记录顺序：键序 i 对应的记录必须用 record_offsets[i] 反查，
这是这套格式最容易看错的地方，所以说明文字常驻在顶部。
"""

from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel,
                               QTableView, QVBoxLayout, QWidget)

from .models import KeyTableModel

_BANNER = ('键表是 nameHash 的升序数组，按「键序」排列，不是记录顺序；'
           '键是弱哈希，同一键可对应多条记录，所以键序 i 对应的记录必须用 record_word 反查记录偏移表。'
           '双击任意一行可跳到记录页并定位到该条。')


class KeyTablePanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.result = None
        self.model = KeyTableModel(self)
        self._build()
        self.set_result(None)
        self.state.manifest_loaded.connect(self.set_result)

    def _build(self):
        banner = QLabel(_BANNER)
        banner.setObjectName('banner')
        banner.setWordWrap(True)

        self.dup_check = QCheckBox('只看重复键（同键组大小 > 1，用于排查弱哈希碰撞）')
        self.dup_check.toggled.connect(self._on_dup_toggled)

        self.count_label = QLabel('—')
        self.count_label.setObjectName('hint')

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addWidget(self.dup_check)
        bar.addStretch(1)
        bar.addWidget(self.count_label)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(22)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        for col, spec in enumerate(self.model.columns):
            self.table.setColumnWidth(col, spec['width'])
        self.table.doubleClicked.connect(self._goto_record)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(banner)
        root.addLayout(bar)
        root.addWidget(self.table, 1)

    # ---------------------------------------------------------------- 数据
    def set_result(self, result):
        self.result = result
        self.model.set_result(result, self.dup_check.isChecked())
        self._update_count()

    def _on_dup_toggled(self, _checked):
        self.model.set_result(self.result, self.dup_check.isChecked())
        self._update_count()

    def _update_count(self):
        if not self.result:
            self.count_label.setText('—')
            return
        st = self.result.stats
        text = (f'键表 {self.result.file_count:,} 条　·　显示 {self.model.rowCount():,} 行　·　'
                f'不同键 {st["distinct_keys"]:,} 个　·　重复键组 {st["duplicate_key_groups"]:,} 组'
                f'（涉及 {st["duplicate_key_rows"]:,} 行，最大 {st["max_key_group"]:,} 条）')
        self.count_label.setText(text)

    def _goto_record(self, index):
        if self.result is None:
            return
        self.state.goto_record.emit(self.model.record_index_at(index.row()))