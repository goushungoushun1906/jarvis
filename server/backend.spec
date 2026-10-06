# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for JARVIS backend
#
# 打包命令（在 server/ 目录下执行）：
#   .venv-final\Scripts\python.exe -m PyInstaller backend.spec --noconfirm
#
# 说明：
#   - app 下的模块用 collect_submodules 自动收集，避免新增模块（如 app.model_names）
#     忘记写进 hiddenimports 导致打包后 500。
#   - 不要再 exclude PIL：pyscreeze（截图）与 rapidocr（OCR）运行时都要用它，
#     之前 exclude 会让打包版截图/OCR 直接失败。

from PyInstaller.utils.hooks import collect_submodules

# --- 项目自身模块：自动全量收集 ---
app_modules = collect_submodules('app')

# chromadb 的实现后端（rust / sqlite / segment）和遥测都是运行时按字符串 `
# importlib.import_module("chromadb.api.<impl>")` 动态加载的，静态钩子扫不到。
# 逐个补漏是无底洞（先缺 telemetry.product.posthog，再缺 api.rust…），
# 这里直接收集全部子模块 —— 一共 136 个 .py / 6.4MB，大多本来就会打进去。
chromadb_modules = collect_submodules('chromadb')

# --- 第三方模块：PyInstaller 钩子覆盖不到的显式补上 ---
extra_hiddenimports = [
    # uvicorn 的 loop / protocol 是按字符串动态导入的
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'uvicorn.lifespan.off',
    'anyio._backends._asyncio',
    'multipart',
    'multipart.multipart',
    'aiosqlite',
    'httpx',
    'starlette',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.responses',
    'pydantic',
    'pydantic.deprecated',
    'chromadb.api',
    'chromadb.api.types',
    'onnxruntime',
    'numpy',
    'numpy.core',
    'numpy.core.numeric',
    'scipy',
    'scipy.spatial',
    'scipy.spatial.distance',
    'scipy.special',
    'pyautogui',
    'pyscreeze',
    'pytweening',
    'pygetwindow',
    'pymsgbox',
    'pyrect',
    'PIL',
    'PIL.Image',
    'PIL.ImageGrab',
    'fitz',
    'docx',
    'openpyxl',
    'xlrd',
    'edge_tts',
    'vosk',
    'pvporcupine',
    'pycaw',
    'comtypes',
    'comtypes.client',
    'win32clipboard',
    'win32con',
    'win32gui',
    'psutil',
    'apscheduler',
    'apscheduler.executors',
    'apscheduler.triggers',
    'playwright',
    'playwright.async_api',
    'mcp',
    'mcp.client',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('.env', '.'),
        # 插件是运行时按目录扫描动态加载的（plugins/manager.scan_and_load），
        # 不打包进去的话打包版会一个插件都加载不到。
        ('plugins', 'plugins'),
        ('mcp_servers.json', '.'),
    ],
    hiddenimports=app_modules + chromadb_modules + extra_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 注意：不要 exclude PIL / matplotlib（rapidocr 会用）；
    # tkinter 确认无用（代码从不 import），保留 exclude 减小体积。
    excludes=['tkinter'],
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
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
