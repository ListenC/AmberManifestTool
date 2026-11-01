"""批量导出 CLI：遍历 input/，把每个 manifest 的解析结果写到 output/<name>/ 下。

解析逻辑在 manifest_core.py（与 amber_gui 可视化工具共用同一份实现），
容器读取在 manifest_container.py。产物文件名保留历史的 blockN 命名，便于与老产物一一对应。

游戏（扰动流，决定 storage_path 前缀）默认用 hashcode.DEFAULT_STREAM「幻想收束」（表 `keyIF`）；
另一款游戏用 `--stream ek`（Engage Kill，表 `keyEK`），也可以直接写游戏名。
"""

import os
import sys
import json

import hashcode
from manifest_core import (FORMAT_VERSION, OFFSET_NAME_HASH, myhex,
                           parse_header, parse_datas, build_json_manifest)
from manifest_container import read_container

# ---------- 目录约定 ----------
INPUT_DIR = 'input'     # manifest 资产（无扩展名）放这里
OUTPUT_DIR = 'output'   # 每个 manifest 的输出写到 output/<manifest名>/ 下


def parse_manifest(name):
    """解析 input/<name>，所有产物写到 output/<name>/ 下。"""
    in_path = os.path.join(INPUT_DIR, name)
    out_dir = os.path.join(OUTPUT_DIR, name)
    os.makedirs(out_dir, exist_ok=True)

    def out_path(suffix):
        return os.path.join(out_dir, name + suffix)

    print(f'================ {name} ================')
    info = read_container(in_path, log=print)

    # 还原出的原始清单字节流，即 <name>.dat
    datas = info.datas
    with open(out_path('.dat'), 'wb') as f:
        f.write(datas)

    # ---------------- 表头 ----------------
    header = parse_header(datas)
    assert header.format_version == FORMAT_VERSION, f'未知格式版本 {header.format_version}'
    print(f'dataHash:      {myhex(header.data_hash)}')
    print(f'formatVersion: {header.format_version}')
    print(f'fileCount:     {header.file_count}')
    print(f'stringIndex:   {myhex(header.string_index)} (字节 {header.string_index * 4})')

    # ---------------- 索引表 / 记录区 / 前缀池 / 自校验（全在 manifest_core） ----------------
    r = parse_datas(datas, source=name,
                    manifest_version=info.manifest_version,
                    asset_bundle_name=info.asset_bundle_name,
                    mono_tree=info.mono_tree, script_tree=info.script_tree,
                    bundle_tree=info.bundle_tree, header=header)

    records = r.records
    file_count = r.file_count
    assert r.stats['record_area_end_word'] == r.string_index, \
        f'记录区结束于 word {r.stats["record_area_end_word"]:#x}，与 stringIndex {r.string_index:#x} 不符'
    print(f'记录区: word {r.stats["record_area_start_word"]:#x} ~ {r.stats["record_area_end_word"]:#x}，共 {len(records)} 条')
    print(f'总大小:{r.stats["size_total"] / 1024 / 1024} MB')
    print(r.namefront)

    # 1) 键表 = XOR16(长名)，见 manifest_format.md §13.1
    assert not r.bad_keys, \
        f'键校验失败：{len(r.bad_keys)}/{file_count} 条与 XOR16(长名) 不符，例 {r.bad_keys[0]}'
    # 2) dataHash = 整表 128 位哈希的最低 32 位（word 0 参与计算时按 0 计），见 §13.2
    assert r.data_hash_recomputed == header.data_hash, 'dataHash 自校验失败'
    # 3) fileHash = HashFromBytes(bundle 文件内容)，见 §13.3。本地没有 bundle 本体无法直接重算，
    #    只能校验必要条件的量级上界（详见 manifest_core.parse_datas）
    assert not r.file_hash_violations, \
        f'fileHash 超出 size 决定的上界：{r.file_hash_violations[0]}'
    print(f'自校验: 键 {file_count}/{file_count} 命中, dataHash 一致, fileHash 上界 0 超例')

    # ---------------- 输出 ----------------
    # 文件名保留原来的 blockN 序号（便于和老产物一一对应），后缀换成 IL2CPP dump 里的真实字段含义
    # 每个文件首行是 '#' 开头的表头注释，写清列的真实含义；表头不会与数据行混淆
    word2rec = r.word2rec
    assert set(r.record_offsets) == set(word2rec), '偏移表不是记录区的完整排列'

    # 存储路径 = getFrontPath(bundle 名) + assetBundleHash 的 32 位 hex，即 CDN / 本地真实文件名
    with open(out_path('.txt'), 'w') as f:
        for rec in records:
            f.write(rec['storage_path'] + '\t' + rec['full_name'] + '\n')

    # block1 = nameHash 键表：升序，AssetBundleData.Get(name) 二分查找用的键（记录顺序与 block2 同序）
    with open(out_path('_block1namehash.txt'), 'w') as f:
        f.write('# name_hash\t升序键，对应记录见 block2 同序行\n')
        for key in r.name_hashes:
            f.write(myhex(key) + '\n')

    # block2 = 键 -> 记录索引表：键序 i 对应的记录必须用 record_offsets 反查，不能按记录顺序对齐
    with open(out_path('_block2asset.txt'), 'w') as f:
        f.write('# name_hash\trecord_word(bundle记录word偏移)\tbundle_name\tsize(bytes)\n')
        for i in range(file_count):
            rec = word2rec[r.record_offsets[i]]
            f.write(myhex(r.name_hashes[i]) + '\t' + myhex(r.record_offsets[i]) + '\t')
            f.write(rec['full_name'] + '\t')
            f.write(str(rec['size']) + ' bytes\n')

    # block3 = fileHash(Int4)：4 个 u32 分量按 x/y/z/w 原序拼成 32 字符 hex，下载后 Verify(filepath, bytes) 校验完整性用
    # 注意它并非密码学哈希：分量是“两个字节累加器相加再异或”得到的窄值，高位通常为 0（见 §13.3）
    with open(out_path('_block3filehash.txt'), 'w') as f:
        f.write('# file_hash(4*u32 分量拼接的 32 字符 hex)\tbundle_name\n')
        for rec in records:
            f.write(rec['file_hash_hex'] + '\t')
            f.write(rec['full_name'] + '\n')

    # block4 = dependencies：第 1 列 dependencyCount，其后每个值都是另一条记录起始的 word 偏移（原样保留）
    with open(out_path('_block4depend.txt'), 'w') as f:
        f.write('# dependency_count\tdependency_record_word...\tbundle_name\t(依赖值=另一条记录的word偏移)\n')
        for rec in records:
            f.write(myhex(rec['dependency_count']) + '\t')
            for x in rec['dependencies']:
                f.write(myhex(x) + '\t')
            f.write(rec['full_name'] + '\n')

    # block5 = namePointer：指向名字前缀池的 word 偏移，完整名 = 池内前缀 + 记录内文件名
    with open(out_path('_block5namepointer.txt'), 'w') as f:
        f.write('# name_pointer_word(前缀池word偏移)\tbundle_name\n')
        for rec in records:
            f.write(myhex(rec['name_pointer']) + '\t')
            f.write(rec['full_name'] + '\n')

    # ---------------- 完整清单 JSON（机器可读，便于解析查询） ----------------
    with open(out_path('_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(build_json_manifest(r), f, ensure_ascii=False, separators=(',', ':'))


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    # 游戏（扰动流）：`--stream if` 或 `--stream=if`，也可以直接给游戏名；缺省用 DEFAULT_STREAM
    argv = sys.argv[1:]
    stream = hashcode.DEFAULT_STREAM
    for i, a in enumerate(argv):
        if a == '--stream' and i + 1 < len(argv):
            stream = argv[i + 1]
        elif a.startswith('--stream='):
            stream = a.split('=', 1)[1]
    try:
        hashcode.set_stream(stream)
    except ValueError as exc:
        sys.exit(f'{exc}\n用法: python main.py [--stream ek|if|游戏名]')
    print(f'游戏: {hashcode.get_stream_label()}（--stream {hashcode.get_stream()}）')

    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for i in sorted(os.listdir(INPUT_DIR)):
        # 只处理无扩展名的 manifest 资产，跳过子目录和 .dat / .txt 等派生产物
        if not os.path.isfile(os.path.join(INPUT_DIR, i)) or os.path.splitext(i)[1]:
            continue
        parse_manifest(i)


# See PyCharm help at https://www.jetbrains.com/help/pycharm/