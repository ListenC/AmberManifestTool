"""查询串 -> 谓词。

纯函数、不依赖 Qt，便于单独测试。查询串由空白分隔的 `field:value` 组成，
没有冒号或字段名无法识别时整体当作 bundle 名子串处理。

支持的字段：
    name / prefix / path / hash / filehash      子串匹配（不区分大小写）
    index / word / byte / pool / key            精确值（key 按 hex 前缀匹配）
    size / dep / index                          N、>N、>=N、<N、<=N、N-M
    enc:off|xor        按前缀池第 1 位标志位过滤
    granularity:one|dir|all   按前缀池第 2 位标志位过滤
    dup:key            只看键有重复（同键组大于 1 条）的记录

另外提供「存储路径反查 bundle 名」的纯函数：normalize_storage_path / lookup_storage_path，
以及支撑它的 RecordIndex.by_path / by_hash 精确索引（storage_path 与 assetBundleHash 实测全表唯一）。
"""

import operator
import re

# URL 的 scheme（http:// https:// file:// ...），用于把 CDN 链接剥成路径
_SCHEME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9+.\-]*://')
# 连续的十六进制串（>=32 位）：取它的末 32 位就是 assetBundleHash
_HEX_RUN_RE = re.compile(r'[0-9a-fA-F]{32,}')


class RecordIndex:
    """记录的检索上下文：把匹配要用到的派生量预先算好，避免谓词里反复做字符串拼接。"""

    def __init__(self, result):
        self.records = result.records
        n = len(self.records)
        self.name_lower = [None] * n
        self.path_lower = [None] * n
        self.prefix_lower = [None] * n
        self.enc = [None] * n
        self.granularity = [None] * n
        # 反查索引：storage_path / assetBundleHash -> 记录下标。两者实测都是 41886/41886 唯一，
        # 所以整条路径或裸文件名都能直接定位到唯一那条记录，不必扫表。
        self.by_path = {}
        self.by_hash = {}
        for i, rec in enumerate(self.records):
            self.name_lower[i] = (rec['full_name'] + ' ' + rec['name']).lower()
            path_low = self.path_lower[i] = rec['storage_path'].lower()
            prefix = rec['pool_prefix']
            self.prefix_lower[i] = prefix.lower()
            self.enc[i] = 'xor' if prefix.startswith('xor/') else 'off'
            rest = prefix[4:] if len(prefix) > 4 else ''
            self.granularity[i] = rest.split('/', 1)[0] if '/' in rest else rest
            self.by_path.setdefault(path_low, i)
            self.by_hash.setdefault(rec['hash_hex'].lower(), []).append(i)


_NUM_RE = re.compile(r'^(>=|<=|>|<)?(\d+)(?:-(\d+))?$')


def _to_int(text):
    text = text.strip().lower()
    return int(text, 16) if text.startswith('0x') else int(text)


def _num_check(value, getter):
    m = _NUM_RE.match(value)
    if not m:
        return None
    op, lo, hi = m.group(1), int(m.group(2)), m.group(3)
    if hi is not None:
        hi = int(hi)
        return lambda i: lo <= getter(i) <= hi
    if op is None:
        return lambda i: getter(i) == lo
    fn = {'>': operator.gt, '>=': operator.ge, '<': operator.lt, '<=': operator.le}[op]
    return lambda i: fn(getter(i), lo)


def _make_check(field, value, idx):
    """返回 (record_index -> bool) 或 None（字段无法识别，由调用方回退为名字子串）。"""
    low = value.lower()
    records = idx.records

    if field == 'name':
        return lambda i: low in idx.name_lower[i]
    if field == 'prefix':
        return lambda i: low in idx.prefix_lower[i]
    if field == 'path':
        return lambda i: low in idx.path_lower[i]
    if field == 'enc':
        return lambda i: idx.enc[i] == low
    if field == 'granularity':
        return lambda i: idx.granularity[i] == low
    if field == 'dup':
        return lambda i: records[i]['key_group_size'] > 1
    if field == 'key':
        key = value.lower()
        if key.startswith('0x'):
            key = key[2:]
        return lambda i: records[i]['key_hex'].startswith(key)
    if field == 'hash':
        return lambda i: low in records[i]['hash_hex']
    if field == 'filehash':
        return lambda i: low in records[i]['file_hash_hex']
    if field in ('index', 'word', 'byte', 'pool', 'size', 'dep'):
        getter = {
            'index': lambda i: records[i]['index'],
            'word': lambda i: records[i]['word'],
            'byte': lambda i: records[i]['byte'],
            'pool': lambda i: records[i]['name_pointer'] * 4,
            'size': lambda i: records[i]['size'],
            'dep': lambda i: records[i]['dependency_count'],
        }[field]
        if field in ('index', 'word', 'byte', 'pool'):
            # 也接受 0x 前缀的十六进制写法
            try:
                want = _to_int(value)
            except ValueError:
                return None
            return lambda i: getter(i) == want
        return _num_check(value, getter)
    return None


def tokenize(text):
    """把查询串拆成 [(field, value)]；无法识别字段的 token 归为 name。"""
    out = []
    for raw in text.split():
        if ':' in raw:
            field, _, value = raw.partition(':')
            field = field.lower()
            if field in ('name', 'prefix', 'path', 'hash', 'filehash', 'key', 'index', 'word',
                         'byte', 'pool', 'size', 'dep', 'enc', 'granularity', 'dup'):
                out.append((field, value))
                continue
        # 裸 token：当作 bundle 名子串
        out.append(('name', raw))
    return out


def build_predicate(text, idx):
    """返回 pred(record_index) -> bool；查询串为空时返回 None（表示不过滤）。"""
    checks = []
    for field, value in tokenize(text):
        if value == '':
            continue
        check = _make_check(field, value, idx)
        if check is None:
            check = _make_check('name', f'{field}:{value}', idx)
        checks.append(check)
    if not checks:
        return None
    return lambda i: all(c(i) for c in checks)


def describe(text):
    """把查询串转成人类可读的说明，用在状态栏。"""
    parts = []
    for field, value in tokenize(text):
        parts.append(f'{field}={value}')
    return '、'.join(parts)


def dependency_closure(records, rec_index_by_word, starts):
    """起点记录 + 其全部传递依赖，按构建顺序去重返回记录下标列表。

    依赖字段存的是另一条记录的起始 word，所以要先过 rec_index_by_word 反查下标；
    落不进任何记录起点的 word 直接跳过。用 visited 集合去重，循环依赖天然收敛。
    """
    visited = set()
    stack = list(starts)
    while stack:
        index = stack.pop()
        if index in visited:
            continue
        visited.add(index)
        for word in records[index]['dependencies']:
            dep = rec_index_by_word.get(word)
            if dep is not None and dep not in visited:
                stack.append(dep)
    return sorted(visited)


# ---------------------------------------------------------------- 存储路径反查
def normalize_storage_path(text):
    """把用户粘贴的各种形态剥成 storage_path 片段，剥不动就原样返回。

    storage_path 形如 `xx/` + 24 位 hex + 32 位 assetBundleHash（合计 59 字符，见
    manifest_format.md §9）。实际拿到的输入五花八门，这里统一成同一个表里的写法：
    * CDN 链接 / 本地绝对路径：去掉 scheme、host、?query、#fragment 与两侧斜杠
    * Windows 反斜杠路径：反斜杠统一成 /
    * 本工具导出的 `storage_path \\t bundle_name` 整行：只取第一列
    * 裸的 32 位 assetBundleHash（CDN 上真正落盘的文件名）：原样保留
    """
    text = (text or '').strip().strip('"\'').strip()
    if not text:
        return ''
    parts = text.split()
    if len(parts) > 1:                    # 粘贴导出的整行：`路径　bundle 名`
        text = parts[0]
    text = text.replace('\\', '/')
    text = text.split('#', 1)[0].split('?', 1)[0]
    if _SCHEME_RE.match(text):
        rest = text.split('://', 1)[1]
        text = rest.split('/', 1)[1] if '/' in rest else ''
    return text.strip('/')


def lookup_storage_path(text, idx):
    """按存储路径反查记录，返回 (kind, [记录下标...])，kind ∈ full | hash | sub | none。

    三级匹配，越靠前越可信：
    * full —— 整条 59 字符 storage_path 精确命中（by_path 字典，O(1)）
    * hash —— 从输入里认出末 32 位 assetBundleHash 再按它命中（by_hash 字典，O(1)）。
      CDN 上真正落盘的文件名就是这个 hash，所以无论前面挂了多长的本地目录、
      域名或 `.bundle` 后缀，只要尾部是它就能反查到 bundle 名。
    * sub  —— 上面都没命中才退化成子串扫描，支持只记得中间片段的情况。
    """
    norm = normalize_storage_path(text)
    if not norm:
        return 'none', []
    low = norm.lower()

    hit = idx.by_path.get(low)
    if hit is not None:
        return 'full', [hit]

    # 取最后一段十六进制串的末 32 位：整条 59 字符路径去掉 `xx/` 后是 56 位长串，
    # 它的末 32 位正好是 assetBundleHash，所以这里不能简单取前 32 位。
    runs = _HEX_RUN_RE.findall(norm)
    if runs:
        hits = idx.by_hash.get(runs[-1][-32:].lower())
        if hits:
            return 'hash', list(hits)

    hits = [i for i, path in enumerate(idx.path_lower) if low in path]
    return ('sub', hits) if hits else ('none', [])