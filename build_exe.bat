@echo off
REM Build the distributable AmberManifestTool as ONE single-file exe: dist\AmberManifestTool.exe
REM (double-click to run; unpacked to %TEMP% on every start, so cold start is slower than a
REM  onedir build and it is slightly more likely to trip Defender).
REM NOTE: keep this file ASCII-only. cmd reads .bat with the OEM codepage (GBK here), so UTF-8
REM Chinese text gets mangled and can split lines (seen: "ol\" is not recognized...).
REM NOTE: --collect-data UnityPy is mandatory: UnityPy\helpers\Tpk.py opens the packaged
REM resources\uncompressed.tpk at import time, and --collect-submodules only ships .py files.
REM NOTE: --collect-submodules PySide6.QtCharts is mandatory: the assets-view tab imports it at
REM module import time, and PySide6's binaries are not auto-detected from a plain import.
setlocal
cd /d "%~dp0"

.\venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name AmberManifestTool ^
  --paths . ^
  --add-data "amber_gui\style.qss;amber_gui" ^
  --collect-submodules UnityPy ^
  --collect-data UnityPy ^
  --hidden-import brotli --hidden-import lz4.block --hidden-import texture2ddecoder ^
  --hidden-import etcpak --hidden-import PIL.Image ^
  --hidden-import PySide6.QtCharts --collect-submodules PySide6.QtCharts ^
  --exclude-module capstone --exclude-module pyfmodex --exclude-module tkinter ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtSql ^
  amber_gui\__main__.py

if errorlevel 1 (
  echo.
  echo Build FAILED, see the output above.
  exit /b 1
)

echo.
echo Done: dist\AmberManifestTool.exe  ^(single file, ship just this exe^)