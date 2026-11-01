"""AssetBundle 容器读取层。

职责：用 UnityPy 打开一个 manifest 资产（UnityFS AssetBundle，通常无扩展名），
取出三类对象（`Amber.AssetBundleManifestInfo` MonoBehaviour / `AssetBundle` / `MonoScript`）
的 typetree，并把 `data.datas` 按 4 字节小端还原成原始清单字节流交给 `manifest_core` 解析。

只负责"读容器 + 还原字节"，不含任何二进制布局知识（那部分全在 manifest_core.py）。
`log` 回调用于让 CLI 复现原有的逐行打印；GUI 传 None。
"""

import os
from dataclasses import dataclass, field

import UnityPy


@dataclass
class ContainerInfo:
    """一个 manifest 资产解出的容器内容。"""
    path: str
    file_size: int = 0
    datas: bytes = b''
    manifest_version: str = ''
    asset_bundle_name: str = ''
    mono_name: str = ''
    mono_tree: dict = field(default_factory=dict)
    script_tree: dict = field(default_factory=dict)
    bundle_tree: dict = field(default_factory=dict)
    object_types: list = field(default_factory=list)


def read_container(path, log=None):
    """打开 path 并解出容器信息。datas 即 <name>.dat 的内容（无需落盘中转）。"""
    env = UnityPy.load(path)
    if log:
        log(env)

    mono_tree = None
    script_tree = None
    bundle_tree = None
    object_types = []

    for obj in env.objects:
        if log:
            log(obj)
        object_types.append(obj.type.name)
        if obj.type.name == "MonoBehaviour":
            if obj.serialized_type.nodes:
                mono_tree = obj.read_typetree()
                if log:
                    for i in mono_tree:
                        log(i)
                    log(mono_tree['m_Name'])
                    log(mono_tree['version'])
        if obj.type.name == "AssetBundle":
            if obj.serialized_type.nodes:
                bundle_tree = obj.read_typetree()
                if log:
                    log(bundle_tree)
        if obj.type.name == "MonoScript":
            if obj.serialized_type.nodes:
                script_tree = obj.read_typetree()
                if log:
                    log(script_tree)

    if mono_tree is None:
        raise RuntimeError(f'{os.path.basename(path)} 中未找到 Amber.AssetBundleManifestInfo（MonoBehaviour）对象')

    # datas 是 uint[]，按 4 字节小端原样还原成原始清单字节流
    datas = b''.join((j & 0xFFFFFFFF).to_bytes(4, byteorder='little')
                     for j in mono_tree['data']['datas'])

    bundle_tree = bundle_tree or {}
    return ContainerInfo(
        path=path,
        file_size=os.path.getsize(path) if os.path.exists(path) else 0,
        datas=datas,
        manifest_version=mono_tree['version'],
        asset_bundle_name=bundle_tree.get('m_AssetBundleName', ''),
        mono_name=mono_tree.get('m_Name', ''),
        mono_tree=mono_tree,
        script_tree=script_tree or {},
        bundle_tree=bundle_tree,
        object_types=object_types,
    )