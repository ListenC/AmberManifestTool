"""后台解析线程。

UnityPy 打开容器 + 全量解析（含 41886 次 getFrontPath 与 GetNameHash）需要数秒，
必须离开 UI 线程；进度与取消都通过 manifest_core.parse_datas 的 progress 回调实现。
"""

from PySide6.QtCore import QThread, Signal

from hashcode import set_stream
from manifest_container import read_container
from manifest_core import parse_datas, parse_header


class LoadCancelled(Exception):
    """用户在解析过程中取消。"""


class LoadWorker(QThread):
    progress = Signal(int, int, str)
    loaded = Signal(object)     # ManifestResult
    failed = Signal(str)

    def __init__(self, path, stream, parent=None):
        super().__init__(parent)
        self.path = path
        self.stream = stream        # 游戏/扰动流的键名：'ek' / 'if' / 自定义游戏
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _on_progress(self, done, total, message):
        if self._cancel:
            raise LoadCancelled()
        self.progress.emit(done, total, message)

    def run(self):
        try:
            # 在解析线程里切换扰动流：storage_path 依赖它，且必须与本次解析同线程发生，
            # 否则 UI 线程改全局 key 会踩到正在跑的解析。
            set_stream(self.stream)
            info = read_container(self.path)
            if self._cancel:
                raise LoadCancelled()
            header = parse_header(info.datas)
            result = parse_datas(
                info.datas,
                source=self.path,
                manifest_version=info.manifest_version,
                asset_bundle_name=info.asset_bundle_name,
                mono_tree=info.mono_tree,
                script_tree=info.script_tree,
                bundle_tree=info.bundle_tree,
                header=header,
                progress=self._on_progress,
            )
            # 原始文件大小与容器对象清单不在 core 的职责里，这里补进 result 供概览展示
            result.stats['file_size'] = info.file_size
            result.stats['source_path'] = info.path
            result.stats['object_types'] = list(info.object_types)
            self.loaded.emit(result)
        except LoadCancelled:
            self.failed.emit('已取消')
        except Exception as exc:                      # noqa: BLE001 —— 线程边界必须兜住所有异常
            self.failed.emit(f'{type(exc).__name__}: {exc}')