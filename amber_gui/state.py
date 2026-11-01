"""跨面板导航与当前状态。

各面板不互相 import，只通过 AppState 的信号通信，避免面板之间形成网状依赖。
"""

from PySide6.QtCore import QObject, Signal


class AppState(QObject):
    """持有当前 ManifestResult，并广播跨面板的定位/过滤请求。"""

    manifest_loading = Signal(str)        # 路径：开始解析
    manifest_progress = Signal(int, int, str)   # done, total, message
    manifest_loaded = Signal(object)      # ManifestResult
    manifest_failed = Signal(str)         # 错误文本

    goto_record = Signal(int)             # 请求记录面板定位到某条记录（records 下标）
    apply_query = Signal(str)             # 请求记录面板套用一条查询串

    def __init__(self, parent=None):
        super().__init__(parent)
        self.result = None

    # ---------- 供面板直接读取的便捷属性 ----------
    @property
    def has_manifest(self):
        return self.result is not None

    @property
    def records(self):
        return self.result.records if self.result else []

    @property
    def file_count(self):
        return self.result.file_count if self.result else 0