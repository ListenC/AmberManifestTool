"""记录 Tab：按构建顺序排列的记录表 + 详情三页（字段 / 依赖 / 原始 word）。

两种顺序必须分清：本表按 records 的磁盘顺序（构建顺序）展示，键表顺序见 key_order 列；
跨面板定位统一走 AppState 的信号，面板之间不互相 import。
"""

from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTabWidget,
                               QTableView, QTableWidget, QTableWidgetItem, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from hashcode import GetNameHash
from manifest_core import u32

from .models import RecordsModel, mono_font
from .query import (RecordIndex, build_predicate, dependency_closure, describe,
                    lookup_storage_path, normalize_storage_path)

_QUERY_HINT = '过滤：name:xxx  prefix:xor/dir  size:>1000  dep:>0  enc:xor  key:0x1a2b  dup:key  path:00/'
_QUERY_TOOLTIP = (
    '空白分隔多个条件，全部满足才显示。\n'
    '子串匹配：name: / prefix: / path: / hash: / filehash:\n'
    '精确值：index: / word: / byte: / pool:（支持 0x 十六进制）、key:x 按 hex 前缀\n'
    '数值比较：size:>N  size:100-200  dep:>0\n'
    '标志位：enc:off|xor  granularity:one|dir|all\n'
    '其它：dup:key 只看同键组大于 1 条的记录'
)

_EXPORT_TOOLTIP = (
    '把选定的记录连同它们的全部传递依赖导出成 copyfiles.txt：\n'
    '每行 `存储路径（反斜杠）<TAB>bundle 名（反斜杠）`，可直接喂给下载 / 拷贝脚本。\n'
    '默认文件名 copyfiles.txt，保存位置自选。\n\n'
    '导出当前过滤结果：表格里现在显示的所有行（排序不影响集合）\n'
    '导出选中记录：表格里高亮选中的行（未选中时取当前行）'
)

_LOOKUP_HINT = '粘贴 storage_path / CDN 链接 / 32 位 assetBundleHash 反查 bundle 名'
_LOOKUP_TOOLTIP = (
    '手上有存储路径、CDN 链接或下载下来的文件名，反查它属于哪个 bundle。\n'
    '依次尝试三级匹配：\n'
    '1. 整条 storage_path 精确命中（如 de/5ed79f0c…76c1）\n'
    '2. 末段 32 位 assetBundleHash 精确命中（CDN 上落盘的文件名）\n'
    '3. 以上都没中才按子串扫描（只记得中间片段时用）\n'
    '前两级命中会直接跳到那条记录；第三级则把表过滤成命中行。'
)

# 依赖树的列；树节点用 data(0, _ROLE_REC) 存 (records 下标, 记录起始 word)
_DEP_COLS = ('依赖树（bundle 名）', 'size', '依赖数', '记录#', 'word')
_ROLE_REC = Qt.UserRole           # 真实节点：(records 下标, word)；根节点同此
_ROLE_PLACEHOLDER = Qt.UserRole + 1   # 懒加载占位子节点（只为让展开箭头出现）
_DEP_NODE_LIMIT = 5000            # “全部展开”的节点上限，防菱形依赖爆炸


# ---------------------------------------------------------------- 小工具
def _new_table(headers, rows=0):
    table = QTableWidget(rows, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
    table.setShowGrid(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _set_row(table, row, values, mono_cols=(), right_cols=()):
    """写入一行；单元格首次出现时按 mono/对齐要求建好，之后只改文本（避免选择变化时重建控件）。"""
    for col, value in enumerate(values):
        item = table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if col in mono_cols:
                item.setFont(mono_font())
            if col in right_cols:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, col, item)
        item.setText(str(value))


def _align_tree_nums(item):
    """依赖树的数值列右对齐（第 0 列是树列，保持左对齐）。"""
    for col in range(1, len(_DEP_COLS)):
        item.setTextAlignment(col, Qt.AlignRight | Qt.AlignVCenter)


def _group(title, hint='', *widgets):
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(6)
    if hint:
        label = QLabel(hint)
        label.setObjectName('hint')
        label.setWordWrap(True)
        lay.addWidget(label)
    for widget in widgets:
        lay.addWidget(widget)
    return box


def _row(*widgets):
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for widget in widgets:
        lay.addWidget(widget)
    lay.addStretch(1)
    return host


def _ok(flag):
    return '通过' if flag else '不通过'


def _name_area_fields(name_len, steps):
    """名字区的每个 word 都拆出字节级归属（word 0 的第 1 字节是长度，其后是 UTF-8 文件名，尾部是对齐填充）。"""
    out = []
    for j in range(steps):
        lo, hi = 4 * j, 4 * j + 3          # 名字区内的字节下标，0 = 长度字节
        file_lo = max(lo, 1)
        file_hi = min(hi, name_len)
        parts = []
        if lo <= 0 <= hi:
            parts.append('nameLen')
        if file_lo <= file_hi:
            parts.append(f'fileName[{file_lo - 1}..{file_hi - 1}]')
        if file_hi < hi:
            parts.append('padding')
        out.append(' + '.join(parts))
    return out


def _word_fields(rec):
    """记录内每个 word 对应的字段名，长度恰好等于 word_len。"""
    names = [f'assetBundleHash[{i}]' for i in range(4)]
    names += [f'fileHash[{i}]' for i in range(4)]
    names.append('size')
    names.append('dependencyCount')
    names += [f'dependency[{i}]' for i in range(rec['dependency_count'])]
    names.append('namePointer')
    names += _name_area_fields(rec['name_len'], (rec['word_len'] - len(names)))
    return names


def _compact_label(names):
    """把一行 4 个 word 的字段名压成 `assetBundleHash[0..3]` 这类简短写法。"""
    bases = []
    for name in names:
        base = name.split('[', 1)[0]
        if not bases or bases[-1] != base:
            bases.append(base)
    if len(bases) == 1:
        idx = [n[len(bases[0]):] for n in names]
        if len(names) > 1 and all(x.startswith('[') and x.endswith(']') for x in idx):
            return f'{bases[0]}[{idx[0][1:-1]}..{idx[-1][1:-1]}]'
        return f'{bases[0]} ×{len(names)}' if len(names) > 1 else bases[0]
    return ' | '.join(names)


def _fill_hash(table, words, joined):
    labels = ('x', 'y', 'z', 'w', '拼接（32 字符 hex）')
    for i, label in enumerate(labels):
        if i < 4:
            _set_row(table, i, (label, f'{words[i]:,}', f'0x{words[i]:08x}'),
                     mono_cols=(1, 2), right_cols=(1,))
        else:
            _set_row(table, i, (label, '', joined), mono_cols=(1, 2), right_cols=(1,))


# 字段页的固定行：(显示名, 说明, 取值函数(记录, 面板))
_FIELDS = (
    ('index（构建顺序）', '记录区在磁盘上的顺序；键序见 key_order', lambda r, p: r['index']),
    ('key_order（键序）', '本记录在 nameHash 键表 / 偏移表中的下标', lambda r, p: r['key_order']),
    ('key（键表原值）', '弱哈希 XOR16(bundle 名)，会重复', lambda r, p: f"0x{r['key']:08x}"),
    ('key 重算校验', 'GetNameHash(bundle 名) 与表中 key 逐条比对', lambda r, p: _ok(GetNameHash(r['full_name']) == r['key'])),
    ('同键组', '同键的记录条数（弱哈希碰撞）',
     lambda r, p: f"{r['key_group_size']} 条" if r['key_group_size'] <= 1 else
     f"{r['key_group_size']} 条，组内第 {p.result.key_pos_in_group[r['key_order']] + 1} 条"),
    ('bundle 名（long_name）', '= 前缀池模板 + 记录内文件名', lambda r, p: r['full_name']),
    ('前缀模板 pool_prefix', '来自名字前缀池，被大量记录共享', lambda r, p: r['pool_prefix']),
    ('file_name（记录内保存）', '完整名的后半段', lambda r, p: r['name']),
    ('storage_path', '= getFrontPath(bundle 名) + assetBundleHash，即 CDN / 本地真实路径', lambda r, p: r['storage_path']),
    ('size', 'bundle 文件字节数', lambda r, p: f"{r['size']:,} B"),
    ('dependency_count', '依赖另一条记录起始 word 的个数', lambda r, p: r['dependency_count']),
    ('word（绝对）', '本记录起始 word 偏移', lambda r, p: f"{r['word']:,}  0x{r['word']:x}"),
    ('byte（绝对）', '= word × 4', lambda r, p: f"{r['byte']:,}  0x{r['byte']:x}"),
    ('word_len', '本记录占用的 word 数（含 4 字节对齐填充）', lambda r, p: r['word_len']),
    ('name_pointer（word）', '指向名字前缀池的条目', lambda r, p: f"{r['name_pointer']:,}  0x{r['name_pointer']:x}"),
    ('name_pointer（byte）', '= word × 4', lambda r, p: r['name_pointer'] * 4),
    ('池条目原始字节', '前缀池里的 2 字符标志位 + 目录模板', lambda r, p: p.pool_text(r)),
    ('name_len', '记录内文件名的字节数，不含长度字节本身', lambda r, p: r['name_len']),
)
_FIELD_MONO = (1,)


class RecordsPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.result = None
        self.index = None
        self.model = RecordsModel(self)
        self._pool_by_byte = {}
        self._current = None
        self._current_rev = []
        self._ab_hex = ''
        self._fh_hex = ''

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._apply_query)

        self._build()
        self._clear_detail()

        self.state.manifest_loaded.connect(self.set_result)
        self.state.apply_query.connect(self.apply_text)
        self.state.goto_record.connect(self.goto_record)

    # ---------------------------------------------------------------- 构建界面
    def _build(self):
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText(_QUERY_HINT)
        self.query_edit.setToolTip(_QUERY_TOOLTIP)
        self.query_edit.setClearButtonEnabled(True)
        self.query_edit.textChanged.connect(lambda _t: self._debounce.start())
        self.query_edit.returnPressed.connect(self._apply_query)

        clear_btn = QPushButton('清空')
        clear_btn.clicked.connect(self.query_edit.clear)

        self.count_label = QLabel('—')
        self.count_label.setObjectName('hint')

        # 导出：两种取数口径（过滤结果 / 选中行）都自动并上全部传递依赖
        self.export_btn = QPushButton('导出 ▼')
        self.export_btn.setToolTip(_EXPORT_TOOLTIP)
        export_menu = QMenu(self.export_btn)
        export_filtered = export_menu.addAction('导出当前过滤结果 + 传递依赖')
        export_selected = export_menu.addAction('导出选中记录 + 传递依赖')
        export_filtered.triggered.connect(self.export_filtered)
        export_selected.triggered.connect(self.export_selected)
        self.export_btn.setMenu(export_menu)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        bar.addWidget(QLabel('过滤'))
        bar.addWidget(self.query_edit, 1)
        bar.addWidget(clear_btn)
        bar.addWidget(self.count_label)
        bar.addWidget(self.export_btn)

        # 存储路径 → bundle 名 反查条
        self.lookup_edit = QLineEdit()
        self.lookup_edit.setPlaceholderText(_LOOKUP_HINT)
        self.lookup_edit.setToolTip(_LOOKUP_TOOLTIP)
        self.lookup_edit.setClearButtonEnabled(True)
        self.lookup_edit.textChanged.connect(self._on_lookup_text_changed)
        self.lookup_edit.returnPressed.connect(self._apply_lookup)

        lookup_btn = QPushButton('反查')
        lookup_btn.setToolTip(_LOOKUP_TOOLTIP)
        lookup_btn.clicked.connect(self._apply_lookup)
        lookup_clear = QPushButton('清空')
        lookup_clear.clicked.connect(self._clear_lookup)

        self.lookup_label = QLabel('—')
        self.lookup_label.setObjectName('hint')
        self.lookup_label.setWordWrap(True)
        # 反查结果里最要紧的是 bundle 名，允许选中复制
        self.lookup_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lookup_bar = QHBoxLayout()
        lookup_bar.setContentsMargins(0, 0, 0, 0)
        lookup_bar.setSpacing(6)
        lookup_bar.addWidget(QLabel('反查'))
        lookup_bar.addWidget(self.lookup_edit, 2)
        lookup_bar.addWidget(lookup_btn)
        lookup_bar.addWidget(lookup_clear)
        lookup_bar.addWidget(self.lookup_label, 3)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        # 41886 行必须走固定行高，否则滚动时反复计算行高会明显掉帧
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(22)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        for col, spec in enumerate(self.model.columns):
            self.table.setColumnWidth(col, spec['width'])
        self.table.selectionModel().currentChanged.connect(self._on_current_changed)

        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        copy_shortcut.setContext(Qt.WidgetShortcut)
        copy_shortcut.activated.connect(self._copy_selection)

        self.detail = QTabWidget()
        self.detail.addTab(self._build_fields_page(), '字段')
        self.detail.addTab(self._build_deps_page(), '依赖')
        self.detail.addTab(self._build_raw_page(), '原始 word')

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([580, 320])

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addLayout(bar)
        root.addLayout(lookup_bar)
        root.addWidget(splitter, 1)

    def _build_fields_page(self):
        self.field_table = _new_table(('字段', '值', '说明'), len(_FIELDS))
        self.field_table.setMinimumHeight(24 * len(_FIELDS) + 34)

        self.ab_table = _new_table(('分量', 'u32（十进制）', 'hex'), 5)
        self.fh_table = _new_table(('分量', 'u32（十进制）', 'hex'), 5)
        for table in (self.ab_table, self.fh_table):
            table.setMinimumHeight(24 * 5 + 34)

        copy_ab = QPushButton('复制 assetBundleHash hex')
        copy_ab.clicked.connect(lambda: self._copy_text(self._ab_hex))
        copy_fh = QPushButton('复制 fileHash hex')
        copy_fh.clicked.connect(lambda: self._copy_text(self._fh_hex))
        copy_path = QPushButton('复制 storage_path')
        copy_path.clicked.connect(lambda: self._copy_text(
            self.model.records[self._current]['storage_path'] if self._current is not None else ''))

        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)
        lay.addWidget(_group('标识与名称', '', self.field_table))
        lay.addWidget(_group(
            'assetBundleHash（Int4，存储 / CDN 文件名用）',
            '4 个连续 u32 分量，拼接成 32 字符 hex 后就是 storage_path 的末段，也是 bundle 的 128 位身份。',
            self.ab_table, _row(copy_ab, copy_path)))
        lay.addWidget(_group(
            'fileHash（Int4，下载后校验用）',
            '由 Hash.FromBytes(bundle 字节流) 得到，是弱校验和而非均匀哈希，分量高位通常为 0（见 manifest_format.md §13.3）。',
            self.fh_table, _row(copy_fh)))
        lay.addStretch(1)
        return _scroll(host)

    def _build_deps_page(self):
        self.dep_label = QLabel('—')
        self.dep_label.setObjectName('hint')
        self.rev_label = QLabel('—')
        self.rev_label.setObjectName('hint')

        # 依赖是树而不是平表：一条 bundle 依赖的 bundle 往往又依赖别的 bundle，
        # 平表看不出层级与传递关系，也无法回答“一共牵扯到哪些 bundle”。
        self.dep_tree = QTreeWidget()
        self.dep_tree.setColumnCount(len(_DEP_COLS))
        self.dep_tree.setHeaderLabels(list(_DEP_COLS))
        self.dep_tree.setAlternatingRowColors(True)
        self.dep_tree.setUniformRowHeights(True)      # 树可能上千节点，固定行高才滚得动
        self.dep_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.dep_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dep_tree.setMinimumHeight(150)
        self.dep_tree.itemExpanded.connect(self._on_dep_expanded)
        self.dep_tree.itemDoubleClicked.connect(self._on_dep_activated)
        self.dep_tree.setToolTip('单击三角逐层展开；双击一行跳到该记录')
        dep_head = self.dep_tree.header()
        dep_head.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(_DEP_COLS)):
            dep_head.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        expand_btn = QPushButton('全部展开传递依赖')
        expand_btn.setToolTip('递归展开整棵树；节点过多时会自动停止并提示')
        expand_btn.clicked.connect(self._expand_all)
        collapse_btn = QPushButton('全部折叠')
        collapse_btn.clicked.connect(self._collapse_all)

        self.rev_table = _new_table(('记录#', 'bundle 名', 'size'))
        self.rev_table.doubleClicked.connect(self._goto_dependent)
        self.rev_table.setToolTip('双击一行跳到依赖本记录的记录')

        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        # 树要尽量占满：单独建 group 以便给它 stretch，否则会被压成两三行高
        tree_box = QGroupBox('本记录依赖谁（按树递归展开）')
        tree_lay = QVBoxLayout(tree_box)
        tree_lay.setContentsMargins(8, 8, 8, 8)
        tree_lay.setSpacing(6)
        tree_lay.addWidget(QLabel(
            '每个值是另一条记录起始的 word 偏移，兄弟节点的先后就是记录里 dependencies 的顺序；'
            '顶层是本记录，向下依次是直接依赖与间接依赖。\n'
            '同一 bundle 在多条路径下会各出现一次，↺ 表示该记录已在祖先链中（循环依赖，不再展开）。',
            objectName='hint', wordWrap=True))
        tree_lay.addWidget(self.dep_label)
        tree_lay.addWidget(_row(expand_btn, collapse_btn))
        tree_lay.addWidget(self.dep_tree, 1)

        rev_box = _group(
            '谁依赖本记录（反查索引 dependents）',
            '由解析时建一次反向索引得到；依赖它的 bundle 必须先加载。',
            self.rev_label, self.rev_table)

        split = QSplitter(Qt.Vertical)
        split.addWidget(tree_box)
        split.addWidget(rev_box)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 80])
        lay.addWidget(split, 1)
        return host

    def _build_raw_page(self):
        self.raw_label = QLabel('—')
        self.raw_label.setObjectName('hint')
        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setObjectName('monoDump')
        self.raw_edit.setReadOnly(True)
        self.raw_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        dump_font = QFont('Consolas')
        dump_font.setStyleHint(QFont.Monospace)
        dump_font.setPointSizeF(10.0)
        self.raw_edit.setFont(dump_font)

        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)
        lay.addWidget(self.raw_label)
        lay.addWidget(self.raw_edit, 1)
        return host

    # ---------------------------------------------------------------- 数据入口
    def set_result(self, result):
        self.result = result
        self.index = RecordIndex(result) if result else None
        self._pool_by_byte = {e['byte']: e for e in result.pool_raw} if result else {}
        self.model.set_result(result)
        self._current = None
        self._debounce.stop()
        self.query_edit.blockSignals(True)
        self.query_edit.clear()
        self.query_edit.blockSignals(False)
        self.lookup_edit.blockSignals(True)
        self.lookup_edit.clear()
        self.lookup_edit.blockSignals(False)
        self.lookup_label.setText('—')
        self._update_count()
        self._clear_detail()
        if self.model.rowCount():
            self.table.setCurrentIndex(self.model.index(0, 0))
            self.table.selectRow(0)

    def apply_text(self, text):
        self._debounce.stop()
        self.query_edit.setText(text)
        self._debounce.stop()          # setText 会触发 textChanged，这里同步应用而不等去抖
        self._apply_query()

    def goto_record(self, index):
        if self.index is None or not (0 <= index < self.model.internal_count()):
            return
        pos = self.model.position_of(index)
        if pos < 0:                    # 被当前过滤条件挡掉了：先清空条件再定位
            self.query_edit.clear()
            self._debounce.stop()
            self._apply_query()
            pos = self.model.position_of(index)
        if pos < 0:
            return
        self.table.setCurrentIndex(self.model.index(pos, 0))
        self.table.selectRow(pos)
        self.table.scrollTo(self.model.index(pos, 0), QAbstractItemView.PositionAtCenter)
        self.table.setFocus()

    # ---------------------------------------------------------------- 查询
    def _apply_query(self):
        if self.index is None:
            return
        text = self.query_edit.text().strip()
        predicate = build_predicate(text, self.index) if text else None
        keep = self._current
        self.model.set_predicate(predicate)
        self._update_count(describe(text) if text else '')
        pos = self.model.position_of(keep) if keep is not None else -1
        if self.model.rowCount():
            pos = pos if pos >= 0 else 0
            self.table.setCurrentIndex(self.model.index(pos, 0))
            self.table.selectRow(pos)
        else:
            self._clear_detail()

    def _update_count(self, condition=''):
        text = f'显示 {self.model.rowCount():,} / {self.model.internal_count():,} 条'
        if condition:
            text += f'　·　条件：{condition}'
        self.count_label.setText(text)

    # ---------------------------------------------------------------- 存储路径反查
    def _on_lookup_text_changed(self, _text):
        # 输入变了，上一次的结果就不再对应当前输入，先清掉免得看错
        self.lookup_label.setText('—')

    def _clear_lookup(self):
        self.lookup_edit.clear()
        self.lookup_label.setText('—')

    def _apply_lookup(self):
        """按存储路径反查 bundle 名：精确命中直接跳到那条记录，只中片段则过滤成命中行。

        bundle 名在表里是「前缀 + 记录内文件名」，而用户手上通常只有存储路径或下载下来的
        文件名，两者对不上号，所以要反过来查（详见 query.lookup_storage_path 的三级匹配）。
        """
        if self.index is None:
            self.lookup_label.setText('（未载入 manifest）')
            return
        raw = self.lookup_edit.text()
        kind, hits = lookup_storage_path(raw, self.index)
        norm = normalize_storage_path(raw)

        if kind == 'none':
            self.lookup_label.setText(
                f'未找到匹配的记录：{norm or raw.strip()!r}　'
                '（可粘贴完整 storage_path、CDN 链接、32 位 assetBundleHash，或只记得的片段）')
            return
        if kind == 'sub':
            # 片段命中可能有很多条，交给表格过滤，复用既有的 path: 条件与选中逻辑
            self.lookup_label.setText(f'子串命中 {len(hits):,} 条，已在表中过滤显示')
            self._debounce.stop()
            self.query_edit.setText(f'path:{norm}')
            self._apply_query()
            return

        index = hits[0]
        rec = self.model.records[index]
        how = ('storage_path 精确命中' if kind == 'full'
               else f'assetBundleHash 精确命中（{len(hits)} 条）')
        self.lookup_label.setText(f'{how} → bundle 名：{rec["full_name"]}　（记录 #{index}）')
        # goto_record 会在目标行被当前过滤挡住时自动清条件重查，所以这里不必自己清
        self.state.goto_record.emit(index)

    # ---------------------------------------------------------------- 导出
    def export_filtered(self):
        """导出表格当前显示的全部记录（过滤后的结果，含各自传递依赖）。"""
        self._export(self._visible_record_indices(), f'当前过滤结果 {self.model.rowCount():,} 条')

    def export_selected(self):
        """导出表格里选中的记录（含各自传递依赖）。"""
        self._export(self._selected_record_indices(), '选中记录')

    def _visible_record_indices(self):
        return [self.model.record_index_at(pos) for pos in range(self.model.rowCount())]

    def _selected_record_indices(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows and self.table.currentIndex().isValid():
            rows = [self.table.currentIndex().row()]
        return [self.model.record_index_at(pos) for pos in rows]

    def _export(self, starts, origin):
        """把 starts 及其传递依赖闭包写成 copyfiles.txt，每行 `存储路径<TAB>bundle 名`。

        口径与 ManifestViewTool 一致：依赖按 word 反查、递归求闭包并去重，
        路径里的 / 统一换成 \\，导完后汇报条数、总大小与落盘位置。
        """
        if self.result is None:
            QMessageBox.information(self, '导出', '尚未载入 manifest，没有可导出的内容。')
            return
        if not starts:
            QMessageBox.information(self, '导出', '没有可导出的记录：请先过滤，或在表中选中若干行。')
            return

        indices = dependency_closure(self.result.records, self.result.rec_index_by_word, starts)
        total = sum(self.result.records[i]['size'] for i in indices)
        default = str(Path(self.result.source).parent / 'copyfiles.txt')
        path, _filter = QFileDialog.getSaveFileName(
            self, '导出 copyfiles.txt', default, '文本文件 (*.txt);;所有文件 (*)')
        if not path:
            return

        lines = []
        for i in indices:
            rec = self.result.records[i]
            lines.append(rec['storage_path'].replace('/', '\\') + '\t' +
                         rec['full_name'].replace('/', '\\'))
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
        except OSError as exc:
            QMessageBox.critical(self, '导出失败', f'无法写入：\n{path}\n\n{exc}')
            return

        mb = total / (1024 * 1024)
        QMessageBox.information(
            self, '导出完成',
            f'来源：{origin}\n'
            f'文件数量：{len(indices):,} 个（已去重，含全部传递依赖）\n'
            f'总大小：{total:,} 字节（{mb:.2f} MB）\n\n'
            f'路径：{path}')

    # ---------------------------------------------------------------- 选中与详情
    def _on_current_changed(self, current, _previous):
        if not current.isValid():
            self._clear_detail()
            return
        self._current = self.model.record_index_at(current.row())
        self._update_detail(self.model.records[self._current])

    def _clear_detail(self):
        self._current = None
        self._current_rev = []
        self._ab_hex = self._fh_hex = ''
        for row, (label, note, _fn) in enumerate(_FIELDS):
            _set_row(self.field_table, row, (label, '—', note), mono_cols=_FIELD_MONO)
        for table in (self.ab_table, self.fh_table):
            _fill_hash(table, [0, 0, 0, 0], '—')
        self.dep_tree.clear()
        self.rev_table.setRowCount(0)
        self.dep_label.setText('—')
        self.rev_label.setText('—')
        self.raw_label.setText('—')
        self.raw_edit.setPlainText('')

    def _update_detail(self, rec):
        for row, (label, note, fn) in enumerate(_FIELDS):
            try:
                value = fn(rec, self)
            except Exception as exc:                     # noqa: BLE001 —— 单个字段异常不应拖垮整个面板
                value = f'<{type(exc).__name__}: {exc}>'
            _set_row(self.field_table, row, (label, value, note), mono_cols=_FIELD_MONO)

        self._ab_hex = rec['hash_hex']
        self._fh_hex = rec['file_hash_hex']
        _fill_hash(self.ab_table, rec['asset_bundle_hash'], rec['hash_hex'])
        _fill_hash(self.fh_table, rec['file_hash'], rec['file_hash_hex'])

        self._fill_deps(rec)

        rev = self.result.dependents.get(rec['word'], [])
        self._current_rev = list(rev)
        self.rev_table.setRowCount(len(rev))
        for i, idx in enumerate(rev):
            other = self.result.records[idx]
            _set_row(self.rev_table, i, (idx, other['full_name'], f'{other["size"]:,}'),
                     mono_cols=(2,), right_cols=(0, 2))
        self.rev_label.setText(f'{len(rev)} 条记录依赖本记录')

        self._fill_raw(rec)

    def _fill_raw(self, rec):
        datas = self.result.datas
        start, total = rec['word'], rec['word_len']
        fields = _word_fields(rec)
        lines = [f'记录 #{rec["index"]}　word {start:,} ~ {start + total - 1:,}（{total} word = {total * 4} B）',
                 f'bundle 名：{rec["full_name"]}',
                 f'存储路径：{rec["storage_path"]}',
                 '（每行 4 word = 16 B；右侧标出这些 word 对应的字段）',
                 '']
        for k in range(0, total, 4):
            words, names = [], []
            for j in range(4):
                rel = k + j
                if rel < total:
                    words.append(f'{u32(datas, start + rel):08x}')
                    names.append(fields[rel])
                else:
                    words.append('--------')
            abs_word = start + k
            lines.append(f'+{k:04x}　word {abs_word:#09x}　byte {abs_word * 4:#09x}　'
                         f'{" ".join(words)}　　{_compact_label(names) if names else "（记录末尾）"}')
        self.raw_label.setText(f'共 {total} word，下列即 datas 中 word {start:,} 起的原始内容')
        self.raw_edit.setPlainText('\n'.join(lines))

    # ---------------------------------------------------------------- 依赖树
    def _closure(self, rec):
        """本记录传递依赖闭包里的 bundle 个数（含本记录）。

        菱形依赖会让“按路径展开”的节点数远大于此数，所以这里按 word 去重，
        回答的是“一共牵扯到哪些 bundle”。指向记录内部（不是起点）的 word 不计入。
        """
        seen = {rec['word']}
        stack = list(rec['dependencies'])
        while stack and len(seen) < _DEP_NODE_LIMIT:
            index = self.result.rec_index_by_word.get(stack.pop())
            if index is None:
                continue
            word = self.result.records[index]['word']
            if word in seen:
                continue
            seen.add(word)
            stack.extend(self.result.records[index]['dependencies'])
        return len(seen)

    def _fill_deps(self, rec):
        """重建依赖树：顶层是本记录，第二层是直接依赖，更深的层按需懒加载。"""
        self.dep_tree.clear()
        if rec['dependency_count']:
            self.dep_label.setText(
                f'dependency_count = {rec["dependency_count"]}，直接依赖 {len(rec["dependencies"])} 条 · '
                f'传递闭包（含本记录）共 {self._closure(rec)} 个不同 bundle')
        else:
            self.dep_label.setText('dependency_count = 0，本记录不依赖任何其他 bundle')

        root = QTreeWidgetItem()
        root.setText(0, f'本记录　{rec["full_name"]}')
        root.setText(1, f'{rec["size"]:,}')
        root.setText(2, str(rec['dependency_count']))
        root.setText(3, str(rec['index']))
        root.setText(4, f'0x{rec["word"]:x}')
        root.setData(0, _ROLE_REC, (rec['index'], rec['word']))
        root.setToolTip(0, f'{rec["full_name"]}\n{rec["storage_path"]}\n（本记录，双击回到自身）')
        _align_tree_nums(root)
        for word in rec['dependencies']:
            root.addChild(self._make_dep_item(word, {rec['word']}))
        self.dep_tree.addTopLevelItem(root)
        root.setExpanded(True)          # 默认展开一层，直接依赖一眼可见

    def _make_dep_item(self, word, ancestors):
        """构造一个依赖节点（不递归展开）；ancestors 是根到父节点的 word 集合。"""
        item = QTreeWidgetItem()
        _align_tree_nums(item)
        item.setText(4, f'0x{word:x}')

        index = self.result.rec_index_by_word.get(word)
        if index is None:
            # 该 word 落在某条记录内部，不指向任何记录的起点，无法再往下走
            item.setText(0, '（不是任何记录的起始 word）')
            item.setToolTip(0, f'word {word:,}（byte {word * 4:,}）不指向任何记录的起点')
            return item

        rec = self.result.records[index]
        item.setText(0, rec['full_name'])
        item.setText(1, f'{rec["size"]:,}')
        item.setText(2, str(rec['dependency_count']))
        item.setText(3, str(index))
        item.setToolTip(0, f'{rec["full_name"]}\n{rec["storage_path"]}\n双击跳到该记录')
        item.setData(0, _ROLE_REC, (index, word))

        if word in ancestors:
            item.setText(0, f'{rec["full_name"]}　↺ 循环依赖')
            item.setToolTip(0, f'{rec["full_name"]}\n{rec["storage_path"]}\n'
                              '该记录已出现在根到这里的路径上（循环依赖），不再继续展开')
            return item

        if rec['dependency_count']:
            placeholder = QTreeWidgetItem()          # 占位，只为让展开箭头出现
            placeholder.setData(0, _ROLE_PLACEHOLDER, True)
            item.addChild(placeholder)
        return item

    def _chain_words(self, item):
        """根到 item（含）路径上的记录起始 word 集合，用于环检测。"""
        words = set()
        node = item
        while node is not None:
            info = node.data(0, _ROLE_REC)
            if info:
                words.add(info[1])
            node = node.parent()
        return words

    def _on_dep_expanded(self, item):
        """懒加载：把占位子节点换成真正的依赖。"""
        if item.childCount() != 1 or not item.child(0).data(0, _ROLE_PLACEHOLDER):
            return
        info = item.data(0, _ROLE_REC)
        if not info:
            return
        index, _word = info
        ancestors = self._chain_words(item)
        item.takeChild(0)
        for word in self.result.records[index]['dependencies']:
            item.addChild(self._make_dep_item(word, ancestors))

    def _on_dep_activated(self, item, _column):
        info = item.data(0, _ROLE_REC)
        if info:
            self.state.goto_record.emit(info[0])

    def _collapse_all(self):
        self.dep_tree.collapseAll()

    def _expand_all(self):
        """逐层展开整棵树；菱形依赖会让节点数膨胀，因此设上限。"""
        stack = [self.dep_tree.topLevelItem(i)
                 for i in range(self.dep_tree.topLevelItemCount())]
        shown = 0
        while stack and shown < _DEP_NODE_LIMIT:
            item = stack.pop()
            item.setExpanded(True)               # 触发懒加载，填出下一层
            for i in range(item.childCount()):
                stack.append(item.child(i))
            shown += 1
        if stack:
            self.dep_label.setText(
                f'已展开 {shown:,} 个节点后停止（上限 {_DEP_NODE_LIMIT:,}）：'
                '菱形依赖会让节点数急剧膨胀，请改用逐层展开')
        else:
            self.dep_label.setText(f'已展开全部 {shown:,} 个节点'
                                   f'（含重复出现的 bundle，↺ 为循环依赖）')

    def _goto_dependent(self, index):
        row = index.row()
        if 0 <= row < len(self._current_rev):
            self.state.goto_record.emit(self._current_rev[row])

    def _copy_selection(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows and self.table.currentIndex().isValid():
            rows = [self.table.currentIndex().row()]
        if not rows:
            return
        lines = [f'{self.model.records[self.model.record_index_at(pos)]["full_name"]}\t'
                 f'{self.model.records[self.model.record_index_at(pos)]["storage_path"]}' for pos in rows]
        QApplication.clipboard().setText('\n'.join(lines))

    @staticmethod
    def _copy_text(text):
        if text:
            QApplication.clipboard().setText(text)

    def pool_text(self, rec):
        entry = self._pool_by_byte.get(rec['name_pointer'] * 4)
        if not entry:
            return '—'
        return f"{entry['raw_hex']}　（{entry['raw_str']} → {entry['prefix']}，被 {entry['ref_count']} 条记录引用）"


def _scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area