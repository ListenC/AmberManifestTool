# Amber Manifest 数据结构解析文档

适用对象：《魔法禁书目录 幻想收束》的 AssetBundle 清单（`and_main`），对应 C# 类型
`Amber.AssetBundleManifestInfo` / `Amber.AssetBundleData` / `Amber.AssetBundleData.File`
（组装于 `Assembly-CSharp.dll`，类型成员定义见 `dumpfiles/dump.cs`）。

本文档描述的是**从 AssetBundle 内 MonoBehaviour 还原出的原始字节流**（即 `output/<name>/<name>.dat`）的二进制布局。

---

## 1. 容器层

输入文件是一个标准 UnityFS AssetBundle，内部含三类对象：

| 对象 | 类型 | 提供的信息 |
|---|---|---|
| `AssetBundleManifestInfo` | MonoBehaviour（`Amber` 命名空间） | `version`（如 `"119618"`）、`data`（`AssetBundleData`）；`data.datas` 是 `uint[]` |
| `AssetBundle` | AssetBundle | `m_AssetBundleName`（如 `"and_main"`）、`m_Container`、`m_PreloadTable`、`m_MainAsset` 等（导出为 §10 的 `asset_bundle` 段） |
| `MonoScript` | MonoScript | `m_ClassName=AssetBundleManifestInfo`、`m_Namespace=Amber`、`m_AssemblyName=Assembly-CSharp.dll`，外加 16 字节 `m_PropertiesHash` 脚本指纹，用于坐实类型归属 |

从 `dump.cs` 摘录的字段偏移：

```csharp
// Amber.AssetBundleManifestInfo : ScriptableObject
0x0C  string version
0x10  AssetBundleData data

// Amber.AssetBundleData
uint[] datas
const int FormatVersion = 1
const int OFFSET_DATA_HASH      = 0
const int OFFSET_FORMAT_VERSION = 1
const int OFFSET_FILE_COUNT     = 2
const int OFFSET_STRING_INDEX   = 3
const int OFFSET_NAME_HASH      = 4
```

`datas` 按 4 字节小端逐个还原即得原始清单字节流：`dat[i]` 写入 `(dat[i] & 0xFFFFFFFF).to_bytes(4, 'little')`。

---

## 2. 单位与约定

- **word**：4 字节，小端无符号 u32。清单内所有偏移/长度/哈希分量都以 word 为单位（少数例外会显式标注 byte）。
- **Int4**：4 个连续 int32 组成的一个 128 位值，占 4 word。文件里天然是 4 个 word 连续排布 —— 这是结构体的内存布局，不是"4 个独立的哈希"。
- **哈希表示**：`Int4` 的规范 hex 写法 = 4 个分量各取 8 位 hex、按 `x/y/z/w` 原序相接，共 32 字符。
  注意这只是**书写格式**：`fileHash` 虽同样是 Int4，其分量是窄值（高位多为 0，见 §13.3），
  并不代表它是一个均匀分布的 128 位哈希。
- 全表 `uint` 读写，不做符号扩展。
  JSON 里 `asset_bundle_hash` / `file_hash` 的分量按**无符号 u32** 给出（C# 声明是 int32），
  hex 字符串不受影响；119618 中 39,331/41,886 条记录的 `asset_bundle_hash` 含 ≥ 2³¹ 的分量。

---

## 3. 整体布局

`N = fileCount`，`S = stringIndex`。

```
word 偏移                      内容
─────────────────────────────────────────────────────────────
0                              dataHash            （整表一致性哈希）
1                              formatVersion        （恒为 1）
2                              fileCount      = N
3                              stringIndex    = S   （名字前缀池起始 word）
─────────────────────────────────────────────────────────────
4          .. 4+N-1            nameHash 键表         N 个，升序
4+N        .. 4+2N-1           recordOffset 表       N 个，与键表同序
─────────────────────────────────────────────────────────────
4+2N       .. S-1              记录区                 N 条 File 记录
─────────────────────────────────────────────────────────────
S          .. 末尾             名字前缀池             可变长条目序列
```

字节层面：

| 段 | 起始 byte | 长度 |
|---|---|---|
| 表头 | 0 | 16 B |
| nameHash 键表 | 16 | `4N` B |
| recordOffset 表 | `16 + 4N` | `4N` B |
| 记录区 | `16 + 8N` | `4S − (16 + 8N)` B |
| 名字前缀池 | `4S` | `len(datas) − 4S` B |

**关键结构性质**：`stringIndex` 与记录区长度是互相印证的 —— 记录区按变长逐条解析后必须**恰好**停在 `S`，一条都不能多也不能少。这条校验如果通过，说明对记录内部布局的理解是完全正确的。

---

## 4. 表头（word 0 ~ 3）

| word | 名称 | 说明 |
|---|---|---|
| 0 | `dataHash` | 整表一致性哈希。已排除"整体/分段 crc32"和"size 求和"等假设，判为构建期的完整性/版本标记 |
| 1 | `formatVersion` | 恒为 1（`AssetBundleData.FormatVersion`）。解析器应据此断言 |
| 2 | `fileCount` | 记录条数 `N`，同时也是两张索引表的长度 |
| 3 | `stringIndex` | 名字前缀池起始位置，单位 **word**（byte = `S × 4`） |

> 注意：头部 8 字节不是 u64，而是 `dataHash`(u32) + `formatVersion`(u32) 两个独立字段。早期按 u64 读会得到无意义的大数。

---

## 5. 两张索引表（word 4 ~ 4+2N-1）

清单需要支持"按 bundle 名查记录"，而记录区是**构建顺序**（不是键序），所以用两张等长的平行数组做排序索引：

**5.1 nameHash 键表**（word `4 .. 4+N-1`）
- `N` 个 u32，**必须非递减**：游戏直接把它当有序数组用
  （`AssetBundleData.Get` 内部就是 `Array.BinarySearch(datas, 4, fileCount, nameHash)`）
- 值是 bundle 名的查找键，即**短名的 XOR16 哈希**（算法见 §13.1）。
  注意它**不是** `File.GetHashCode()`——那个函数只有一个 `ldr r0,[r0,#4]; bx lr`，返回的是 `_p`
- 查找流程：二分到某个下标 `i` → 向两侧线性扫描所有同键项 → 逐个用 `ShortNameIsSame`
  比对完整名（命中后取偏移表同下标得到记录位置）
- 键**大量重复**：119618 的 41886 条里只有 25062 个不同值（60%）。原因是 XOR16 是弱哈希：
  它只看每 4 字节一组的异或，而 64 字节缓冲全是 ASCII（每字节最高位恒为 0），
  于是只有 4×7 = 28 位熵，且**同一字节槽内字符的排列顺序不可区分** —— 目录名相同、
  只有数字编号位置不同的名字会成组撞键。游戏靠"二分 + 逐条 `ShortNameIsSame`"兜住碰撞，
  所以键与 bundle 名**不是一对一**，不能只凭键反查名字

**5.2 recordOffset 表**（word `4+N .. 4+2N-1`）
- `N` 个 u32，每个是**某条记录起始的 word 偏移**
- 与键表**同序**：`recordOffsets[i]` 就是键 `nameHashes[i]` 对应记录的位置
- 实测这 `N` 个偏移构成记录区的**完整排列**（无重复、无遗漏）

> 常见错误：把键表/偏移表按下标 `i` 直接对齐到"记录区的第 i 条记录"。记录区是构建顺序，必须用 `recordOffsets[i]` 反查，否则名字与大小会整体错配。

---

## 6. 记录区（`Amber.AssetBundleData.File`）

每条记录以 word 为单位，从记录起始处相对定位。记 `D = dependencyCount`、`L = 名字字节长度`。

```
相对 word   相对 byte   字段              类型      说明
─────────────────────────────────────────────────────────────────────
0 .. 3      0 .. 15     assetBundleHash   Int4      存储 / CDN 文件名用的 128 位哈希
4 .. 7      16 .. 31    fileHash          Int4      下载后校验文件完整性用
8           32           size              u32       文件字节数
9           36           dependencyCount   u32       依赖条数 D
10 .. 10+D-1             dependencies[D]   u32[D]    每项=另一条记录起始的 word 偏移
10+D                     namePointer       u32       指向名字前缀池的 word 偏移
(10+D+1)    名字长度       nameLen(u8) + name(UTF-8) + 4 字节对齐
─────────────────────────────────────────────────────────────────────
本记录占用的 word 数 = 10 + D + 1 + ceil((L + 1) / 4)
```

细节：

- **名字区**：`namePointer` 所在 word 的下一个 word 起，先 1 字节长度 `L`，紧跟 `L` 字节 UTF-8 内容（无结束符），随后补齐到 4 字节边界。占用 word 数 = `ceil((L+1)/4)`。
- **依赖值**：不是名字、不是哈希，而是**另一条记录起始的 word 偏移**。据此反查可得到被依赖 bundle 的完整名（`GetDependency(index)`）。
- **无依赖**时 `D = 0`，记录长为 `11 + ceil((L+1)/4)` word。
- 记录区的起始 word = `4 + 2N`。

---

## 7. 名字前缀池（word `S` .. 末尾）

共 391 条（119618 实测），是**去重后的共享目录前缀池**：大量 bundle 名共享同一目录前缀（如 `xor/one/ui_prefab/`），记录里只存剩下的文件名部分。

池内条目格式与记录里的名字区相同：`u8 长度` + UTF-8 字节 + 补齐到 4 字节。条目起始位置是**字节偏移**（`namePointer × 4`）。

**前缀的前两位是打包标志位**，不是目录名的一部分（由 `stringliteral.json` 中的 `off/one/...`、`xor/dir/...`、`xor/all/...` 字面量坐实）：

| 位 | 字符 | 展开 |
|---|---|---|
| 第 1 位 | `O` / `X` | `off/` / `xor/`（是否 xor 加密） |
| 第 2 位 | `O` / `D` / `A` | `one/` / `dir/` / `all/`（打包粒度） |
| 第 3 位起 | 原样 | 目录前缀，可能以 `/` 结尾也可能不以（如 `xor/one/ui_prefab/storyeventbase_`） |

**完整 bundle 名 = 池内前缀 + 记录内的文件名后缀**，例如：

```
池条目   xor/dir/  +  voice_chara/          → "xor/dir/voice_chara/"
记录内名 0760000001
完整名   xor/dir/voice_chara/0760000001
```

实测 119618 的 391 个池条目**全部**被至少一条记录引用（无死条目）。

---

## 8. 字段语义与证据

| 本解析中的字段 | dump 中的成员 | 语义判定依据 |
|---|---|---|
| `assetBundleHash` | `Int4 assetBundleHash`（属性） | 属性名同名；与 `localStorageFilePath`、`isDownloaded` 配套，即落盘 / CDN 的文件名 |
| `nameHash`（键表） | `AsciiString.GetHashCode()` | 键表非递减 + `AssetBundleData.Get` 内 `Array.BinarySearch` + `ShortNameIsSame` 复核；算法见 §13.1 |
| `dataHash` | `OFFSET_DATA_HASH` | `AssetBundleData.CheckDatas()` 的校验值，算法见 §13.2 |
| `fileHash` | `Int4 fileHash`（属性） | 与 `Verify(filepath, bytes)` 配套，算法 = `Hash.FromBytes`，见 §13.3 |
| `size` | `int size` | 与 CDN 实际文件字节数一致 |
| `dependencyCount` / `dependencies` | `dependencyCount`、`GetDependency(index)` | 每个值指向另一条记录起始 word |
| `namePointer` | `namePointer` | 指向名字前缀池的 word 偏移 |
| 短名 | `ToShortAssetBundleName`、`ShortNameIsSame` | 短名 = 长名把展开的标志位压回 2 个字符（`off/dir/` → `OD`），即"池内原样前缀 + 记录内后缀"；`ShortNameIsSame` 逐字节比对它 |

另有 `isValid`、`next`、`Remove(filepath)`、`GetAssetBundleName`、`GetFileName` 等成员，对本布局无额外影响。

---

## 9. 存储路径构造

`assetBundleHash` 不直接就是文件名，而是与 bundle 名一起拼出真实路径：

```
getFrontPath(bundleName) =
    hex2( GetHashCode(bundleName, 0) & 0xFF )      // 2 字符 + '/'
  + hex8( GetHashCode(bundleName, 1) )             // 8 字符
  + hex8( crc32(bundleName) )                      // 8 字符
  + hex8( GetHashCode(bundleName, 3) )             // 8 字符
                                                   // 合计 27 字符，含 1 个 '/'

storage_path = getFrontPath(bundleName) + hex32(assetBundleHash)   // 合计 59 字符
```

`GetHashCode` 是自研 32 位哈希（`hashcode.py`）：256 字节 `keyIF` / `keyEK` 替换表 + `rotate_left` / xor 混合，`pattern` 取 0 / 1 / 3。

> **扰动流是分游戏的、可注册可切换**：`keyIF` 与 `keyEK` 是同框架两款游戏各自的一份 256 字节表，用错表算出的前缀完全不同。
> 因此 `hashcode.STREAMS` 把它按「游戏」登记：`'if' -> 幻想収束`（表 `keyIF`）、`'ek' -> Engage Kill`（表 `keyEK`），
> 默认 `'if'`（幻想收束）。同一个 bundle 在两款游戏里的前缀互不相同，例如
> `getFrontPath('off/one/chara_anim_adv/adv_anim_ctrl')`：`keyIF` 得 `f1/679da982dad60e1f852b546a`、
> `keyEK` 得 `a6/44e571addad60e1fd5438dcf`。
> 切换接口为 `hashcode.set_stream('if' | 'ek' | 游戏名)`；CLI 用 `python main.py --stream ek`，
> GUI 用工具栏「游戏」下拉框（换游戏后自动重新解析），其他同框架游戏用「自定义游戏…」注册
> （`register_stream` / `parse_stream_text` / `load_stream_file`，固定 256 字节并校验长度与取值，存在 QSettings 里）。
> 校验手段：第三组 `crc32(bundle 名)` 与扰动流无关（上例两组都是 `dad60e1f`），两组前缀同时命中即可排除巧合。

实例（119618）：

```
存储路径  de/5ed79f0cab9aab132024cdb0a67442b3ed9defb0ab9b9faa9bad76c1
bundle 名 xor/dir/voice_chara/0760000001
          └ 27 字符路径 ┘└ 32 字符 assetBundleHash hex ┘
```

实测全部 41886 条的 `storage_path` 长度恒为 **59 字符**。

---

## 10. 产物文件对照

每个 manifest 在 `output/<name>/` 下生成 8 个文件，前 7 个的 `#` 开头首行是表头注释：

| 文件 | 内容 | 对应字段 |
|---|---|---|
| `<name>.dat` | 还原出的原始清单字节流 | 全部（byte 级） |
| `<name>.txt` | `storage_path \t bundle_name` | 存储路径 + 记录里存的完整名（前缀池 + 记录内文件名） |
| `<name>_block1namehash.txt` | 升序键，每行一个 | nameHash 键表 |
| `<name>_block2asset.txt` | `name_hash \t record_word \t bundle_name \t size` | 键表 + 偏移表 + 记录 |
| `<name>_block3filehash.txt` | `file_hash(4×u32 分量拼成的 32 字符 hex) \t bundle_name` | fileHash |
| `<name>_block4depend.txt` | `dependency_count \t 依赖word偏移... \t bundle_name` | dependencyCount + dependencies |
| `<name>_block5namepointer.txt` | `name_pointer_word \t bundle_name` | namePointer |
| `<name>_manifest.json` | 全量机器可读清单 | 全部 + 派生量 |

**JSON 是机器解析的首选**，每条记录自包含定位与解析结果：

```jsonc
{
  "index": 12000,
  "key_order": 29977,                       // 该记录在键表中的下标（记录序 ↔ 键序 双向映射）
  "word": 307834, "byte": 1231336,          // 在 .dat 中的定位
  "key": "0x44726135",                      // 排序键
  "bundle_name": "xor/dir/voice_chara/0760000001",
  "storage_path": "de/5ed79f0cab9aab132024cdb0a67442b3ed9defb0ab9b9faa9bad76c1",  // 真实 CDN 路径
  "asset_bundle_hash": [...], "asset_bundle_hash_hex": "a67442b3...",
  "file_hash": [...],         "file_hash_hex": "0000aa9d...",
  "size": 925663,
  "dependency_count": 0, "dependencies": [], "dependencies_resolved": [],
  "name_pointer_word": 764334, "name_pointer_byte": 3057336,
  "pool_prefix": "xor/dir/voice_chara/", "file_name": "0760000001"
}
```

顶层字段除 `source` / `asset_bundle_name` / `manifest_version` 三个标识项外，其余 7 段为：

| 段 | 内容 |
|---|---|
| `mono_behaviour` | 承载清单的 MonoBehaviour（`AssetBundleManifestInfo`）的 `m_Name` 与 `m_Script` 的 path_id |
| `script` | 对应 MonoScript 的 `class_name` / `namespace` / `assembly_name` / `properties_hash`（16 字节脚本指纹的 32 字符 hex） |
| `asset_bundle` | AssetBundle 对象本体：`path_flags`、`runtime_compatibility`、`explicit_data_layout`、`is_streamed_scene_asset_bundle`、`container`、`main_asset`、`preload_table`、`dependencies`、`scene_hashes` |
| `header` | 表头与各段边界（word / byte 两种单位） |
| `name_prefix_pool` | 字节偏移 → 目录前缀 |
| `key_table` | **键序**索引表：`key` / `record_word` / `record_byte` / `record_index`（指回 `records` 下标） |
| `records` | 全量记录（**记录序**，即构建顺序） |

> `key_table` 必须原样导出：键是弱哈希（§5.1），119618 的 41886 条只有 25062 个不同键，
> 仅凭 `records` 里的 `key` 无法还原键表的顺序与重复项。`key_order` 与 `record_index` 构成双向映射。
>
> `dependencies` 保留原始 word 偏移，`dependencies_resolved` 是反查出的 bundle 名。

---

## 11. 解析校验点

一个正确的解析器应满足：

1. `formatVersion == 1`
2. nameHash 键表非递减
3. 记录区按变长逐条解析后**恰好**终止于 `stringIndex`
4. `recordOffset` 表的取值集合与记录区起始 word 集合完全相等（完整排列）
5. 记录内 `namePointer × 4` 必须命中前缀池某条目起始位置
6. 键表的每个 `recordOffsets[i]` 都能反查到记录，且 `key_order`（记录 → 键表下标）
   是 `0..N-1` 的双射；反之 `key_table` 的 `record_index` 是 `0..N-1` 的完整排列
7. 段长对账：`16 + 8N`（表头 + 两张索引表）+ 记录区 + 前缀池 = `.dat` 总字节数

表头中**没有**全表总大小字段（`size` 之和只能自行累计，无原始值可比对）。

---

## 12. 跨版本实测数据

`input/` 下 8 个 manifest 均为同一 bundle（`and_main`）的连续版本快照：

| manifest | formatVersion | fileCount | 记录区终止 word（=stringIndex） | size 合计 |
|---|---|---|---|---|
| 99799 | 1 | 31743 | 0x8c99f | 7642.6 MB |
| 106376 | 1 | 36046 | 0xa033a | 8826.7 MB |
| 112786 | 1 | 38510 | 0xab5d0 | 9545.8 MB |
| 113252 | 1 | 38779 | 0xac87d | 9722.6 MB |
| 113747 | 1 | 39332 | 0xaeffd | 9937.3 MB |
| 114668 | 1 | 39574 | 0xb0162 | 10068.0 MB |
| 117643 | 1 | 40904 | 0xb5d93 | 10495.6 MB |
| 119618 | 1 | 41886 | 0xba5c2 | 10033.9 MB |

以 119618 为例的结构量级：

| 指标 | 值 |
|---|---|
| `.dat` 总字节 | 3,066,016 |
| stringIndex | word 763,330（byte 3,053,320） |
| 前缀池字节 | 12,696（391 条，全部被引用） |
| 不同 nameHash 键 | 25,062（XOR16 是弱哈希，见 §5.1 / §13.1） |
| 有依赖的记录 | 8,500 条（最大 89 个依赖） |
| `xor` / `off` | 33,942 / 7,944 |
| `size` 合计 | 10,521,333,201 B ≈ 10033.9 MB |

---

## 13. 关键算法（已由 libil2cpp.so 反汇编破解）

`libil2cppdump/com.square_enix.android_googleplay.index_if-08000000-0b245000.bin` 就是生成
`dumpfiles/dump.cs` 的同一个 `libil2cpp.so`：**ELF32 / ELFDATA2LSB（32 位 ARM，A32 指令集，
不是 Thumb）**。`PT_LOAD#1` 的 `off = vaddr = 0`，所以 **RVA 即文件偏移**，反汇编时基址直接取
RVA，`bl` 的目标也是 RVA（加载基址 `0x08000000` 只出现在文件名里）。

### 13.1 nameHash 键 = XOR16(短名)

涉及 3 个函数：`File.GetAssetBundleName`（长名拼装，RVA `0xEC187C`）、
`File.ToShortAssetBundleName`（RVA `0xEC0F64`）、`AsciiString.GetHashCode`（RVA `0xEC0FCC`）。
`AssetBundleData.Get`（RVA `0xEC0AA0`）把前两者内联后自己做二分查找。

```python
# 1) 长名 = 展开的标志位 + 池内目录 + 记录内后缀
#    "off/|xor/"                    "one/|dir/|all/"
long_name = enc_str + "/" + pack_str + "/" + pool_dir + inline_name
#    例：off/dir/c_anim/001000101

# 2) 短名：把展开的标志位压回 2 个字符（= 池里的原样前缀 + 记录内后缀）
#    AsciiString 布局 = { int length@0; byte data[64]@4 }，共 68 字节；
#    变换是"就地"的，缓冲区不足 64 字节补 0，长度字段不参与哈希
buf = bytearray(64); buf[:min(len(long_name), 64)] = long_name.encode()[:64]
n = min(len(long_name), 64)
buf[0] = (buf[0] - 0x20) & 0xFF          # 加密标志：'o'->'O'、'x'->'X'
buf[1] = (buf[4] - 0x20) & 0xFF          # 打包标志：'d'->'D'、'o'->'O'、'a'->'A'
for i in range(n - 8):                   # 左移 6 字节：long_name[8:] -> buf[2:]
    buf[i + 2] = buf[i + 8]
#   ⚠ 左移覆盖不到 buf[n-6 .. n-1]，这 6 字节仍是 long_name 尾部的内容，参与哈希
#     （6 = 标志位展开后的 8 字符 - 压缩后的 2 字符；"off/"、"xor/"、"one/"、"dir/"、"all/" 都是 4 字符）

# 3) 哈希 = 64 字节按 16 个 u32 全部异或（= AsciiString.GetHashCode 的完整实现）
name_hash = 0
for i in range(16):
    name_hash ^= int.from_bytes(buf[i*4:i*4+4], 'little')
```

等价说法：`name_hash = XOR16(池内原样前缀 + 记录内后缀)`，只是那 6 字节残留让结果与"纯短名"略有差别。

- `buf[4]` 取的是**原始** `long_name[4]`（`off/`、`xor/` 的第 4 个字符就是打包标志 `d/o/a`），
  这也是为什么短名恰好等于池里的原始字节。
- 池条目的第 1 个字符分支为 `X` / `O` / `N`（`N` 在实测数据里没有出现），
  第 2 个字符分支为 `A` / `D` / `O`，与 §7 的标志位映射一致。
- `File.GetHashCode()`（RVA `0xEC2DB8`）只有两条指令 `ldr r0,[r0,#4]; bx lr`，返回的是 `_p`，
  **与键表无关**（早期文档里"键 = `File.GetHashCode()`"的推测已被反汇编推翻）。

### 13.2 dataHash = 整表 128 位哈希的低 32 位

`AssetBundleData.CheckDatas()`（RVA `0xEC1334`）就是 dataHash 的自检：

```python
tmp, datas[0] = datas[0], 0                       # 先把 dataHash 自己清零
h = Hash.FromUInts(datas, 0, -1, None)            # 对整表求 §13.3 的 128 位哈希（第 3 参 -1 = 取到末尾）
datas[0] = tmp
assert h[0] == tmp                                # 只比较最低 32 位分量
```

也就是说 `dataHash` **覆盖整张清单本身**（表头 4 个 word + 两张索引表 + 记录区 + 前缀池），
是构建期写入的自校验值；`word 0` 参与计算时按 0 计。

> 早期文档写作"含 `length-1` 个 word"，与实测不符：`main.py` 用**全部 word**（`word 0` 置 0）
> 重算，8 个 manifest 的 `h[0]` 与 `word 0` **8/8 完全一致**。

### 13.3 fileHash = 内容哈希（Hash.FromBytes / FromUInts）

`File.Verify(filepath, bytes)`（RVA `0xEC2BF4`）实现为
`Int4.op_Equality(Hash.FromBytes(bytes), this.fileHash)`；
`Hash.FromUInts`（RVA `0xED76FC`）把 u32 数组按**小端展开成字节流**后走同一算法。

```python
def hash128(byte_stream):                 # fileHash：对 bundle 文件字节流
    S = [0] * 16                          # 16 个 u32 累加器（mod 2^32）
    for i, b in enumerate(byte_stream):
        S[i & 15] = (S[i & 15] + b) & 0xFFFFFFFF
    return [((S[11] + S[0]) ^ (S[15] + S[7])) & 0xFFFFFFFF,   # x
            ((S[10] + S[1]) ^ (S[14] + S[6])) & 0xFFFFFFFF,   # y
            ((S[9]  + S[2]) ^ (S[13] + S[5])) & 0xFFFFFFFF,   # z
            ((S[12] + S[4]) ^ (S[8]  + S[3])) & 0xFFFFFFFF]   # w
```

- 4 个分量各由"两个累加器相加再异或"得到，16 个槽位恰好被 4 个分量均分（每个用 4 个槽）。
- **"28 位特征"由此解释**：`S[k] ≤ 255 × ⌈size/16⌉`，分量是两个这样的值相加再异或，
  故分量 < 2^⌈log₂(2 × 255 × size/16)⌉。本作 bundle 最大约 10 MB ⇒ 上限约 2²⁸，
  实测最大值 `0x0ffe8b45`（28 位）。这不是"4×28 位槽位打包"，纯粹是文件不够大。
- 该哈希**与路径、版本无关**，只取决于文件内容：跨版本重复出现的 bundle
  共 214,972 条，其 `(size, fileHash)` 100% 一致。
- **它不是均匀哈希**，因此不该被称作"128 位哈希"：分量高位普遍为 0
  （119618 实测如 `0003abd9 / 00001c6c / 00065939 / 001e102b`），
  "32 字符 hex"只是 Int4 的书写格式。这是"两个相近累加器异或、高位相同"的必然结果，
  不是解析错误。

### 13.4 三项的验证结果

| 项 | 验证方式 | 结果 |
|---|---|---|
| nameHash 键 | 8 个 manifest 全量重算键并与键表逐条比对 | **306,774 / 306,774 命中（100%）** |
| `dataHash` | 8 个 manifest 全量按 §13.2 重算并与 `word 0` 比对 | 8 / 8 完全一致 |
| `fileHash` | ①`Verify`/`Hash.FromBytes` 反汇编；②跨版本 214,972 条重复 bundle 的 (size,fileHash) 一致性；③全部记录分量是否满足理论上界 | ①算法确认；②0 条冲突；③0 例超界 |

这三项已落进 `hashcode.py`（`GetNameHash` / `HashFromBytes` / `HashFromUInts`），
`main.py` 解析完每个 manifest 后会立即自校验并打印一行结论；键与 `dataHash` 是逐条全量比对，
`fileHash` 因本地没有 bundle 本体只能做量级上界检查（完整校验需等拿到 CDN 文件后跑 `Verify`）。

---

## 附：相关文件

| 路径 | 说明 |
|---|---|
| `main.py` | 批量解析脚本：读 `input/`，写 `output/<name>/` |
| `hashcode.py` | 哈希实现：`GetHashCode` / `crc32` / `getFrontPath`（存储路径）；`GetNameHash` / `HashFromBytes` / `HashFromUInts`（§13） |
| `dumpfiles/dump.cs` | IL2CPP dump，字段偏移与成员定义的依据 |
| `dumpfiles/stringliteral.json` | 字面量证据（`off|xor` + `one|dir|all` 命名规范） |
| `libil2cppdump/*.bin` | 原始 32 位 ARM `libil2cpp.so`，§13 反汇编的依据 |
| `manifest_analysis/` | 脚本早期版本备份 |