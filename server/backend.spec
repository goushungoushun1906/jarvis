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

# vosk 的原生库是运行时拼路径加载的，静态扫描扫不到，必须显式收集：
#   vosk/__init__.py: _ffi.dlopen(os.path.join(os.path.dirname(__file__), "libvosk.dll"))
# frozen 下 __file__ 指向 _MEIPASS/vosk/__init__.py，所以 DLL 也必须落在
# _MEIPASS/vosk/ 下。漏掉它的表现极具误导性 —— STT 不报错，只是"识别返回空"
# （异常被 stt.py 吞掉后回落到网络 Whisper），日志里只有一句 WinError 2。
import os as _os

import vosk as _vosk

_vosk_dir = _os.path.dirname(_vosk.__file__)
vosk_binaries = [
    (_os.path.join(_vosk_dir, _f), "vosk")
    for _f in sorted(_os.listdir(_vosk_dir))
    if _f.lower().endswith((".dll", ".so", ".dylib"))
]

# --- Porcupine 语音唤醒 ---
#
# 跟 vosk 同病：原生库是运行时按路径加载的，静态扫描扫不到。但比 vosk 更狠 ——
# pvporcupine 在 **import 阶段**就会列举内置关键词目录（_factory.py 里的
# KEYWORD_PATHS = pv_keyword_paths() 是模块级代码），所以这个目录漏掉的表现
# 不是"唤醒没反应"，而是连 import 都失败、整个后端起不来。
#
# 另外打包是单文件模式，frozen 下 __file__ 指向 PYZ 内的虚拟路径，pvporcupine
# 自带的库路径推导必然失败，因此 app/wakeword.py 一律显式传绝对路径
# （用 sys._MEIPASS 解析）—— 这里的收集只负责"文件在不在包里"。
pvporcupine_binaries = []
pvporcupine_datas = []
pvporcupine_modules = []
try:
    import pvporcupine as _pv

    _pv_dir = _os.path.dirname(_pv.__file__)
    _pv_win = _os.path.join(_pv_dir, "lib", "windows", "amd64")
    if _os.path.isdir(_pv_win):
        pvporcupine_binaries = [
            (_os.path.join(_pv_win, _f), _os.path.join("pvporcupine", "lib", "windows", "amd64"))
            for _f in sorted(_os.listdir(_pv_win))
            if _f.lower().endswith(".dll")
        ]
    _pv_kw = _os.path.join(_pv_dir, "resources", "keyword_files", "windows")
    if _os.path.isdir(_pv_kw):
        pvporcupine_datas = [
            (
                _os.path.join(_pv_kw, _f),
                _os.path.join("pvporcupine", "resources", "keyword_files", "windows"),
            )
            for _f in sorted(_os.listdir(_pv_kw))
        ]
    pvporcupine_modules = [
        'pvporcupine',
        'pvporcupine._factory',
        'pvporcupine._util',
        'pvporcupine._porcupine',
    ]
except ImportError:
    # 没装 pvporcupine 的机器上也要能把 spec 跑完，只是打出来没有唤醒能力
    pass

# --- sherpa-onnx：默认唤醒引擎 ---
#
# 与 vosk / pvporcupine 同病但更隐蔽：
#   sherpa-onnx 的 wheel 里只有 .pyd 和 .py，一个 DLL 都没有；
#   onnxruntime.dll / sherpa-onnx-core.dll 全在 **另一个包** sherpa-onnx-core
#   里（win_amd64 约 16.9MB），安装后被放进同一个 sherpa_onnx/lib/ 目录。
#
# 因此：
#   1. requirements.txt 里 sherpa-onnx 与 sherpa-onnx-core 必须同版本成对锁定；
#   2. 这里必须把 DLL 收集到与 .pyd **同级**的目录，否则加载时找不到；
#   3. 不写死 "lib" 子目录名，用 walk 兜住包布局变化。
sherpa_binaries = []
sherpa_modules = []
try:
    import sherpa_onnx as _sherpa

    _sherpa_dir = _os.path.dirname(_os.path.abspath(_sherpa.__file__))
    for _root, _dirs, _files in _os.walk(_sherpa_dir):
        for _f in sorted(_files):
            if _f.lower().endswith('.dll'):
                _rel = _os.path.relpath(_root, _sherpa_dir)
                _dest = 'sherpa_onnx' if _rel == '.' else _os.path.join('sherpa_onnx', _rel)
                sherpa_binaries.append((_os.path.join(_root, _f), _dest))
    # sherpa_onnx/lib/ 没有 __init__.py，静态扫描扫不到，必须显式列出
    sherpa_modules = [
        'sherpa_onnx',
        'sherpa_onnx.cli',
        'sherpa_onnx.display',
        'sherpa_onnx.keyword_spotter',
        'sherpa_onnx.offline_recognizer',
        'sherpa_onnx.online_recognizer',
        'sherpa_onnx.utils',
        'sherpa_onnx.lib',
    ]
except ImportError:
    # 没装也要能把 spec 跑完，只是打出来的包没有 sherpa 唤醒能力
    pass


def _asr_model_datas() -> list[tuple[str, str]]:
    """收集 models/asr/ 下的 Paraformer 模型目录。

    目录名带日期版本号（如 ...-2024-03-09），写死必然在换模型后失效；
    这里扫描实际目录，并只收模型本体与词表——test_wavs 之类的样例音频
    不进包，它们只会白白撑大体积。
    """
    import glob as _glob

    out: list[tuple[str, str]] = []
    for _d in sorted(_glob.glob(_os.path.join('models', 'asr', '*'))):
        if not _os.path.isdir(_d):
            continue
        # 每个目录只收「词表 + 一个模型本体」，int8 优先
        _files = [_os.path.join(_d, 'tokens.txt')]
        for _pattern in ('model.int8.onnx', '*.int8.onnx', 'model.onnx', '*.onnx'):
            _hits = sorted(_glob.glob(_os.path.join(_d, _pattern)))
            if _hits:
                _files.append(_hits[0])
                break
        for _f in _files:
            if _os.path.isfile(_f):
                out.append((_f, _os.path.join('models', _os.path.relpath(_f, 'models'))))
    return out

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
    binaries=vosk_binaries + pvporcupine_binaries + sherpa_binaries,
    datas=[
        ('.env', '.'),
        # 插件是运行时按目录扫描动态加载的（plugins/manager.scan_and_load），
        # 不打包进去的话打包版会一个插件都加载不到。
        ('plugins', 'plugins'),
        ('mcp_servers.json', '.'),
        # 构建标识。Electron 靠它判断已在 18200 上运行的后端是不是当前这份
        # 代码，不匹配就换掉，避免"换了新包却仍在跟旧后端对话"。
        ('BUILD_ID.txt', '.'),
        # 离线语音识别模型。不打进来的话打包版 STT 找不到模型，会静默回落
        # 到网络 Whisper —— 表现就是"说话后干等十几秒然后返回空"。
        # 只打 small（66MB）：大模型 2GB 会让 exe 体积和启动解压时间都失控。
        # 需要更高准确率时，把 vosk-model-cn-0.22 放到 exe 同级 models/vosk/
        # 即可覆盖（stt.py 的搜索优先级：外部目录 > 包内）。
        ('models/vosk/vosk-model-small-cn-0.22', 'models/vosk/vosk-model-small-cn-0.22'),
        # 中文指令识别的主力模型（sherpa-onnx Paraformer）。缺了它 STT 会
        # 静默退回 Vosk，识别质量差一档但不报错——所以和唤醒模型一样整目录打。
        # 目录名带日期版本号，这里用 glob 扫描而不是写死，换版本不用改 spec。
        *_asr_model_datas(),
        # 中文语音唤醒的模型目录，两个引擎共用（默认 sherpa）：
        #   - sherpa：sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/
        #     含 tokens.txt + 三个 int8 onnx + keywords.txt（唤醒词唯一真源）
        #   - porcupine（legacy）：porcupine_params_zh.pv + 自训练 .ppn
        #
        # 缺任何一项的表现都是"叫不应但不报错"，排查成本极高，所以整目录打包。
        # 打包前记得删掉非 int8 的模型与 test_wavs/，别白白撑大体积。
        # 目录还不存在时 PyInstaller 只会警告，不会中断构建。
        ('models/wakeword', 'models/wakeword'),
        *pvporcupine_datas,
    ],
    hiddenimports=app_modules + chromadb_modules + extra_hiddenimports
    + pvporcupine_modules + sherpa_modules,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 注意：不要 exclude PIL / matplotlib（rapidocr 会用）；
    # 也不要 exclude requests —— pvporcupine 的 _util.py 在顶层 import 了它，
    # 排掉会让打包版一 import 唤醒模块就崩。
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
