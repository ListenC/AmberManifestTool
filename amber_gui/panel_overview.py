"""概览面板：文件信息、段布局条、表头字段、容器对象、自校验、统计。"""

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QGroupBox, QHeaderView, QLabel, QScrollArea, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from manifest_core import props_hash_hex

from . import theme


# 段太窄时只够画一行，用短名（宽条仍用 _segments() 里的全名）
SHORT_SEG_LABEL = {
    'nameHash 键表（键序）': '键表',
    'recordOffset 表': '偏移表',
    '记录区（构建顺序）': '记录区',
}


def fmt_bytes(n):
    return f'{n:,} B'


def fmt_value(v):
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return '—'
    return str(v)


class SegmentBar(QWidget):
    """按字节比例画出清单的 5 个段，逐段标注 word/byte 起止与长度。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(104)
        self.setMouseTracking(True)
        self._segments = []     # (label, word_start, word_end, byte_start, byte_end, color_idx)
        self._rects = []

    def set_segments(self, segments):
        self._segments = segments
        self.update()

    def paintEvent(self, event):    # noqa: N802 —— Qt 命名
        painter = QPainter(self)
        self._rects = []
        if not self._segments:
            painter.end()
            return
        total = max(1, sum(s[4] - s[3] for s in self._segments))
        width, height = self.width(), self.height()
        x = 0.0
        label_font = QFont(self.font())
        label_font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        mono_font = QFont('Consolas')
        mono_font.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        short_metrics = QFontMetrics(label_font)    # 窄条只写短名，放不下就干脆不写
        colors = theme.segments()
        for label, w0, w1, b0, b1, ci in self._segments:
            length = b1 - b0
            w = width * length / total
            self._rects.append(((x, 0.0, w, float(height)),
                                self._tooltip(label, w0, w1, b0, b1, total)))
            fill, edge, ink = colors[ci % len(colors)]
            painter.fillRect(int(x), 0, max(1, int(w)), height, QColor(fill))
            painter.setPen(QPen(QColor(edge), 1))
            painter.drawRect(int(x), 0, max(1, int(w)), height)
            painter.setPen(QColor(ink))
            if w >= 92:
                painter.setFont(label_font)
                painter.drawText(int(x) + 6, 18, label)
                painter.setFont(mono_font)
                painter.drawText(int(x) + 6, 36, f'word {w0:,} ~ {w1 - 1:,}')
                painter.drawText(int(x) + 6, 50, f'byte {b0:,} ~ {b1 - 1:,}')
                painter.drawText(int(x) + 6, 64, f'{length:,} B  {length * 100.0 / total:.2f}%')
            else:
                # 窄条（表头/键表/偏移表这类占比极小的段）只写一行短名，数值看 tooltip 与「段明细」
                short = SHORT_SEG_LABEL.get(label, label)
                if w >= short_metrics.horizontalAdvance(short) + 8:    # 放不下就不画，免得压到相邻段
                    painter.setFont(label_font)
                    painter.drawText(int(x) + 4, 18, short)
            x += w
        painter.end()

    @staticmethod
    def _tooltip(label, w0, w1, b0, b1, total):
        return (f'{label}\nword {w0:,} ~ {w1 - 1:,}（{w1 - w0:,} word）\n'
                f'byte {b0:,} ~ {b1 - 1:,}\n{b1 - b0:,} B（{(b1 - b0) * 100.0 / total:.2f}%）')

    def mouseMoveEvent(self, event):    # noqa: N802
        pos = event.position().x()
        for (x, _y, w, _h), tip in self._rects:
            if x <= pos <= x + w:
                self.setToolTip(tip)
                return
        self.setToolTip('')


def _table(headers, rows, mono_cols=()):
    """小规模详情表：按内容撑高到能看全（超过上限则内部滚动）。

    末尾追加一个空列并交给 stretch，这样真实列保持自然宽度，不会被拉成一条长空档。
    """
    headers = list(headers) + ['']
    rows = [list(row) + [''] for row in rows]
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setAlternatingRowColors(True)
    table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
    table.setShowGrid(False)
    mono = QFont('Consolas')
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(str(value))
            if c in mono_cols:
                item.setFont(mono)
            table.setItem(r, c, item)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    if rows:
        table.resizeRowsToContents()
        total = table.horizontalHeader().height() + 4
        for r in range(len(rows)):
            total += table.rowHeight(r)
        table.setMinimumHeight(min(total + 4, 900))
    return table


def _group(title, widget):
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(8, 6, 8, 8)
    lay.addWidget(widget)
    return box


class OverviewPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self._body = None
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(10, 10, 10, 10)
        self._host_layout.setSpacing(10)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._host)
        outer.addWidget(scroll)

        self.set_result(None)
        self.state.manifest_loaded.connect(self.set_result)
        # 概览页的底色/文字是建表时写死在 item 上的，换配色后必须整页重建
        theme.notifier.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _scheme):
        if self._result is not None:
            self.set_result(self._result)

    # ---------------------------------------------------------------- 刷新
    def set_result(self, result):
        self._result = result
        if self._body is not None:
            self._host_layout.removeWidget(self._body)
            self._body.deleteLater()
            self._body = None
        self._body = self._build(result)
        self._host_layout.addWidget(self._body)
        self._host_layout.addStretch(1)

    def _build(self, result):
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        if result is None:
            layout.addWidget(QLabel('尚未载入 manifest：用「打开 manifest 文件…」选择资产文件'
                                    '（manifest 通常没有扩展名，对话框里选「所有文件」）。'))
            return body

        st = result.stats
        header = result.header

        header_table = _table(
            ['字段', '值', '说明'],
            [('dataHash', header.data_hash_hex, '整表一致性哈希，见 manifest_format.md §13.2'),
             ('formatVersion', header.format_version, '固定等于 AssetBundleData.FormatVersion = 1'),
             ('fileCount', f'{header.file_count:,}', '记录条数，也是两张索引表的长度'),
             ('stringIndex (word)', f'{header.string_index:,}', '名字前缀池起始位置'),
             ('stringIndex (byte)', f'{header.string_index * 4:,}', '= word × 4'),
             ('nameHash 键表起点 word', f'{header.name_hash_table_word:,}', '升序键，长度 = fileCount'),
             ('recordOffset 表起点 word', f'{header.record_offset_table_word:,}', '与键表同序'),
             ('记录区起点 word', f'{header.record_area_start_word:,}', '= 4 + 2 × fileCount'),
             ('记录区终点 byte', f'{header.string_index * 4:,}', '必须恰好等于 stringIndex × 4'),
             ('前缀池字节数', fmt_bytes(st['pool_bytes']),
              '= .dat 总长 − stringIndex × 4')],
            mono_cols=(1,))

        file_table = _table(
            ['项目', '值'],
            [('文件路径', st.get('source_path', result.source)),
             ('asset_bundle_name', result.asset_bundle_name or '—'),
             ('manifest_version', result.manifest_version or '—'),
             ('原始文件字节数', fmt_bytes(st.get('file_size', 0))),
             ('还原出的 .dat 字节数', fmt_bytes(st['datas_bytes'])),
             ('容器内对象类型', ' / '.join(st.get('object_types', [])) or '—'),
             ('记录条数 fileCount', f'{header.file_count:,}')],
            mono_cols=(1,))

        segbar = SegmentBar()
        segbar.set_segments(self._segments(result))

        layout.addWidget(_group('文件信息', file_table))
        layout.addWidget(_group('段布局（按字节比例；键表=键序，记录区=构建顺序）', segbar))

        seg_table = _table(['段', 'word 起', 'word 止', 'byte 起', 'byte 止', '长度', '占比'],
                           self._segment_rows(result), mono_cols=(1, 2, 3, 4))
        # 首列铺上与该段相同的底色（文字同色配套），和上面布局条一一对应
        colors = theme.segments()
        for row, (_label, _w0, _w1, _b0, _b1, color_idx) in enumerate(self._segments(result)):
            fill, _edge, ink = colors[color_idx]
            item = seg_table.item(row, 0)
            item.setBackground(QColor(fill))
            item.setForeground(QColor(ink))
        layout.addWidget(_group('段明细', seg_table))
        layout.addWidget(_group('表头字段（word 0 ~ 3 与各段边界）', header_table))
        layout.addWidget(_group('自校验（按 §13 反汇编出的算法重算，与原表逐项比对）',
                                self._check_table(result)))
        layout.addWidget(_group('统计', _table(['指标', '值'], self._stat_rows(result))))
        layout.addWidget(self._container_boxes(result))
        return body

    # ---------------------------------------------------------------- 各段数据
    @staticmethod
    def _segments(result):
        h = result.header
        st = result.stats
        n = h.file_count
        return [
            ('表头', 0, 4, 0, 16, 0),
            ('nameHash 键表（键序）', 4, 4 + n, 16, 16 + n * 4, 1),
            ('recordOffset 表', 4 + n, 4 + 2 * n, 16 + n * 4, 16 + 2 * n * 4, 2),
            ('记录区（构建顺序）', 4 + 2 * n, st['record_area_end_word'],
             (4 + 2 * n) * 4, st['record_area_end_word'] * 4, 3),
            ('名字前缀池', h.string_index, h.string_index + st['pool_bytes'] // 4,
             h.string_index * 4, len(result.datas), 4),
        ]

    def _segment_rows(self, result):
        rows = []
        total = len(result.datas)
        for label, w0, w1, b0, b1, _ci in self._segments(result):
            length = b1 - b0
            rows.append((label, f'{w0:,}', f'{max(w0, w1 - 1):,}', f'{b0:,}', f'{max(b0, b1 - 1):,}',
                         fmt_bytes(length), f'{length * 100.0 / total:.2f}%'))
        rows.append(('合计', '', '', '0', f'{total - 1:,}', fmt_bytes(total), '100.00%'))
        return rows

    @staticmethod
    def _check_table(result):
        rows = [(f"{c['id']}. {c['name']}", '通过' if c['ok'] else '不通过', c['actual'], c['expected'])
                for c in result.checks]
        table = _table(['校验项', '结果', '实测', '期望'], rows, mono_cols=(2, 3))
        cells = theme.cells()
        ok_bg, ok_ink = QColor(cells['CHECK_OK']), QColor(cells['INK'])
        bad_bg, bad_ink = QColor(cells['CHECK_BAD']), QColor(cells['INK'])
        for r, c in enumerate(result.checks):
            bg, ink = (ok_bg, ok_ink) if c['ok'] else (bad_bg, bad_ink)
            for col in range(4):
                item = table.item(r, col)
                if item is not None:
                    item.setBackground(bg)
                    item.setForeground(ink)
        return table

    @staticmethod
    def _stat_rows(result):
        st = result.stats
        enc, grain = st['enc_counts'], st['grain_counts']
        return [
            ('.dat 总字节', fmt_bytes(st['datas_bytes'])),
            ('.dat 总 word', f"{st['datas_words']:,}"),
            ('size 合计', f"{st['size_total']:,} B ≈ {st['size_total_mb']:,} MB"),
            ('索引表区字节', fmt_bytes(st['index_tables_bytes'])),
            ('记录区字节', fmt_bytes(st['record_area_bytes'])),
            ('前缀池字节 / 条数', f"{fmt_bytes(st['pool_bytes'])} / {st['pool_count']:,} 条"),
            ('未被引用的前缀池条目', f"{st['pool_unreferenced']:,}"),
            ('单个前缀最多被引用', f"{st['pool_max_ref']:,} 次"),
            ('不同 nameHash 键', f"{st['distinct_keys']:,}"),
            ('重复键组数 / 涉及行数', f"{st['duplicate_key_groups']:,} / {st['duplicate_key_rows']:,}"),
            ('最大同键组', f"{st['max_key_group']:,} 条"),
            ('有依赖的记录 / 最大依赖数', f"{st['records_with_deps']:,} / {st['max_deps']:,}"),
            ('xor / off', f"{enc['xor']:,} / {enc['off']:,}"),
            ('粒度 one / dir / all', f"{grain['one']:,} / {grain['dir']:,} / {grain['all']:,}"),
            ('storage_path 长度范围', f"{st['storage_path_len_min']} ~ {st['storage_path_len_max']} 字符"),
            ('不同完整名', f"{st['distinct_names']:,}"),
        ]

    @staticmethod
    def _container_boxes(result):
        widget = QWidget()
        lay = QVBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        mono = result.mono_tree
        mono_rows = [(k, fmt_value(v)) for k, v in mono.items() if k not in ('data', 'm_Script')]
        mono_rows.append(('m_Script', fmt_value(mono.get('m_Script'))))
        mono_rows.append(('data', 'Amber.AssetBundleData（datas: '
                                  f"{len(mono.get('data', {}).get('datas', [])):,} 个 u32）"))
        lay.addWidget(_group('MonoBehaviour：Amber.AssetBundleManifestInfo',
                             _table(['字段', '值'], mono_rows, mono_cols=(1,))))

        script = result.script_tree
        script_rows = [(k, fmt_value(v)) for k, v in script.items() if k != 'm_PropertiesHash']
        script_rows.append(('m_PropertiesHash（16 字节脚本指纹）',
                            props_hash_hex(script.get('m_PropertiesHash'))))
        lay.addWidget(_group('MonoScript（坐实类型归属）',
                             _table(['字段', '值'], script_rows, mono_cols=(1,))))

        bundle = result.bundle_tree
        if bundle:
            simple = [(k, fmt_value(v)) for k, v in bundle.items()
                      if k not in ('m_Container', 'm_PreloadTable', 'm_SceneHashes')]
            simple.append(('m_SceneHashes', fmt_value(bundle.get('m_SceneHashes'))))
            lay.addWidget(_group('AssetBundle', _table(['字段', '值'], simple, mono_cols=(1,))))

            container = bundle.get('m_Container') or []
            lay.addWidget(_group('AssetBundle.m_Container（路径 -> 资产）', _table(
                ['#', '容器内路径', 'preloadIndex', 'preloadSize', 'asset（FileID / PathID）'],
                [(i, path, entry.get('preloadIndex'), entry.get('preloadSize'),
                  fmt_value(entry.get('asset'))) for i, (path, entry) in enumerate(container)],
                mono_cols=(2, 3, 4))))

            preload = bundle.get('m_PreloadTable') or []
            lay.addWidget(_group('AssetBundle.m_PreloadTable（预加载对象引用）', _table(
                ['#', 'm_FileID', 'm_PathID'],
                [(i, e.get('m_FileID'), e.get('m_PathID')) for i, e in enumerate(preload)],
                mono_cols=(1, 2))))
        return widget