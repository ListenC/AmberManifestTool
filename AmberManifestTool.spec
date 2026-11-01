# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules

datas = [('amber_gui/style.qss', 'amber_gui')]
hiddenimports = ['brotli', 'lz4.block', 'texture2ddecoder', 'etcpak', 'PIL.Image', 'PySide6.QtCharts']
datas += collect_data_files('UnityPy')
hiddenimports += collect_submodules('UnityPy')
hiddenimports += collect_submodules('PySide6.QtCharts')


a = Analysis(
    ['amber_gui/__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['capstone', 'pyfmodex', 'tkinter', 'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtWebEngineCore', 'PySide6.QtSql'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AmberManifestTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
