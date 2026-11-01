"""Amber manifest 纯解析层。

职责：把 `AssetBundleManifestInfo.data.datas` 还原出的字节流解析成 `ManifestResult`。
本模块**不 import UnityPy、不 import Qt、不打印、不写盘**，因此可以被
`main.py`（批量 CLI 导出）与 `amber_gui`（可视化工具）共用，
保证这套二进制格式只有一份实现（字段布局与算法依据见 manifest_format.md）。
"""

from dataclasses import dataclass, field

from hashcode import GetNameHash, HashFromUInts, getFrontPath


def myhex(n):
    return "".join(f"0x{n:08x}")


def props_hash_hex(h):
    """MonoScript.m_PropertiesHash：UnityPy 读出来是 {'bytes[0]'..'bytes[15]': int} 的字典。"""
    if isinstance(h, dict):
        return ''.join('%02x' % (int(h['bytes[%d]' % i]) & 0xFF) for i in range(16))
    return bytes(h).hex() if h else ''


# 名字前缀池的头两位标志位（由 stringliteral.json 中 "off/one/..."、"xor/dir/..." 字面量坐实）
namef1 = {
    'O': 'off/',   # 第 1 位：是否 xor 加密
    'X': 'xor/'
}

namef2 = {
    'O': 'one/',   # 第 2 位：打包方式
    'D': 'dir/',
    'A': 'all/'
}

# ---------- Amber.AssetBundleData（整体表头，单位：word = 4 字节小端 u32） ----------
OFFSET_DATA_HASH = 0        # 整表一致性哈希
OFFSET_FORMAT_VERSION = 1   # 固定等于 AssetBundleData.FormatVersion = 1
OFFSET_FILE_COUNT = 2       # 记录数
OFFSET_STRING_INDEX = 3     # 名字前缀池起始 word
OFFSET_NAME_HASH = 4        # 升序哈希键表起点，紧随其后是等长的“键 -> 记录偏移”表

# ---------- Amber.AssetBundleData.File（单条记录内部，单位：word，相对记录起始） ----------
OFFSET_BUNDLE_HASH = 0      # 4 word：assetBundleHash(Int4)，即存储 / CDN 文件名用的 128 位哈希
OFFSET_FILE_HASH = 4        # 4 word：fileHash(Int4)，下载后 Verify(filepath, bytes) 校验用
OFFSET_FILE_SIZE = 8        # 文件大小
OFFSET_DEPENDENCY_COUNT = 9
OFFSET_DEPENDENCIES = 10    # 每个依赖是另一条记录起始的 word 偏移（GetDependency(index)）

FORMAT_VERSION = 1


def u32(buf, word):
    """按 word 编号读一个小端 u32。"""
    return int.from_bytes(buf[word * 4: word * 4 + 4], byteorder='little', signed=False)


def word_step(name_len):
    """名字（1 字节长度 + name_len 字节内容）按 4 字节对齐后占用的 word 数。"""
    return (name_len + 1 + 3) // 4


def _tick(progress, done, total, message):
    if progress is not None:
        progress(done, total, message)


@dataclass
class Header:
    """表头 4 个 word 与各段边界。word 为单位，byte = word * 4。"""
    data_hash: int
    format_version: int
    file_count: int
    string_index: int

    @property
    def data_hash_hex(self):
        return myhex(self.data_hash)

    @property
    def name_hash_table_word(self):
        return OFFSET_NAME_HASH

    @property
    def record_offset_table_word(self):
        return OFFSET_NAME_HASH + self.file_count

    @property
    def record_area_start_word(self):
        return OFFSET_NAME_HASH + self.file_count * 2

    @property
    def name_prefix_pool_word(self):
        return self.string_index

    def as_dict(self, datas_len):
        return {
            'data_hash': self.data_hash_hex,
            'format_version': self.format_version,
            'file_count': self.file_count,
            'string_index_word': self.string_index,
            'string_index_byte': self.string_index * 4,
            'name_hash_table_word': self.name_hash_table_word,
            'record_offset_table_word': self.record_offset_table_word,
            'record_area_start_word': self.record_area_start_word,
            'record_area_start_byte': self.record_area_start_word * 4,
            'record_area_end_byte': self.string_index * 4,
            'name_prefix_pool_bytes': datas_len - self.string_index * 4,
        }


@dataclass
class ManifestResult:
    """一份 manifest 的全部解析结果（无损原文 + 派生量 + 反查索引 + 自检）。"""
    source: str
    datas: bytes
    header: Header
    manifest_version: str = ''
    asset_bundle_name: str = ''
    mono_tree: dict = field(default_factory=dict)
    script_tree: dict = field(default_factory=dict)
    bundle_tree: dict = field(default_factory=dict)

    name_hashes: list = field(default_factory=list)     # 键表，磁盘顺序（键序）
    record_offsets: list = field(default_factory=list)  # 偏移表，与键表同序
    records: list = field(default_factory=list)         # 记录区，磁盘顺序（构建顺序）
    namefront: dict = field(default_factory=dict)       # 前缀池：byte 偏移 -> 展开后的目录前缀
    pool_raw: list = field(default_factory=list)        # 前缀池原始条目（含未展开的 2 字符标志位）

    # 键表分组（键大量重复，供"同键组"展示）
    key_group_no: list = field(default_factory=list)
    key_pos_in_group: list = field(default_factory=list)
    key_group_size: list = field(default_factory=list)

    # 反查索引
    word2rec: dict = field(default_factory=dict)          # 记录 word -> 记录
    rec_index_by_word: dict = field(default_factory=dict)  # 记录 word -> records 下标
    key_order_by_word: dict = field(default_factory=dict)  # 记录 word -> 键表下标
    name_index: dict = field(default_factory=dict)         # 完整名 -> records 下标
    dependents: dict = field(default_factory=dict)         # 记录 word -> 依赖它的记录下标列表

    stats: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)   # [{id, name, ok, actual, expected}]

    # 自校验的原始证据（供 CLI 复现原有的 assert 与提示文本）
    bad_keys: list = field(default_factory=list)           # 键重算不符的完整名
    file_hash_violations: list = field(default_factory=list)
    data_hash_recomputed: int = 0

    # ---------- 便捷视图 ----------
    @property
    def file_count(self):
        return self.header.file_count

    @property
    def string_index(self):
        return self.header.string_index

    def record(self, index):
        return self.records[index]

    def pool_ref_count(self):
        """byte 偏移 -> 被记录引用的次数。"""
        used = {}
        for rec in self.records:
            b = rec['name_pointer'] * 4
            used[b] = used.get(b, 0) + 1
        return used


def parse_header(datas):
    return Header(
        data_hash=u32(datas, OFFSET_DATA_HASH),
        format_version=u32(datas, OFFSET_FORMAT_VERSION),
        file_count=u32(datas, OFFSET_FILE_COUNT),
        string_index=u32(datas, OFFSET_STRING_INDEX),
    )


def parse_datas(datas, *, source, manifest_version='', asset_bundle_name='',
                mono_tree=None, script_tree=None, bundle_tree=None,
                header=None, progress=None):
    """把 datas 字节流解析成 ManifestResult。

    header 可由调用方先行解析并断言过格式版本，避免重复读表头。
    progress(done, total, message) 会分阶段汇报进度，供 GUI 显示；CLI 传 None。
    """
    if header is None:
        header = parse_header(datas)
    file_count = header.file_count
    string_index = header.string_index

    # ---------------- 两张索引表 ----------------
    # 键表升序，是 bundle 名哈希；偏移表按同一顺序给出对应记录的 word 偏移
    _tick(progress, 0, 1, '读取索引表')
    name_hashes = [u32(datas, OFFSET_NAME_HASH + i) for i in range(file_count)]
    record_offsets = [u32(datas, OFFSET_NAME_HASH + file_count + i) for i in range(file_count)]
    word2key = {record_offsets[i]: name_hashes[i] for i in range(file_count)}

    # ---------------- 记录区 ----------------
    # 记录自身是构建顺序，不是键序，顺序由 record_offsets 反查
    records = []
    p = header.record_area_start_word
    size_all = 0
    for i in range(file_count):
        start = p
        rec = {
            'index': i,
            'word': p,
            'byte': p * 4,
            'asset_bundle_hash': [u32(datas, p + OFFSET_BUNDLE_HASH + k) for k in range(4)],
            'file_hash': [u32(datas, p + OFFSET_FILE_HASH + k) for k in range(4)],
            'size': u32(datas, p + OFFSET_FILE_SIZE),
            'dependency_count': u32(datas, p + OFFSET_DEPENDENCY_COUNT),
        }
        rec['dependencies'] = [u32(datas, p + OFFSET_DEPENDENCIES + k)
                               for k in range(rec['dependency_count'])]
        name_word = p + OFFSET_DEPENDENCIES + rec['dependency_count']   # namePointer
        rec['name_word'] = name_word
        rec['name_pointer'] = u32(datas, name_word)
        name_len = datas[(name_word + 1) * 4]
        rec['name_len'] = name_len
        rec['name'] = datas[(name_word + 1) * 4 + 1: (name_word + 1) * 4 + 1 + name_len].decode('utf-8')
        size_all += rec['size']
        records.append(rec)
        p = name_word + 1 + word_step(name_len)
        rec['word_len'] = p - start
        if progress is not None and (i & 2047) == 0:
            progress(i, file_count, '解析记录区')

    record_area_end = p

    # ---------------- 名字前缀池 ----------------
    # 池里存的是共享的目录前缀（去重），记录内只存文件名剩下的部分
    namefront = {}
    pool_raw = []
    p = string_index
    while p * 4 < len(datas):
        name_len = datas[p * 4]
        raw = datas[p * 4 + 1: p * 4 + 1 + name_len]
        filenamestr = raw.decode('utf-8')
        prefix = namef1[filenamestr[0]] + namef2[filenamestr[1]] + filenamestr[2:]
        namefront[p * 4] = prefix
        pool_raw.append({
            'byte': p * 4,
            'word': p,
            'raw_hex': ''.join('%02x' % b for b in raw),
            'raw_str': filenamestr,
            'flag1': filenamestr[0],
            'flag2': filenamestr[1],
            'prefix': prefix,
        })
        p += word_step(name_len)
        if progress is not None and (len(pool_raw) & 255) == 0:
            progress(len(pool_raw), 0, '解析名字前缀池')

    # 完整 bundle 名 / 存储路径 / 键重算：一遍扫完
    for i, rec in enumerate(records):
        rec['full_name'] = namefront.get(rec['name_pointer'] * 4, '') + rec['name']
        rec['key'] = word2key[rec['word']]
        rec['hash_hex'] = ''.join('%08x' % x for x in rec['asset_bundle_hash'])
        # Int4 的 4 个 int32 按 x/y/z/w 原序拼成 32 字符 hex，即该 128 位值的规范写法
        rec['file_hash_hex'] = ''.join('%08x' % x for x in rec['file_hash'])
        rec['storage_path'] = getFrontPath(rec['full_name']) + rec['hash_hex']
        rec['key_order'] = 0     # 下面按键表回填
        if progress is not None and (i & 2047) == 0:
            progress(i, file_count, '构造存储路径')

    # ---------------- 反查索引 ----------------
    word2rec = {rec['word']: rec for rec in records}
    rec_index_by_word = {rec['word']: rec['index'] for rec in records}
    key_order_by_word = {record_offsets[i]: i for i in range(file_count)}
    name_index = {rec['full_name']: rec['index'] for rec in records}
    for rec in records:
        rec['key_order'] = key_order_by_word[rec['word']]
        rec['key_hex'] = '%08x' % rec['key']
        rec['pool_prefix'] = namefront.get(rec['name_pointer'] * 4, '')
        rec['key_group_size'] = 0     # 下面按键表分组回填
        rec['dependencies_resolved'] = [word2rec[x]['full_name'] if x in word2rec else None
                                        for x in rec['dependencies']]
    dependents = {}
    for rec in records:
        for d in rec['dependencies']:
            dependents.setdefault(d, []).append(rec['index'])

    # 键表分组：连续相同键为一组（键表本身非递减，故同键必然连续）
    key_group_no = [0] * file_count
    key_pos_in_group = [0] * file_count
    key_group_size = [0] * file_count
    gno = -1
    i = 0
    while i < file_count:
        j = i
        while j + 1 < file_count and name_hashes[j + 1] == name_hashes[i]:
            j += 1
        gno += 1
        size = j - i + 1
        for k in range(i, j + 1):
            key_group_no[k] = gno
            key_pos_in_group[k] = k - i
            key_group_size[k] = size
        i = j + 1
    for rec in records:
        rec['key_group_size'] = key_group_size[rec['key_order']]

    # ---------------- 自校验：用 §13 反汇编出的算法重算，逐项与原表比对 ----------------
    # 1) 键表 = XOR16(长名)，见 §13.1
    _tick(progress, 0, file_count, '校验 nameHash 键')
    bad_keys = []
    for i, rec in enumerate(records):
        if GetNameHash(rec['full_name']) != rec['key']:
            bad_keys.append(rec['full_name'])
        if progress is not None and (i & 2047) == 0:
            progress(i, file_count, '校验 nameHash 键')

    # 2) dataHash = 整表 128 位哈希的最低 32 位（word 0 参与计算时按 0 计），见 §13.2
    _tick(progress, 0, 1, '校验 dataHash')
    words = [u32(datas, i) for i in range(len(datas) // 4)]
    words[OFFSET_DATA_HASH] = 0
    data_hash_recomputed = HashFromUInts(words)[0]

    # 3) fileHash = HashFromBytes(bundle 文件内容)，见 §13.3。本地没有 bundle 本体无法直接重算，
    #    只能校验必要条件的量级上界：累加器 <= 255*ceil(size/16)，分量是两个累加器相加再异或。
    file_hash_violations = []
    for rec in records:
        bound = 1 << (2 * 255 * ((rec['size'] + 15) // 16)).bit_length()
        if not all(x < bound for x in rec['file_hash']):
            file_hash_violations.append(rec['full_name'])

    # ---------------- 汇总：统计与校验清单（供 GUI 直接展示） ----------------
    used = {}
    sample_by_byte = {}
    for rec in records:
        b = rec['name_pointer'] * 4
        used[b] = used.get(b, 0) + 1
        if b not in sample_by_byte:
            sample_by_byte[b] = rec['full_name']
    for entry in pool_raw:
        entry['ref_count'] = used.get(entry['byte'], 0)
        entry['sample_name'] = sample_by_byte.get(entry['byte'], '')

    distinct_keys = len(set(name_hashes))
    enc_counts = {'xor': 0, 'off': 0}
    grain_counts = {'one': 0, 'dir': 0, 'all': 0}
    for rec in records:
        prefix = namefront.get(rec['name_pointer'] * 4, '')
        if prefix.startswith('xor/'):
            enc_counts['xor'] += 1
        elif prefix.startswith('off/'):
            enc_counts['off'] += 1
        for g in grain_counts:
            if prefix.startswith(('xor/' + g + '/', 'off/' + g + '/')):
                grain_counts[g] += 1
                break
    deps_counts = [r['dependency_count'] for r in records]
    path_lens = [len(r['storage_path']) for r in records]

    stats = {
        'datas_bytes': len(datas),
        'datas_words': len(datas) // 4,
        'size_total': size_all,
        'size_total_mb': round(size_all / 1024 / 1024, 1),
        'index_tables_bytes': (OFFSET_NAME_HASH + file_count * 2) * 4,
        'record_area_bytes': (record_area_end - header.record_area_start_word) * 4,
        'record_area_start_word': header.record_area_start_word,
        'record_area_end_word': record_area_end,
        'pool_bytes': len(datas) - string_index * 4,
        'pool_count': len(pool_raw),
        'pool_unreferenced': sum(1 for e in pool_raw if e['ref_count'] == 0),
        'pool_max_ref': max((e['ref_count'] for e in pool_raw), default=0),
        'distinct_keys': distinct_keys,
        'duplicate_key_rows': sum(1 for s in key_group_size if s > 1),
        'max_key_group': max(key_group_size, default=0),
        'records_with_deps': sum(1 for d in deps_counts if d > 0),
        'max_deps': max(deps_counts, default=0),
        'enc_counts': enc_counts,
        'grain_counts': grain_counts,
        'storage_path_len_min': min(path_lens, default=0),
        'storage_path_len_max': max(path_lens, default=0),
        'distinct_names': len(name_index),
    }
    stats['duplicate_key_groups'] = len(set(g for g, s in zip(key_group_no, key_group_size) if s > 1))

    # 校验清单：§11 的 7 项 + dataHash / fileHash 补充项
    checks = [
        {'id': 1, 'name': 'formatVersion == 1', 'ok': header.format_version == FORMAT_VERSION,
         'actual': header.format_version, 'expected': FORMAT_VERSION},
        {'id': 2, 'name': '键表非递减', 'ok': all(name_hashes[i] <= name_hashes[i + 1]
                                              for i in range(file_count - 1)),
         'actual': f'{file_count} 键', 'expected': '非递减'},
        {'id': 3, 'name': '记录区恰好终止于 stringIndex', 'ok': record_area_end == string_index,
         'actual': f'word {record_area_end:#x}', 'expected': f'word {string_index:#x}'},
        {'id': 4, 'name': '偏移表是记录区的完整排列', 'ok': set(record_offsets) == set(word2rec),
         'actual': f'{len(set(record_offsets))} 个不同偏移', 'expected': f'{file_count} 条记录'},
        {'id': 5, 'name': 'namePointer 命中前缀池条目', 'ok': all(r['name_pointer'] * 4 in namefront
                                                            for r in records),
         'actual': f'{sum(1 for e in pool_raw if e["ref_count"] > 0)}/{len(pool_raw)} 条被引用',
         'expected': '全部命中'},
        {'id': 6, 'name': 'key_order 是 0..N-1 的双射', 'ok': sorted(key_order_by_word.values()) == list(range(file_count)),
         'actual': f'{len(set(key_order_by_word.values()))} 个不同下标', 'expected': f'{file_count} 个'},
        {'id': 7, 'name': '段长对账 = datas 总字节', 'ok':
            (OFFSET_NAME_HASH + file_count * 2) * 4 + (record_area_end - header.record_area_start_word) * 4
            + (len(datas) - string_index * 4) == len(datas),
         'actual': f'{(OFFSET_NAME_HASH + file_count * 2) * 4 + (record_area_end - header.record_area_start_word) * 4 + (len(datas) - string_index * 4)} B',
         'expected': f'{len(datas)} B'},
        {'id': 8, 'name': 'dataHash = 整表 128 位哈希低 32 位', 'ok': data_hash_recomputed == header.data_hash,
         'actual': myhex(data_hash_recomputed), 'expected': myhex(header.data_hash)},
        {'id': 9, 'name': 'fileHash 未超 size 决定的上界', 'ok': not file_hash_violations,
         'actual': f'{len(file_hash_violations)} 例超界', 'expected': '0 超例'},
        {'id': 10, 'name': 'nameHash 键逐条命中 XOR16(长名)', 'ok': not bad_keys,
         'actual': f'{file_count - len(bad_keys)}/{file_count} 命中', 'expected': f'{file_count}/{file_count}'},
    ]

    return ManifestResult(
        source=source, datas=datas, header=header,
        manifest_version=manifest_version, asset_bundle_name=asset_bundle_name,
        mono_tree=mono_tree or {}, script_tree=script_tree or {}, bundle_tree=bundle_tree or {},
        name_hashes=name_hashes, record_offsets=record_offsets, records=records,
        namefront=namefront, pool_raw=pool_raw,
        key_group_no=key_group_no, key_pos_in_group=key_pos_in_group, key_group_size=key_group_size,
        word2rec=word2rec, rec_index_by_word=rec_index_by_word,
        key_order_by_word=key_order_by_word, name_index=name_index, dependents=dependents,
        stats=stats, checks=checks,
        bad_keys=bad_keys, file_hash_violations=file_hash_violations,
        data_hash_recomputed=data_hash_recomputed,
    )


def build_json_manifest(r):
    """组装完整清单 JSON（字段与其插入顺序与历史产物保持一致，便于逐字节比对）。"""
    datas = r.datas
    namefront = r.namefront
    word2rec = r.word2rec
    header = r.header
    file_count = header.file_count
    return {
        'source': r.source,
        'asset_bundle_name': r.asset_bundle_name,
        'manifest_version': r.manifest_version,
        # 承载清单的 MonoBehaviour（Amber.AssetBundleManifestInfo）自身元字段
        'mono_behaviour': {
            'name': r.mono_tree.get('m_Name', ''),
            'script_path_id': (r.mono_tree.get('m_Script') or {}).get('m_PathID'),
        },
        # 对应的 MonoScript：坐实类型归属（类名 / 命名空间 / 程序集 + 16 字节脚本指纹）
        'script': {
            'class_name': r.script_tree.get('m_ClassName', ''),
            'namespace': r.script_tree.get('m_Namespace', ''),
            'assembly_name': r.script_tree.get('m_AssemblyName', ''),
            'properties_hash': props_hash_hex(r.script_tree.get('m_PropertiesHash')),
        },
        # AssetBundle 对象本体
        'asset_bundle': {
            'path_flags': r.bundle_tree.get('m_PathFlags'),
            'runtime_compatibility': r.bundle_tree.get('m_RuntimeCompatibility'),
            'explicit_data_layout': r.bundle_tree.get('m_ExplicitDataLayout'),
            'is_streamed_scene_asset_bundle': r.bundle_tree.get('m_IsStreamedSceneAssetBundle'),
            'container': r.bundle_tree.get('m_Container', []),
            'main_asset': r.bundle_tree.get('m_MainAsset'),
            'preload_table': r.bundle_tree.get('m_PreloadTable', []),
            'dependencies': r.bundle_tree.get('m_Dependencies', []),
            'scene_hashes': r.bundle_tree.get('m_SceneHashes', []),
        },
        'header': header.as_dict(len(datas)),
        # 名字共享前缀池：字节偏移 -> 目录前缀（off|xor / one|dir|all / 目录）
        'name_prefix_pool': {str(k): v for k, v in namefront.items()},
        # 键表（键序）：键大量重复而无法从 records 还原，故原样导出并给出对应记录
        'key_table': [
            {
                'key': myhex(r.name_hashes[i]),
                'record_word': r.record_offsets[i],
                'record_byte': r.record_offsets[i] * 4,
                'record_index': r.rec_index_by_word[r.record_offsets[i]],
            }
            for i in range(file_count)
        ],
        'records': [
            {
                'index': i,
                'key_order': rec['key_order'],
                'word': rec['word'],
                'byte': rec['byte'],
                'key': myhex(rec['key']),
                'bundle_name': rec['full_name'],
                'storage_path': rec['storage_path'],
                'asset_bundle_hash': rec['asset_bundle_hash'],
                'asset_bundle_hash_hex': rec['hash_hex'],
                'file_hash': rec['file_hash'],
                'file_hash_hex': rec['file_hash_hex'],
                'size': rec['size'],
                'dependency_count': rec['dependency_count'],
                'dependencies': rec['dependencies'],
                'dependencies_resolved': rec['dependencies_resolved'],
                'name_pointer_word': rec['name_pointer'],
                'name_pointer_byte': rec['name_pointer'] * 4,
                'pool_prefix': namefront.get(rec['name_pointer'] * 4, ''),
                'file_name': rec['name'],
            }
            for i, rec in enumerate(r.records)
        ],
    }