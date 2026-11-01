"""主窗口：工具栏、4 个 Tab、状态栏、后台载入与拖拽。

只负责"打开文件 -> 后台解析 -> 广播结果"这条主线；面板之间不互相 import，
跨面板导航一律经 AppState 的信号。
"""

import json
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QFileDialog, QLabel, QMainWindow, QMessageBox,
                               QProgressBar, QTabWidget)

from hashcode import BUILTIN_STREAMS, DEFAULT_STREAM, STREAMS, register_stream
from . import APP_NAME, __version__
from .loader import LoadWorker
from .panel_assets import AssetsPanel
from .panel_keytable import KeyTablePanel
from .panel_overview import OverviewPanel
from .panel_pool import PoolPanel
from .panel_records import RecordsPanel
from .state import AppState
from .stream_dialog import StreamDialog

CUSTOM_STREAMS_KEY = 'streams/custom'   # QSettings：本机保存的自定义扰动流（json）


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = AppState(self)
        self.worker = None
        self._current_path = ''
        self._parsed_table_id = None   # 上次解析用的扰动流表 id，见 _refresh_stream_combo

        self.setWindowTitle(APP_NAME)
        self.resize(1480, 920)
        self.setAcceptDrops(True)
        self._build()

        self.state.manifest_loading.connect(self._on_loading)
        self.state.manifest_progress.connect(self._on_progress)
        self.state.manifest_loaded.connect(self._on_loaded)
        self.state.manifest_failed.connect(self._on_failed)
        self.state.goto_record.connect(self._on_goto_record)
        self.state.apply_query.connect(self._on_apply_query)

    # ---------------------------------------------------------------- 界面
    def _build(self):
        open_action = QAction('打开 manifest 文件…', self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_dialog)

        reload_action = QAction('重新解析', self)
        reload_action.setShortcut('Ctrl+R')
        reload_action.triggered.connect(self.reload)

        about_action = QAction('关于', self)
        about_action.triggered.connect(self.show_about)

        toolbar = self.addToolBar('主工具栏')
        toolbar.setMovable(False)
        toolbar.addAction(open_action)
        toolbar.addAction(reload_action)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel('游戏 '))
        # storage_path 前缀由 256 字节扰动流决定，而这张表是分游戏的，所以必须让用户选
        self.stream_combo = QComboBox()
        self.stream_combo.setToolTip(
            'storage_path 前缀用的 256 字节扰动流（分游戏，切换后自动重新解析）：\n'
            '幻想收束 —— 表 keyIF（默认）\n'
            'Engage Kill —— 表 keyEK\n'
            '其他同框架游戏用「自定义游戏…」注册。\n'
            '只影响 storage_path，bundle 名与其余字段不受影响。')
        self.stream_combo.currentIndexChanged.connect(self._on_stream_changed)
        toolbar.addWidget(self.stream_combo)

        custom_action = QAction('自定义游戏…', self)
        custom_action.setToolTip('注册/删除扰动流（支持粘贴十六进制或从文件导入 256 字节表，保存在本机）')
        custom_action.triggered.connect(self.edit_streams)
        toolbar.addAction(custom_action)
        toolbar.addSeparator()
        toolbar.addAction(about_action)
        self.reload_action = reload_action
        reload_action.setEnabled(False)

        self._load_custom_streams()
        self._refresh_stream_combo()

        self.overview_panel = OverviewPanel(self.state, self)
        self.assets_panel = AssetsPanel(self.state, self)
        self.records_panel = RecordsPanel(self.state, self)
        self.keytable_panel = KeyTablePanel(self.state, self)
        self.pool_panel = PoolPanel(self.state, self)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.overview_panel, '概览')
        self.tabs.addTab(self.assets_panel, '资源视图')
        self.tabs.addTab(self.records_panel, '记录（构建顺序）')
        self.tabs.addTab(self.keytable_panel, '键表（键序）')
        self.tabs.addTab(self.pool_panel, '名字前缀池')
        self.setCentralWidget(self.tabs)

        self.status_label = QLabel('就绪：用「打开 manifest 文件…」选择资产文件，或直接把文件拖进窗口')
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.verdict_label = QLabel('')
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.verdict_label)

    # ---------------------------------------------------------------- 打开与载入
    def open_dialog(self):
        start = str(Path(self._current_path).parent) if self._current_path else ''
        # manifest 没有扩展名，默认过滤器必须是「所有文件 (*)」
        path, _filter = QFileDialog.getOpenFileName(
            self, '选择 manifest 文件（通常是 AssetBundle，无扩展名）', start, '所有文件 (*)')
        if path:
            self.load_path(path)

    def load_path(self, path):
        path = str(path)
        if not Path(path).is_file():
            QMessageBox.warning(self, APP_NAME, f'文件不存在：\n{path}')
            return
        self._stop_worker()
        self._current_path = path
        self.state.result = None
        self.state.manifest_loading.emit(path)

        stream = self.stream_combo.currentData()
        self._parsed_table_id = id(STREAMS[stream]['table'])   # 记住本次算 storage_path 用的是哪张表
        self.worker = LoadWorker(path, stream, self)
        # worker 只把结果交给 AppState，再由 AppState 广播给界面，避免信号回环
        self.worker.progress.connect(self.state.manifest_progress)
        self.worker.loaded.connect(self._on_worker_loaded)
        self.worker.failed.connect(self.state.manifest_failed)
        self.worker.start()

    def reload(self):
        if self._current_path:
            self.load_path(self._current_path)

    def _on_stream_changed(self, _index):
        """游戏换掉后 storage_path 全部作废，重新解析当前文件（未载入文件时是空操作）。"""
        self.reload()

    # ---------------------------------------------------------------- 扰动流（游戏）
    def _refresh_stream_combo(self):
        """按当前 STREAMS 重建下拉框，尽量保持选中项；选中的游戏没了就回到默认。

        换游戏、或当前游戏的表被「覆盖」过（`register_stream` 会换一个新的 list 对象，
        所以比 `id()` 就能发现）都要重解析 —— storage_path 依赖这张表。
        """
        previous = self.stream_combo.currentData()
        self.stream_combo.blockSignals(True)
        self.stream_combo.clear()
        for name, entry in STREAMS.items():
            self.stream_combo.addItem(entry['label'], name)
        keys = list(STREAMS)
        index = keys.index(previous) if previous in keys else keys.index(DEFAULT_STREAM)
        self.stream_combo.setCurrentIndex(index)
        self.stream_combo.blockSignals(False)
        chosen = self.stream_combo.currentData()
        if chosen != previous or id(STREAMS[chosen]['table']) != self._parsed_table_id:
            self.reload()

    def _load_custom_streams(self):
        """把本机保存的自定义游戏注册回 hashcode。配置里的坏条目直接跳过，不影响启动。"""
        try:
            saved = json.loads(QSettings().value(CUSTOM_STREAMS_KEY, '') or '[]')
        except ValueError:
            return
        for item in saved if isinstance(saved, list) else []:
            try:
                register_stream(item['name'], item['table'], item.get('label'))
            except (KeyError, TypeError, ValueError):
                continue

    def _save_custom_streams(self):
        """自定义游戏落盘到 QSettings（内置的不存）。"""
        data = [{'name': n, 'label': e['label'], 'table': e['table']}
                for n, e in STREAMS.items() if n not in BUILTIN_STREAMS]
        QSettings().setValue(CUSTOM_STREAMS_KEY, json.dumps(data))

    def edit_streams(self):
        """打开自定义游戏对话框；关掉后一律落盘并刷新下拉框。

        对话框里的删除是即时的（所以无论确定/取消都要落盘），且可能覆盖了当前游戏的表，
        因此刷新时按表 id 判断是否需要重解析。
        """
        StreamDialog(self).exec()
        self._save_custom_streams()
        self._refresh_stream_combo()

    def _stop_worker(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        self.worker = None

    # ---------------------------------------------------------------- 状态回调
    def _on_loading(self, path):
        self.reload_action.setEnabled(False)
        self.status_label.setText(f'正在解析 {path} …')
        self.verdict_label.setText('')
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)

    def _on_progress(self, done, total, message):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self.progress.setFormat(f'{done:,} / {total:,}')
            self.status_label.setText(f'{message}…')
        else:
            self.status_label.setText(f'{message}…')

    def _on_worker_loaded(self, result):
        """后台解析的唯一出口：先把结果落到 state，再广播给各面板与界面。"""
        self.state.result = result
        self.state.manifest_loaded.emit(result)

    def _on_loaded(self, result):
        self.progress.setVisible(False)
        self.reload_action.setEnabled(True)
        name = Path(result.source).name
        self.setWindowTitle(f'{name} — {APP_NAME}')

        st = result.stats
        self.status_label.setText(
            f'{result.source}　·　游戏 {self.stream_combo.currentText()}　·　'
            f'记录 {result.file_count:,} 条　·　不同键 {st["distinct_keys"]:,} 个　·　'
            f'xor {st["enc_counts"]["xor"]:,} / off {st["enc_counts"]["off"]:,}　·　'
            f'size 合计 {st["size_total"]:,} B（约 {st["size_total_mb"]:,} MB）　·　'
            f'前缀池 {st["pool_count"]:,} 条　·　.dat {st["datas_bytes"]:,} B')

        passed = sum(1 for c in result.checks if c['ok'])
        failed = [c for c in result.checks if not c['ok']]
        self.verdict_label.setText(f'自校验 {passed}/{len(result.checks)} 通过' if not failed
                                   else f'自校验 {passed}/{len(result.checks)}：{failed[0]["name"]} 不通过')

    def _on_failed(self, message):
        self.progress.setVisible(False)
        self.reload_action.setEnabled(True)
        if message == '已取消':
            self.status_label.setText('已取消')
            return
        self.status_label.setText('解析失败')
        QMessageBox.critical(self, APP_NAME,
                             f'无法解析该文件：\n{message}\n\n'
                             '请确认它是本游戏的 manifest 资产（AssetBundle，内部含 '
                             'Amber.AssetBundleManifestInfo）。')

    # ---------------------------------------------------------------- 跨 Tab 联动
    def _on_goto_record(self, _index):
        self.tabs.setCurrentWidget(self.records_panel)

    def _on_apply_query(self, _text):
        self.tabs.setCurrentWidget(self.records_panel)

    # ---------------------------------------------------------------- 拖拽
    def dragEnterEvent(self, event):      # noqa: N802 —— Qt 命名
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):           # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and Path(path).is_file():
                self.load_path(path)
                break

    def closeEvent(self, event):          # noqa: N802
        self._stop_worker()
        super().closeEvent(event)

    # ---------------------------------------------------------------- 关于
    def show_about(self):
        QMessageBox.about(self, f'关于 {APP_NAME}', f"""
<h3>{APP_NAME}<span style="font-weight:normal"> v{__version__}</span></h3>
<p>用于查看《魔法禁书目录 幻想收束》Amber manifest
（<code>Amber.AssetBundleManifestInfo</code>）的桌面工具。</p>
<ul>
  <li>概览：文件信息、段布局、表头字段、容器对象、自校验、统计</li>
  <li>记录（构建顺序）：全部字段 + 依赖正反查 + 原始 word 十六进制 dump</li>
  <li>键表（键序）：nameHash 升序键表与 record_word 反查</li>
  <li>名字前缀池：共享目录前缀、标志位、引用记录数</li>
</ul>
<p>二进制布局与三套哈希的依据见项目内 <code>manifest_format.md</code>。</p>
<p><b>游戏选择</b>：工具栏的「游戏」下拉框决定 <code>storage_path</code> 前缀用哪张 256 字节扰动流
（分游戏，默认「幻想收束」）；切换后会重新解析。其他同框架游戏用「自定义游戏…」注册 ——
可粘贴十六进制文本或导入 256 字节表文件，保存在本机设置里，下次启动仍可选。</p>
<p><b>当前版本为只读</b>：只解析与展示，不修改、不写盘。</p>
""")