"""资源视图（v3）：QChartView 饼图 + QTreeWidget 路径树。

饼图的三条设计约定（都是 v1/v2 踩过坑后定下来的）：
* **按整份 manifest 的真实比例画**：选中某层时，它的 children 只占自己真实的角区间，
  其余角区间用灰色「其他目录」补齐 —— 所以任何时候饼图都代表 100% 的 manifest，
  下钻不会把比例重新归一化（v1/v2 用 QPieSeries 的默认行为，是全饼归一化，比例会虚高）。
* **可缩放，但字号恒定**：滚轮放大/缩小（1x ~ 8x）。QGraphicsView.scale() 会把标签文字
  一起放大，所以 _label_font() 里按当前倍数反向抵消，屏幕上字号始终不变、饼图却变大
  （QChart.zoom() 对没有坐标轴的饼图不起作用，只能用视图变换）。
* **扇区数量封顶**：一层只画最大的 _TOP_SLICES 个子项，其余聚合成一片斜纹「本层其余 N 项」
  （角度 = 那些小项的真实大小之和，所以比例依然诚实）。实测有一层有 4517 个子项，
  全画成扇区光 QtCharts 布局就要 ~1.9 秒，而且全是看不见的发丝 —— 这才是"太卡"的主因。
* **标签"放得下才画"**：某个扇区在当前倍数下的屏幕弧长够长（>= _MIN_ARC_PX）才显示标签，
  且最多 _MAX_LABELS 个，其余 setLabelVisible(False)。子目录一多，1x 下索性一个标签都不画，
  放大到弧长够了才逐个出现。标签只放扇区内部（不外置，外置就是之前那张糊成一片的图），
  放不下的段名以 '…' 收尾，完整信息在 tooltip 里。

交互：树的顶行是「（整份 manifest）」代表虚拟根（否则顶行就是第一个目录，用户看不到根层饼图）；
树选中 ↔ 饼图扇区点击 双向联动；点灰色「其他目录」扇区回到上一层；hover 扇区会弹出
（exploded）、加粗标签并弹出完整信息。
"""

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QGradient, QLinearGradient,
                           QPainter)
from PySide6.QtCharts import QChart, QChartView, QPieSeries, QPieSlice
from PySide6.QtWidgets import (QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter,
                               QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme


# ---------------------------------------------------------------- 数据层 ----------------------------------------------------------------

def build_size_tree(records):
    """从 ManifestResult.records 建路径前缀树（虚拟根 = 整份 manifest）。

    节点形状：{'size': int, 'count': int, 'children': {seg: {...}}}。
    full_name 为空的记录极少，归到根的下一级 seg = '未命名'。
    """
    root = {'size': 0, 'count': 0, 'children': {}}
    for rec in records:
        name = (rec.get('full_name') or '').strip() or '未命名'
        size = int(rec.get('size') or 0)
        node = root
        node['size'] += size
        node['count'] += 1
        for seg in name.split('/'):
            child = node['children'].get(seg)
            if child is None:
                child = node['children'][seg] = {'size': 0, 'count': 0, 'children': {}}
            child['size'] += size
            child['count'] += 1
            node = child
    return root


def children_of(node):
    """当前层 children 按 size 降序过滤空节点。"""
    items = [(s, c) for s, c in node.get('children', {}).items() if c.get('size', 0) > 0]
    items.sort(key=lambda kv: -kv[1]['size'])
    return items


def fmt_bytes(n):
    """人类可读字节数，1 位小数 + 单位。"""
    for unit, step in (('TB', 1024 ** 4), ('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024)):
        if n >= step:
            return f'{n / step:.1f} {unit}'
    return f'{n:,} B'


def elide(text, limit):
    """超过 limit 个字符就截断并以 '…' 提示（QPieSlice 的 label 没有自动省略能力）。"""
    limit = max(4, limit)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def label_color(base):
    """按感知亮度选白字还是黑字 —— QColor.lightness() 会把高饱和金黄判成"深色"配白字，糊。"""
    r, g, b, _ = base.getRgb()
    return QColor('#1a1a1a') if 0.299 * r + 0.587 * g + 0.114 * b > 140 else QColor('#ffffff')


# 手挑的 16 色高分离调色板（相邻色相跨度大，且明度交错），比 QChart 内置主题色对比强得多
_PALETTE = ['#e6194b', '#3cb44b', '#f5c40d', '#4363d8', '#f58231', '#911eb4', '#42d4f4',
            '#f032e6', '#9a9a16', '#469990', '#8b4513', '#dcbeff', '#000075', '#a9a9a9',
            '#800000', '#aaffc3']
_CONTEXT_COLOR = '#8d8d8d'      # 「其他目录」的灰色
_CONTEXT_SEG = '__context__'    # 上下文扇区的 property('seg') 取值，不参与下钻
_TAIL_COLOR = '#7a7a7a'         # 「本层其余 N 项」的聚合扇区色（斜纹，跟纯灰的上下文区分开）
_TAIL_SEG = '__tail__'          # 聚合扇区的 property('seg') 取值，不可下钻
_COLS = ['段名', '文件数', '总大小', '占整份']

# 扇区数量上限。实测 /off/one/effect_battle 这一层有 4517 个子项，全画成扇区的话
# QtCharts 光布局就要 ~1.9 秒（而且全是看不见的发丝）—— 只画最大的 _TOP_SLICES 个，
# 其余聚合成一片斜纹扇区（角度 = 它们的真实大小之和，不破坏"按真实比例画"这条约定）。
_TOP_SLICES = 12

# 标签预算：屏幕弧长不够就直接不画（这是"字多了太卡"的根治手段，见模块 docstring）
_MIN_ARC_PX = 40.0      # 扇区屏幕弧长 >= 这个值才配文字
_MAX_LABELS = 16        # 一次最多画几个标签，兜住极端情况
_RADIUS_RATIO = 0.36    # 饼图半径 ≈ 0.36 × min(视图宽, 视图高)，用于估算弧长/弦长
_HORIZONTAL_FRAC = 0.10  # 扇区占比 >= 这个值才横排标签，否则沿半径竖排
_CJK_PX = 1.05          # 一个段名字符的宽度 ≈ 字号 × 该系数（估）
_PCT_PX = 0.62          # 百分比那截是纯 ASCII，宽度系数更小


class _ZoomChartView(QChartView):
    """QChartView + 滚轮缩放：滚轮即缩放（不用按 Ctrl），双击重置。

    这里的 scale() 会把标签文字一起放大，字号恒定由 AssetsPanel._label_font() 反向抵消。
    """

    zoomed = Signal(float)      # 新倍数（1.0 ~ 8.0）

    MIN_ZOOM = 1.0
    MAX_ZOOM = 8.0

    def __init__(self, chart, parent=None):
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setTransformationAnchor(QChartView.AnchorUnderMouse)   # 缩放时鼠标下的点不动
        # 不用 ScrollHandDrag：它会吃掉左键，导致点击扇区下钻失效；放大后靠滚动条平移
        self.setDragMode(QChartView.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setStyleSheet('background: transparent;')
        self._zoom = 1.0

    def zoom_factor(self):
        return self._zoom

    def zoom_by(self, factor):
        new = min(self.MAX_ZOOM, max(self.MIN_ZOOM, self._zoom * factor))
        if abs(new - self._zoom) < 1e-9:
            return False
        self.scale(new / self._zoom, new / self._zoom)
        self._zoom = new
        self.zoomed.emit(new)
        return True

    def reset_zoom(self):
        if abs(self._zoom - 1.0) < 1e-9:
            return False
        self.resetTransform()
        self._zoom = 1.0
        self.zoomed.emit(1.0)
        return True

    def wheelEvent(self, ev):    # noqa: N802 —— Qt 命名
        dy = ev.angleDelta().y()
        if dy == 0:
            super().wheelEvent(ev)
            return
        self.zoom_by(1.2 if dy > 0 else 1 / 1.2)
        ev.accept()

    def mouseDoubleClickEvent(self, ev):    # noqa: N802
        self.reset_zoom()
        ev.accept()


class AssetsPanel(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.result = None
        self.root_node = None
        self.root_total = 0
        self._root_item = None      # 树顶那行「（整份 manifest）」，代表虚拟根
        self._tips = {}             # seg -> hover tooltip 的 html
        self._suppress_tree_signal = False
        self._build()
        self.set_result(None)
        self.state.manifest_loaded.connect(self.set_result)
        theme.notifier.changed.connect(self._on_theme_changed)

    # ---- 布局 ----

    def _build(self):
        self.banner = QLabel('（未载入 manifest）')
        self.banner.setObjectName('banner')
        self.banner.setWordWrap(True)

        self._chart = QChart()
        self._chart.setAnimationOptions(QChart.SeriesAnimations)
        self._chart.legend().setVisible(True)
        self._chart.legend().setAlignment(Qt.AlignRight)
        self._chart.setBackgroundBrush(Qt.NoBrush)
        self._chart.setPlotAreaBackgroundBrush(Qt.NoBrush)
        self._chart.setPlotAreaBackgroundVisible(False)
        self._chart_view = _ZoomChartView(self._chart)
        self._chart_view.zoomed.connect(self._on_zoomed)
        self._set_chart_theme()

        self.zoom_in_btn = QPushButton('放大')
        self.zoom_out_btn = QPushButton('缩小')
        self.zoom_reset_btn = QPushButton('重置缩放')
        self.zoom_in_btn.setToolTip('也可以直接在饼图上滚轮（双击重置）')
        self.zoom_out_btn.setToolTip('也可以直接在饼图上滚轮（双击重置）')
        self.zoom_in_btn.clicked.connect(lambda: self._chart_view.zoom_by(1.3))
        self.zoom_out_btn.clicked.connect(lambda: self._chart_view.zoom_by(1 / 1.3))
        self.zoom_reset_btn.clicked.connect(self._chart_view.reset_zoom)
        self.zoom_reset_btn.setEnabled(False)
        self.zoom_label = QLabel('1.0x')

        zoom_bar = QHBoxLayout()
        zoom_bar.setContentsMargins(0, 0, 0, 0)
        zoom_bar.addWidget(QLabel('饼图 '))
        zoom_bar.addWidget(self.zoom_in_btn)
        zoom_bar.addWidget(self.zoom_out_btn)
        zoom_bar.addWidget(self.zoom_reset_btn)
        zoom_bar.addWidget(self.zoom_label)
        zoom_bar.addWidget(QLabel('　滚轮缩放（字号不变）· 双击重置 · 放不下的段名以 … 收尾'
                                  '（悬停看全名）· 点灰色扇区回到上层 · 斜纹是聚合成的小项'))
        zoom_bar.addStretch(1)

        # 路径树
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(_COLS))
        self.tree.setHeaderLabels(_COLS)
        self.tree.setEditTriggers(QTreeWidget.NoEditTriggers)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(_COLS)):
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)

        chart_side = QWidget()
        chart_layout = QVBoxLayout(chart_side)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.addLayout(zoom_bar)
        chart_layout.addWidget(self._chart_view, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(chart_side)
        splitter.addWidget(self.tree)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([620, 380])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self.banner)
        layout.addWidget(splitter, 1)

    # ---- 数据源切换 ----

    def set_result(self, result):
        self.result = result
        if result is None:
            self.root_node = None
            self.root_total = 0
            self._root_item = None
            self.banner.setText('（未载入 manifest）')
            self.tree.clear()
            self._clear_chart()
            return
        self.root_node = build_size_tree(result.records)
        self.root_total = self.root_node['size'] or 1
        self.tree.clear()
        # 顶层放一行「整份 manifest」代表虚拟根：否则树的顶行是第一个目录，用户永远看不到根层饼图
        self._root_item = QTreeWidgetItem(self.tree)
        self._root_item.setText(0, '（整份 manifest）')
        self._root_item.setText(1, f'{self.root_node["count"]:,}')
        self._root_item.setText(2, fmt_bytes(self.root_node['size']))
        self._root_item.setText(3, '100.00%')
        self._root_item.setData(0, Qt.UserRole, [])
        self._fill_tree(self.root_node, self._root_item, path=[])
        self._suppress_tree_signal = True
        self.tree.setCurrentItem(self._root_item)
        self._root_item.setExpanded(True)
        self._suppress_tree_signal = False
        self._render_chart(self._node_of_selected())
        self._update_banner()

    def _fill_tree(self, node, parent_item, path):
        for seg, child in children_of(node):
            full_path = path + [seg]
            it = QTreeWidgetItem(parent_item)
            pct = child['size'] / self.root_total * 100 if self.root_total else 0
            it.setText(0, seg)
            it.setText(1, f'{child["count"]:,}')
            it.setText(2, fmt_bytes(child['size']))
            it.setText(3, f'{pct:.2f}%')
            it.setData(0, Qt.UserRole, full_path)
            self._fill_tree(child, it, full_path)

    # ---- 选中 / 渲染 ----

    def _node_at_path(self, path):
        node = self.root_node
        for seg in path:
            node = node['children'][seg]
        return node

    def _node_of_selected(self):
        sel = self.tree.currentItem()
        if sel is None or self.root_node is None:
            return self.root_node
        path = sel.data(0, Qt.UserRole) or []
        return self._node_at_path(path)

    def _on_tree_selection_changed(self):
        if self._suppress_tree_signal:
            return
        self._render_chart(self._node_of_selected())
        self._update_banner()

    def _update_banner(self):
        if self.root_node is None:
            self.banner.setText('（未载入 manifest）')
            return
        node = self._node_of_selected()
        sel = self.tree.currentItem()
        path = sel.data(0, Qt.UserRole) if sel else []
        label = '/' + '/'.join(path) if path else '（根）'
        pct = node['size'] / self.root_total * 100 if self.root_total else 0
        note = ('' if node is self.root_node
                else f'，其余 {100 - pct:.2f}% 是灰色「其他目录」，点它回到上层')
        self.banner.setText(
            f'当前层 <b>{label}</b>　文件数 {node["count"]:,}　'
            f'总大小 {fmt_bytes(node["size"])}　占整份 manifest <b>{pct:.2f}%</b>'
            f'　共 {len(children_of(node))} 个子项（饼图按整份比例画{note}）')

    def _clear_chart(self):
        while self._chart.series():
            self._chart.removeSeries(self._chart.series()[0])

    # ---- 标签预算：屏幕弧长够长才画字 ----

    def _screen_radius(self):
        """饼图在屏幕上的半径估计（QChart 的 plotArea 要首次绘制后才有效，这里按视图尺寸估）。"""
        side = min(self._chart_view.width() or 1, self._chart_view.height() or 1)
        return _RADIUS_RATIO * side * self._chart_view.zoom_factor()

    @staticmethod
    def _chars_that_fit(screen_r, frac, font_px, pct_txt):
        """这一扇区最多排得下几个段名字符 —— 横排看弦长，竖排（沿半径）看半径长度。"""
        if frac >= _HORIZONTAL_FRAC:
            room = 2 * screen_r * 0.62 * math.sin(math.pi * frac) - 12
        else:
            room = screen_r * 0.72 - 12
        room -= len(pct_txt) * font_px * _PCT_PX
        return int(room / (font_px * _CJK_PX))

    def _slice_brush(self, base):
        """亮→暗对角线性渐变；ObjectBoundingMode 让 0~1 落在扇区自身的包围盒上。"""
        grad = QLinearGradient(0.0, 0.0, 1.0, 1.0)
        grad.setCoordinateMode(QGradient.ObjectBoundingMode)
        grad.setColorAt(0.0, base.lighter(135))
        grad.setColorAt(1.0, base.darker(125))
        return QBrush(grad)

    def _render_chart(self, node, animate=True):
        """画 node 的 children（真实角区间）+ 灰色「其他目录」补足到 100%。"""
        self._clear_chart()
        if self.root_node is None:
            return
        items = children_of(node)
        if not items:
            return

        self._chart.setAnimationOptions(
            QChart.SeriesAnimations if animate else QChart.NoAnimation)

        font_px = self._label_font().pointSizeF() * 96.0 / 72.0     # pt -> px
        screen_r = self._screen_radius()
        # 只画最大的 _TOP_SLICES 个，其余同层子项聚合成一片（见 _TOP_SLICES 的说明）
        top, tail = items[:_TOP_SLICES], items[_TOP_SLICES:]
        self._tips = {}          # seg -> tooltip html（QPieSlice 没有 tooltip API，只能 hover 时手动弹）
        series = QPieSeries()
        shown = 0
        for i, (seg, child) in enumerate(top):
            base = QColor(_PALETTE[i % len(_PALETTE)])
            pct_global = child['size'] / self.root_total * 100 if self.root_total else 0
            pct_local = child['size'] / node['size'] * 100 if node['size'] else 0
            frac = child['size'] / node['size'] if node['size'] else 0
            pct_txt = f'{pct_global:.1f}%'
            # 屏幕弧长够长、且没超出标签总数上限，才画字；否则索性不画 —— 几十个标签
            # 让 QtCharts 做避让布局是"字多了太卡"的根源。放大后弧长变长，标签才逐个出现。
            arc = 2 * math.pi * screen_r * frac
            show = shown < _MAX_LABELS and arc >= _MIN_ARC_PX
            limit = self._chars_that_fit(screen_r, frac, font_px, pct_txt)
            label = f'{elide(seg, limit if show else 14)}  {pct_txt}'
            slice_ = QPieSlice(label, child['size'])
            slice_.setBrush(self._slice_brush(base))
            slice_.setPen(Qt.NoPen)
            slice_.setLabelVisible(show)
            slice_.setLabelColor(label_color(base))
            slice_.setLabelFont(self._label_font())
            slice_.setProperty('seg', seg)
            self._tips[seg] = (
                f'<b>{seg}</b><br>文件数 {child["count"]:,}<br>'
                f'大小 {fmt_bytes(child["size"])}<br>'
                f'占整份 manifest {pct_global:.2f}%<br>占当前层 {pct_local:.1f}%')
            # 标签一律放扇区内部（外置就是之前糊成一片的那张图），横排或沿半径竖排
            slice_.setLabelPosition(QPieSlice.LabelInsideHorizontal if frac >= _HORIZONTAL_FRAC
                                    else QPieSlice.LabelInsideNormal)
            slice_.setExplodeDistanceFactor(0.05)
            slice_.hovered.connect(lambda state, s=slice_: self._on_slice_hovered(s, state))
            slice_.clicked.connect(lambda seg=seg: self._drill(seg))
            series.append(slice_)
            shown += 1 if show else 0

        # 同层剩余子项的聚合扇区：角度 = 它们真实大小之和，所以饼图比例依然诚实；
        # 但它们太小、太细，逐个画只会拖慢渲染，具体是谁去左边的树里看。
        if tail:
            tail_size = sum(ch['size'] for _, ch in tail)
            tail_files = sum(ch['count'] for _, ch in tail)
            tail_frac = tail_size / node['size'] if node['size'] else 0
            tail_pct = tail_size / self.root_total * 100 if self.root_total else 0
            tail_txt = f'{tail_pct:.1f}%'
            tail_name = f'本层其余 {len(tail)} 项'
            tail_fit = self._chars_that_fit(screen_r, tail_frac, font_px, tail_txt)
            agg = QPieSlice(f'{elide(tail_name, tail_fit)}  {tail_txt}', tail_size)
            agg.setProperty('seg', _TAIL_SEG)
            agg.setBrush(QBrush(QColor(_TAIL_COLOR), Qt.BDiagPattern))   # 斜纹，跟纯灰的上下文区分
            agg.setPen(Qt.NoPen)
            agg.setLabelVisible(2 * math.pi * screen_r * tail_frac >= _MIN_ARC_PX)
            agg.setLabelFont(self._label_font())
            agg.setLabelColor(label_color(QColor(_TAIL_COLOR)))
            agg.setLabelPosition(QPieSlice.LabelInsideHorizontal if tail_frac >= _HORIZONTAL_FRAC
                                 else QPieSlice.LabelInsideNormal)
            self._tips[_TAIL_SEG] = (
                f'<b>{tail_name}</b>（斜纹）<br>含 {tail_files:,} 个文件<br>'
                f'大小 {fmt_bytes(tail_size)}<br>'
                f'占整份 manifest {tail_pct:.2f}%　占当前层 {tail_frac * 100:.1f}%<br>'
                f'<i>这些小项不单独画扇区，去左边的树里逐级查看</i>')
            series.append(agg)

        # 上下文扇区：把「不属于当前层」的部分补成 100%，饼图因此始终是整份 manifest 的真实比例
        rest = self.root_total - node['size']
        if rest > 0:
            rest_frac = rest / self.root_total
            rest_pct = rest_frac * 100
            rest_txt = f'{rest_pct:.1f}%'
            ctx_name = '其他目录（本层之外）'
            ctx_fit = self._chars_that_fit(screen_r, rest_frac, font_px, rest_txt)
            ctx = QPieSlice(f'{elide(ctx_name, ctx_fit)}  {rest_txt}', rest)
            ctx.setProperty('seg', _CONTEXT_SEG)
            ctx.setBrush(self._slice_brush(QColor(_CONTEXT_COLOR)))
            ctx.setPen(Qt.NoPen)
            ctx.setLabelVisible(2 * math.pi * screen_r * rest_frac >= _MIN_ARC_PX)
            ctx.setLabelFont(self._label_font())
            ctx.setLabelColor(label_color(QColor(_CONTEXT_COLOR)))
            ctx.setLabelPosition(QPieSlice.LabelInsideHorizontal if rest_frac >= _HORIZONTAL_FRAC
                                 else QPieSlice.LabelInsideNormal)
            self._tips[_CONTEXT_SEG] = (
                f'<b>{ctx_name}</b><br>大小 {fmt_bytes(rest)}<br>'
                f'占整份 manifest {rest_pct:.2f}%<br>'
                f'<i>灰色补齐到 100%，点击回到上一层</i>')
            ctx.clicked.connect(self._go_up)
            series.append(ctx)

        # 特意不挖环形（setHoleSize）：中心挖空在上一个版本被当成「黑洞」，实心更直观。
        # 子目录多导致的拥挤靠"只给放得下的扇区画字 + 放大"解决，不再外置标签。

        self._chart.addSeries(series)
        # 图例是没标签那些扇区的文字版；扇区一多它又占地方又挤小饼图，所以只在饼不挤时显示
        self._chart.legend().setVisible(series.count() <= _TOP_SLICES)
        self._chart.legend().setAlignment(Qt.AlignRight)
        self._chart.legend().setBackgroundVisible(False)
        self._chart.legend().setFont(self._label_font())

    def _label_font(self):
        """屏幕上恒定的标签字号：视图变换会放大 z 倍，这里先把字号除以 z 抵消掉。

        这就是「字不要跟着放大」—— 放大的是饼图本身，文字始终保持可读大小，
        于是放大后同一扇区能容纳更长的段名（见 _chars_that_fit）。
        """
        z = max(1.0, self._chart_view.zoom_factor())
        f = QFont(self.font())
        f.setPointSizeF(max(6.0, self.font().pointSizeF() - 1.5) / z)
        return f

    # ---- 交互 ----

    def _on_slice_hovered(self, slice_, state):
        slice_.setExploded(state)
        f = self._label_font()
        f.setBold(state)
        slice_.setLabelFont(f)
        if state:
            seg = slice_.property('seg')
            tip = self._tips.get(seg)
            if tip:
                QToolTip.showText(QCursor.pos(), tip, self._chart_view)
        else:
            QToolTip.hideText()

    def _drill(self, seg):
        """点击扇区 → 在树里选中对应子节点并展开，饼图跟着切到该层。"""
        cur = self._node_of_selected()
        if seg not in cur.get('children', {}):
            return
        sel = self.tree.currentItem()
        path = (sel.data(0, Qt.UserRole) if sel else []) + [seg]
        target = self._find_tree_item(path)
        if target is None:
            return
        self._suppress_tree_signal = True
        self.tree.setCurrentItem(target)
        target.setExpanded(True)
        self._suppress_tree_signal = False
        self._render_chart(self._node_of_selected())
        self._update_banner()

    def _go_up(self):
        """点灰色「其他目录」→ 在树里选中上一层并重画（已经在虚拟根行就停住）。"""
        sel = self.tree.currentItem()
        if sel is None or sel is self._root_item:
            return
        parent = sel.parent() or self._root_item
        self._suppress_tree_signal = True
        self.tree.setCurrentItem(parent)
        parent.setExpanded(True)
        self._suppress_tree_signal = False
        self._render_chart(self._node_of_selected(), animate=False)
        self._update_banner()

    def _find_tree_item(self, path):
        """按路径从「整份 manifest」行往下找 QTreeWidgetItem（空路径就是那一行本身）。"""
        cur = self._root_item
        if cur is None:
            return None
        for seg in path:
            found = None
            for i in range(cur.childCount()):
                ch = cur.child(i)
                if ch.text(0) == seg:
                    found = ch
                    break
            if found is None:
                return None
            cur = found
        return cur

    # ---- 缩放 / 主题 ----

    def _on_zoomed(self, zoom):
        self.zoom_label.setText(f'{zoom:.1f}x')
        self.zoom_reset_btn.setEnabled(zoom > 1.0)
        if self.root_node is not None:
            # 放大后允许更长的段名，重新渲染标签（不带动画，避免缩放时抖）
            self._render_chart(self._node_of_selected(), animate=False)

    def _set_chart_theme(self):
        """按 Windows 明暗选 QChart 内置主题；扇区配色我们全部自己 setBrush，主题只影响图例/文字底色。"""
        from PySide6.QtGui import QGuiApplication
        dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self._chart.setTheme(QChart.ChartThemeDark if dark else QChart.ChartThemeLight)
        self._chart.setBackgroundBrush(Qt.NoBrush)
        self._chart.setPlotAreaBackgroundVisible(False)

    def _on_theme_changed(self, *_arg):
        self._set_chart_theme()
        if self.root_node is not None:
            self._render_chart(self._node_of_selected(), animate=False)