"""三个表格模型：记录（构建顺序）、键表（键序）、名字前缀池。

共同约定：模型内部只保存"显示行 -> 内部下标"的 `_rows` 列表，
排序排 `_rows`、过滤重算 `_rows`，不在 data() 里做任何拼接或计算，保证 41886 行下滚动仍然跟手。
"""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from manifest_core import myhex, namef1, namef2

from . import theme

_mono_font = None


def mono_font():
    global _mono_font
    if _mono_font is None:
        _mono_font = QFont('Consolas')
        _mono_font.setStyleHint(QFont.Monospace)
    return _mono_font


def _col(title, width, align='left', mono=False):
    return {'title': title, 'width': width, 'align': align, 'mono': mono}


class _TableModel(QAbstractTableModel):
    """按列定义驱动的只读表模型。子类实现 text(i, col) 与 sort_key(i, col)。"""

    columns = ()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._sort_col = -1
        self._sort_order = Qt.AscendingOrder

    # ---------- 只读表接口 ----------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.columns[section]['title']
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter) if self.columns[section]['align'] == 'right' \
                    else int(Qt.AlignLeft | Qt.AlignVCenter)
        return None

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        pos, col = index.row(), index.column()
        spec = self.columns[col]
        if role == Qt.DisplayRole:
            return self.text(self._rows[pos], col)
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter) if spec['align'] == 'right' \
                else int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.FontRole and spec['mono']:
            return mono_font()
        return None

    # ---------- 排序 ----------
    def sort(self, column, order=Qt.AscendingOrder):
        if not self._rows:
            return
        self._sort_col, self._sort_order = column, order
        keys = [self.sort_key(i, column) for i in range(self.internal_count())]
        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=lambda i: keys[i], reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()

    def apply_sort(self):
        """过滤后重排行序，保持表头当前的排序设置。"""
        if self._sort_col >= 0:
            self.sort(self._sort_col, self._sort_order)

    def set_rows(self, rows):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    # ---------- 子类接口 ----------
    def internal_count(self):
        raise NotImplementedError

    def text(self, i, col):
        raise NotImplementedError

    def sort_key(self, i, col):
        return self.text(i, col)

    def source_index(self, pos):
        return self._rows[pos]

    def visible_count(self):
        return len(self._rows)

    def position_of(self, i):
        """内部下标 -> 当前显示行；被过滤掉时返回 -1（供跨面板定位使用）。"""
        try:
            return self._rows.index(i)
        except ValueError:
            return -1


# ---------------------------------------------------------------- 记录（构建顺序）
class RecordsModel(_TableModel):
    columns = (
        _col('#', 80, 'right'),
        _col('键序', 70, 'right'),
        _col('key(hex)', 90, 'right', True),
        _col('bundle 名', 300),
        _col('前缀', 220),
        _col('文件名', 170, 'left', True),
        _col('size', 100, 'right'),
        _col('依赖数', 60, 'right'),
        _col('word', 90, 'right', True),
        _col('byte', 100, 'right', True),
        _col('存储路径 storage_path', 400, 'left', True),
        _col('assetBundleHash', 280, 'left', True),
        _col('fileHash', 280, 'left', True),
    )
    _TEXT_KEYS = {
        3: 'full_name', 4: 'pool_prefix', 5: 'name',
        10: 'storage_path', 11: 'hash_hex', 12: 'file_hash_hex',
    }

    # 数值列：按列号直接取记录字段（其余列见 _TEXT_KEYS）
    _NUM_KEYS = {
        0: 'index', 1: 'key_order', 2: 'key_hex',
        6: 'size', 7: 'dependency_count', 8: 'word', 9: 'byte',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.records = []

    def set_result(self, result):
        self.records = result.records if result else []
        self._sort_col = -1
        self.set_rows(list(range(len(self.records))))

    def internal_count(self):
        return len(self.records)

    def text(self, i, col):
        rec = self.records[i]
        if col in self._TEXT_KEYS:
            return rec[self._TEXT_KEYS[col]]
        return str(rec[self._NUM_KEYS[col]])

    def sort_key(self, i, col):
        rec = self.records[i]
        if col in self._TEXT_KEYS:
            return rec[self._TEXT_KEYS[col]].lower()
        return rec[self._NUM_KEYS[col]]

    # ---------- 过滤 ----------
    def set_predicate(self, predicate):
        if predicate is None:
            rows = list(range(len(self.records)))
        else:
            rows = [i for i in range(len(self.records)) if predicate(i)]
        self.set_rows(rows)
        self.apply_sort()

    def record_index_at(self, pos):
        return self._rows[pos]


# ---------------------------------------------------------------- 键表（键序）
class KeyTableModel(_TableModel):
    columns = (
        _col('键序', 80, 'right'),
        _col('key(hex)', 100, 'right', True),
        _col('record_word', 110, 'right', True),
        _col('record_byte', 120, 'right', True),
        _col('记录#', 80, 'right'),
        _col('同键组#', 90, 'right'),
        _col('组内#', 70, 'right'),
        _col('组大小', 70, 'right'),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None

    def set_result(self, result, only_duplicates=False):
        self.result = result
        self._only_dup = only_duplicates
        n = result.file_count if result else 0
        if only_duplicates and result:
            rows = [i for i in range(n) if result.key_group_size[i] > 1]
        else:
            rows = list(range(n))
        self._sort_col = -1
        self.set_rows(rows)

    def internal_count(self):
        return self.result.file_count if self.result else 0

    def _values(self, i):
        r = self.result
        word = r.record_offsets[i]
        return (i, r.name_hashes[i], word, word * 4, r.rec_index_by_word[word],
                r.key_group_no[i], r.key_pos_in_group[i], r.key_group_size[i])

    def text(self, i, col):
        v = self._values(i)[col]
        if col == 1:
            return myhex(v)
        if col in (2, 3):
            return myhex(v)
        return str(v)

    def sort_key(self, i, col):
        return self._values(i)[col]

    def data(self, index, role=Qt.DisplayRole):
        value = super().data(index, role)
        if value is not None:
            return value
        if role == Qt.BackgroundRole:
            # 同键组按组号奇偶着色，连续重复的键一眼可辨
            i = self._rows[index.row()]
            gno = self.result.key_group_no[i]
            dup = self.result.key_group_size[i] > 1
            if dup:
                key = 'KEY_DUP_EVEN' if gno % 2 == 0 else 'KEY_DUP_ODD'
            else:
                key = 'KEY_ROW_EVEN' if gno % 2 else 'KEY_ROW_ODD'
            return QBrush(QColor(theme.cells()[key]))
        if role == Qt.ForegroundRole:
            # 底色是模型自定的，文字必须一并给出：否则深色主题下浅色底纹 + 浅色文字 = 看不清
            return QBrush(QColor(theme.cells()['INK']))
        return None

    def record_index_at(self, pos):
        """键序显示行 -> 对应记录在 records 中的下标（必须经 record_offsets 反查）。"""
        word = self.result.record_offsets[self._rows[pos]]
        return self.result.rec_index_by_word[word]


# ---------------------------------------------------------------- 名字前缀池
class PoolModel(_TableModel):
    columns = (
        _col('字节偏移', 100, 'right', True),
        _col('word', 90, 'right', True),
        _col('原始 hex', 260, 'left', True),
        _col('加密', 60),
        _col('粒度', 60),
        _col('目录前缀', 320),
        _col('引用记录数', 90, 'right'),
        _col('示例全长名', 400),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = []

    def set_result(self, result):
        self.pool = result.pool_raw if result else []
        self._sort_col = -1
        self.set_rows(list(range(len(self.pool))))
        self.sort(6, Qt.DescendingOrder)     # 默认按引用数降序，便于一眼看出无死条目

    def internal_count(self):
        return len(self.pool)

    def _values(self, i):
        e = self.pool[i]
        return (e['byte'], e['word'], e['raw_hex'],
                namef1.get(e['flag1'], e['flag1']), namef2.get(e['flag2'], e['flag2']),
                e['prefix'], e['ref_count'], e['sample_name'])

    def text(self, i, col):
        v = self._values(i)[col]
        if col in (0, 1):
            return myhex(v)
        return str(v)

    def sort_key(self, i, col):
        return self._values(i)[col]