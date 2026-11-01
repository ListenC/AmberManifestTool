import re
import zlib

keyIF = [0xBD, 0x65, 0xF2, 0x4F, 0xBE, 0xD1, 0x36, 0xD4, 0x95, 0xFE, 0x64, 0x94, 0xCB, 0xD3, 0x7E, 0x91, 0x57, 0xB7, 0x94, 0xB7, 0x9F, 0x70, 0xB2, 0xA9, 0xA0, 0xD5, 0x4E, 0x36, 0xC6, 0x40, 0xE0, 0x2F, 0x4E, 0x6E, 0x76, 0x6D, 0xCD, 0xAE, 0xEA, 0x05, 0x13, 0x6B, 0xA7, 0x84, 0xFF, 0xED, 0x90, 0x91, 0x15, 0x7E, 0xF1, 0xF8, 0xA5, 0x9C, 0xB6, 0xDE, 0xF9, 0x56, 0x57, 0x18, 0xBF, 0x94, 0x63, 0x6F, 0x1B, 0xE2, 0x92, 0xD2, 0x7E, 0x25, 0x95, 0x23, 0x24, 0xCB, 0x93, 0xD3, 0x36, 0xD9, 0x18, 0x11, 0xF5, 0x50, 0x18, 0xE4, 0x22, 0x28, 0xD8, 0xE2, 0x1A, 0x57, 0x1E, 0x04, 0x88, 0xA5, 0x84, 0xC0, 0x6C, 0x3B, 0x46, 0x62, 0xCE, 0x85, 0x10, 0x2E, 0xA0, 0xDC, 0xD3, 0x09, 0xB2, 0xB6, 0xA4, 0x8D, 0xAF, 0x74, 0x36, 0xF7, 0x9A, 0x3F, 0x98, 0xDA, 0x62, 0x57, 0x71, 0x75, 0x92, 0x05, 0xA3, 0xB2, 0x7C, 0xCA, 0xFB, 0x1E, 0xBE, 0xC9, 0x24, 0xC1, 0xD2, 0xB9, 0xDE, 0xE4, 0x7E, 0xF3, 0x0F, 0xB4, 0xFB, 0xA2, 0xC1, 0xC2, 0x14, 0x5C, 0x78, 0x13, 0x74, 0x41, 0x8D, 0x79, 0xF4, 0x3C, 0x49, 0x92, 0x98, 0xF2, 0xCD, 0x8C, 0x09, 0xA6, 0x40, 0x34, 0x51, 0x1C, 0x11, 0x2B, 0xE0, 0x6B, 0x42, 0x9C, 0x86, 0x41, 0x06, 0xF6, 0xD2, 0x87, 0xF1, 0x10, 0x26, 0x89, 0xC2, 0x7B, 0x2A, 0x5D, 0x1C, 0xDA, 0x92, 0xC8, 0x93, 0x59, 0xF9, 0x60, 0xD0, 0xB5, 0x1E, 0xD5, 0x75, 0x56, 0xA0, 0x05, 0x83, 0x90, 0xAC, 0x72, 0xC8, 0x10, 0x09, 0xED, 0x1A, 0x46, 0xD9, 0x39, 0x6B, 0x9E, 0x19, 0x5E, 0x51, 0x44, 0x09, 0x0D, 0x74, 0xAB, 0xA8, 0xF9, 0x32, 0x43, 0xBC, 0xD2, 0xED, 0x7B, 0x6C, 0x75, 0x32, 0x24, 0x14, 0x43, 0x5D, 0x98, 0xB2, 0xFC, 0xFB, 0xF5, 0x9A, 0x19, 0x03, 0xB0, 0xB7, 0xAC, 0xAE, 0x8B]
keyEK = [0x56, 0x91, 0xBC, 0xDF, 0x11, 0xB3, 0x6F, 0xF6, 0x15, 0xD2, 0x40, 0x39, 0x18, 0x4F, 0x0E, 0x9C, 0x05, 0xB2, 0x17, 0xD8, 0x93, 0x03, 0x81, 0xFE, 0x5F, 0xC4, 0x24, 0xCF, 0x4D, 0xC5, 0x45, 0xBE, 0x52, 0x74, 0xBD, 0xAD, 0x0B, 0xA6, 0x60, 0x94, 0x57, 0x27, 0x2D, 0xE2, 0x6A, 0x55, 0x96, 0xEF, 0x69, 0x41, 0xE1, 0x6C, 0x62, 0xA5, 0xF7, 0x80, 0xBF, 0x1B, 0x79, 0xFB, 0xCA, 0x5B, 0x21, 0xB1, 0x8F, 0x04, 0xFC, 0x37, 0x3F, 0x0C, 0x86, 0x8A, 0x65, 0x77, 0xB7, 0xAA, 0xFD, 0x7A, 0x34, 0xF0, 0x97, 0xF5, 0x6D, 0x2C, 0xD5, 0x48, 0x66, 0x3C, 0x9D, 0xB0, 0xD1, 0xA9, 0x58, 0x78, 0xD7, 0x6E, 0xE3, 0x51, 0x87, 0xAC, 0x63, 0xD4, 0xE9, 0x47, 0x23, 0xE8, 0x36, 0x7D, 0xE5, 0xF2, 0xC3, 0x5C, 0x84, 0x88, 0x76, 0x1A, 0xA4, 0xC0, 0xF3, 0x31, 0x99, 0x9B, 0x6B, 0x2E, 0x98, 0xEE, 0x4B, 0x5A, 0x44, 0xC7, 0xF8, 0xC1, 0xB5, 0x53, 0xA2, 0x32, 0xC9, 0xBA, 0x7B, 0x1E, 0x10, 0xA3, 0x00, 0x01, 0x14, 0x4A, 0x08, 0x0D, 0x46, 0x95, 0x8C, 0xB6, 0x8D, 0xB8, 0xFF, 0x75, 0x4C, 0xB4, 0x64, 0xDE, 0x85, 0xA7, 0x30, 0x43, 0x1F, 0xDB, 0x7E, 0x50, 0x26, 0x35, 0x20, 0x92, 0xCE, 0x9A, 0x9E, 0xEB, 0xF4, 0x1C, 0xF9, 0x33, 0x38, 0xDD, 0x29, 0xF1, 0x3A, 0x2F, 0x5E, 0x73, 0xD3, 0xE6, 0x54, 0xB9, 0x68, 0xCD, 0xAF, 0x06, 0xD9, 0xD0, 0x61, 0x83, 0x8E, 0xC6, 0xE7, 0x7F, 0xDA, 0xAB, 0x12, 0x0F, 0x89, 0xBB, 0xD6, 0x3B, 0xC2, 0xA1, 0x3D, 0x72, 0x70, 0x19, 0xCB, 0xA8, 0x22, 0x2A, 0x59, 0x4E, 0x90, 0x28, 0x07, 0x71, 0x13, 0xA0, 0x5D, 0xCC, 0x7C, 0xED, 0xFA, 0x16, 0x9F, 0xEC, 0x09, 0x67, 0x02, 0xEA, 0x25, 0x82, 0x3E, 0x49, 0xAE, 0x0A, 0x42, 0xE0, 0x8B, 0x1D, 0xDC, 0x2B, 0xE4, 0xC8]

# GetJammingInt 查的 256 字节扰动流是**分游戏**的：同框架每款游戏各一份表，算出的存储路径前缀完全不同。
# 所以这份表按「游戏」命名与选择：STREAMS 的键是命令行/配置里用的名字，label 是界面上显示的游戏名。
#   keyIF -> 幻想收束（本作，默认）：off/one/chara_anim_adv/adv_anim_ctrl => f1/679da982dad60e1f852b546a
#   keyEK -> Engage Kill：同一个 bundle 会得到 a6/44e571addad60e1fd5438dcf，用在本作上是错的
# 校验手段：第三组 crc32(bundle 名) 与扰动流无关（上面两例都是 dad60e1f），两组前缀同时命中才能排除巧合。
STREAMS = {
    'if': {'label': '幻想收束', 'table': keyIF},      # 本作，默认
    'ek': {'label': 'Engage Kill', 'table': keyEK},
}
STREAM_SIZE = 256                       # 扰动流固定 256 字节
BUILTIN_STREAMS = frozenset(STREAMS)    # 内置游戏不可删除
DEFAULT_STREAM = 'if'
key = STREAMS[DEFAULT_STREAM]['table']


def stream_to_text(table) -> str:
    """256 字节表 -> 便于粘贴/保存的 hex 文本（空格分隔的大写两位十六进制）。"""
    return ' '.join(f'{b:02X}' for b in table)


def normalize_stream(table):
    """校验并规整成 256 字节表（list[int]，每项 0..255）。"""
    values = list(table)
    if len(values) != STREAM_SIZE:
        raise ValueError(f'扰动流必须是 {STREAM_SIZE} 字节，收到 {len(values)} 字节')
    out = []
    for i, v in enumerate(values):
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise ValueError(f'扰动流第 {i} 个字节 {v!r} 不是整数') from None
        if not 0 <= iv <= 0xFF:
            raise ValueError(f'扰动流第 {i} 个字节 {v} 超出 0..255 范围')
        out.append(iv)
    return out


def parse_stream_text(text):
    """把 hex 文本解析成 256 字节表。

    接受 `56 91 BC …`、`0x56,0x91,…`、`5691bc…` 等写法，空格/逗号/换行/冒号/0x 前缀可混用。
    """
    s = re.sub(r'0[xX]', '', str(text))
    s = re.sub(r'[\s,;:|\[\](){}<>-]', '', s)
    if len(s) % 2:
        raise ValueError(f'十六进制字符数为奇数（{len(s)} 个），无法按字节切分')
    try:
        table = list(bytes.fromhex(s))
    except ValueError:
        raise ValueError('含非十六进制字符（只接受 0-9 与 a-f，允许空格/逗号/换行/0x 前缀）') from None
    if not table:
        raise ValueError('没有解析到任何字节')
    return normalize_stream(table)


def load_stream_file(path):
    """从文件读一份扰动流：正好 256 字节按原始表读，否则当 hex 文本解析（.txt / .json 等）。"""
    with open(path, 'rb') as f:
        raw = f.read()
    if len(raw) == STREAM_SIZE:
        return normalize_stream(raw)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError(f'文件既不是 {STREAM_SIZE} 字节的原始表，也不是 UTF-8 文本，无法识别') from None
    return parse_stream_text(text)


def register_stream(name, table, label=None) -> str:
    """注册/覆盖一条扰动流，供 --stream 或 GUI 选用；返回规整后的键名。

    name 是键（查表用，大小写不敏感），label 是界面上显示的游戏名（缺省与 name 相同）。
    """
    sname = str(name).strip().lower()
    if not sname:
        raise ValueError('游戏名不能为空')
    table = normalize_stream(table)
    STREAMS[sname] = {'label': str(label).strip() if label else sname, 'table': table}
    return sname


def unregister_stream(name) -> bool:
    """移除一条自定义扰动流（内置游戏不允许移除）；返回是否真的移除了。"""
    sname = str(name).strip().lower()
    if sname in BUILTIN_STREAMS:
        raise ValueError(f'{STREAMS[sname]["label"]} 是内置游戏，不能删除')
    return STREAMS.pop(sname, None) is not None


def set_stream(name: str):
    """切换扰动流。name 是 STREAMS 的键或界面显示的游戏名，均大小写不敏感。"""
    global key
    sname = str(name).strip().lower()
    if sname in STREAMS:
        key = STREAMS[sname]['table']
        return
    for entry in STREAMS.values():                  # 允许直接按游戏名切换
        if entry['label'].lower() == sname:
            key = entry['table']
            return
    known = ' / '.join(f'{n}（{e["label"]}）' for n, e in STREAMS.items())
    raise ValueError(f'未知扰动流 {name!r}，可选：{known}')


def get_stream() -> str:
    """当前扰动流的键名。"""
    return next((n for n, e in STREAMS.items() if e['table'] is key), DEFAULT_STREAM)


def get_stream_label(name=None) -> str:
    """当前（或指定）扰动流的界面显示名（游戏名）。"""
    sname = get_stream() if name is None else str(name).strip().lower()
    entry = STREAMS.get(sname)
    return entry['label'] if entry else str(name)


def int32(invalue: int):
    invalue &= 0xFFFFFFFF
    #if invalue & 0x80000000:
    #    invalue = (invalue & 0x7FFFFFFF)*-1
    return int(invalue)


def rotate_left(value, shift):
    shift %= 32  # 确保shift在0到31的范围内
    return int32((value << shift) % (1 << 32))  # 使用模运算保持数值范围


def GetHashCode(strin:str, pattern=0):
    table = [0, 0, 0, 0]
    lenstr = len(strin) - 1

    for i in range(len(table)):
        c = ord(strin[lenstr & ~(lenstr >> 0x1F)])
        value32 = GetJammingInt(int32(pattern + c))
        if i >= len(table):
            table.append(value32)
        else:
            table[i] = value32
        pattern += len(table)
        lenstr -= 1

    shift = 0

    for i in range(len(strin) - 1, -1, -1):
       c = ord(strin[i])
       shift = int32(shift + i) ^ c
       table[i % len(table)] = int32(table[i % len(table)] + rotate_left(c, shift))

    return table[0] ^ table[1] ^ table[2] ^ table[3]


def GetJammingInt(top_index):
    value = None
    try:
        value = int.from_bytes(key[top_index:top_index+4], byteorder='little')
    except:
        pass
    value = int32(value)
    value = value if value != None else -1

    return value


def StringToHash(instr: str):
    value = zlib.crc32(instr.encode('utf-8'))
    return (value & 0xffffffff)


def getFrontPath(instr: str):
    strout = ''
    strout += format(GetHashCode(instr, 0) & 0xff, '02x') + '/'
    strout += format(GetHashCode(instr, 1), '08x')
    strout += format(StringToHash(instr), '08x')
    strout += format(GetHashCode(instr, 3), '08x')
    return strout


# ---------- manifest 键表用的哈希（依据 libil2cpp.so 反汇编，见 manifest_format.md §13） ----------
# File.GetAssetBundleName(0xEC187C) 拼出长名（如 off/dir/c_anim/001000101），
# File.ToShortAssetBundleName(0xEC0F64) 把展开的标志位压回 2 个字符，
# AsciiString.GetHashCode(0xEC0FCC) 再把 64 字节缓冲按 16 个 u32 全部异或。
# AsciiString 布局 = { int length@0; byte data[64]@4 }，以 ref 传递，所以变换是"就地"的。
# 注意它和上面 GetHashCode（存储路径用的自研哈希）是两套完全不同的算法。
def GetNameHash(long_name: str):
    buf = bytearray(64)                      # 缓冲区初始为 0（调用方会先清空 68 字节 out_name）
    raw = long_name.encode('utf-8')[:64]
    n = len(raw)
    buf[:n] = raw
    buf[0] = (buf[0] - 0x20) & 0xFF          # 加密标志：'o'->'O'、'x'->'X'
    buf[1] = (buf[4] - 0x20) & 0xFF          # 打包标志：'d'->'D'（取的是原始 long_name[4]）
    for i in range(n - 8):                   # 左移 6 字节：long_name[8:] -> buf[2:]
        buf[i + 2] = buf[i + 8]
    # 左移覆盖不到 buf[n-6 .. n-1]（6 = 展开后 8 字符 - 压缩后 2 字符），这 6 字节仍是长名尾部并参与哈希
    name_hash = 0
    for i in range(16):
        name_hash ^= int32(int.from_bytes(buf[i * 4: i * 4 + 4], byteorder='little'))
    return int32(name_hash)


# Hash.FromBytes(0xED72F0) / Hash.FromUInts(0xED76FC)：
# 16 个 u32 累加器按"字节下标 mod 16"累加，再把 16 个槽 4 分均分、两两相加后异或。
# fileHash 就是对 bundle 文件字节流跑这个算法；dataHash 是对整张清单跑。
def HashFromBytes(data: bytes):
    s = [0] * 16
    for i, b in enumerate(data):
        s[i & 15] = int32(s[i & 15] + b)
    return [int32((s[11] + s[0]) ^ (s[15] + s[7])),   # x
            int32((s[10] + s[1]) ^ (s[14] + s[6])),   # y
            int32((s[9] + s[2]) ^ (s[13] + s[5])),    # z
            int32((s[12] + s[4]) ^ (s[8] + s[3]))]    # w


def HashFromUInts(words):
    """Hash.FromUInts：把 u32 数组按小端展开成字节流后走 HashFromBytes。"""
    buf = bytearray()
    for w in words:
        buf += (w & 0xFFFFFFFF).to_bytes(4, byteorder='little')
    return HashFromBytes(buf)


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    strtemp = "xor/one/container/battlecard"
    strout = ''
    strout += format(GetHashCode(strtemp, 0) & 0xff, '02x') + '/'
    strout += format(GetHashCode(strtemp, 1), '08x')
    strout += format(StringToHash(strtemp), '08x')
    strout += format(GetHashCode(strtemp, 3), '08x')

    print(strout)
