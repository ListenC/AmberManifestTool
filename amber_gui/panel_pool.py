"""名字前缀池 Tab：记录共享的目录前缀条目。

记录里只保存文件名片段，完整 bundle 名 = 池内前缀 + 文件名；
池的头两位是标志位：X/O = xor(加密) / off(明文)，O/D/A = one / dir / all（打包粒度）。
"""

from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QTableView,
                               QVBoxLayout, QWidget)

from .models import PoolModel

_BANNER = ('记录里只保存文件名片段，完整 bundle 名 = 池内前缀 + 记录内文件名；池条目被大量记录共享，故做了去重。'
           '原始 2 字符标志位：第 1 位 X= xor(加密) / O= off(明文)，第 2 位 O= one / D= dir / A= all（打包粒度）。'
           '双击一行会切到记录页并按该前缀过滤。"引用记录数"为 0 的条目说明池中有未被使用的条目。')


class PoolPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.result = None
        self.model = PoolModel(self)
        self._build()
        self.set_result(None)
        self.state.manifest_loaded.connect(self.set_result)

    def _build(self):
        banner = QLabel(_BANNER)
        banner.setObjectName('banner')
        banner.setWordWrap(True)

        self.count_label = QLabel('—')
        self.count_label.setObjectName('hint')

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
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
        self.table.doubleClicked.connect(self._filter_by_prefix)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(banner)
        root.addLayout(bar)
        root.addWidget(self.table, 1)

    # ---------------------------------------------------------------- 数据
    def set_result(self, result):
        self.result = result
        self.model.set_result(result)
        self._update_count()

    def _update_count(self):
        if not self.result:
            self.count_label.setText('—')
            return
        st = self.result.stats
        self.count_label.setText(
            f'池条目 {st["pool_count"]:,} 条　·　未被引用 {st["pool_unreferenced"]:,} 条　·　'
            f'单条最多被引用 {st["pool_max_ref"]:,} 次　·　共占 {st["pool_bytes"]:,} B')

    def _filter_by_prefix(self, index):
        entry = self.model.pool[self.model.source_index(index.row())]
        self.state.apply_query.emit(f'prefix:{entry["prefix"]}')