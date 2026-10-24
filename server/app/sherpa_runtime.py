"""sherpa-onnx 的 Windows DLL 预装载。

必须**先于** ``import sherpa_onnx`` 被导入。原因是 .pyd 的依赖 DLL 在 LoadLibrary
那一刻就解析完了，事后再补 add_dll_directory 已经无效 —— 这也是为什么本模块把
准备工作放在模块顶层执行，而不是暴露成让调用方记得去调的函数。

背景（k2-fsa/sherpa-onnx#3059）：Windows 的 DLL 搜索顺序会让 sherpa-onnx 命中
系统里另一份不兼容的 onnxruntime.dll，报错形态是

    The requested API version is not available, only API versions [1, 17]
    are supported in this build. Current ORT Version is: 1.17.1

本机实测 C:\\Windows\\System32\\onnxruntime.dll 与 SysWOW64 下同名文件的版本正是
1.17.2606.1301，与上面那句报错里的版本号一致。所以这不是理论风险，是本机的现实。

本模块做的是应用级兜底（不需要管理员权限、随时可停）；根治手段是把那份系统 DLL
改名，属于系统改动，交给使用者自行决定是否执行。
"""

from __future__ import annotations

import os
import sys

# 记录实际注册成功的目录，供后端自检与诊断面板展示
_ADDED: list[str] = []

_ENV_DLL_DIR = "JARVIS_SHERPA_DLL_DIR"
_ENV_FORCE_DIRS = "JARVIS_SHERPA_FORCE_DLL_DIRS"


def candidate_dirs() -> list[str]:
    """找出所有可能需要加入 DLL 搜索路径的目录。

    不写死 ``lib`` 这个子目录名 —— 包的内部布局可能随版本变化，这里直接在包目录
    里 walk，凡是有 .dll 的子目录都收进来，兼容性更稳。
    """
    dirs: list[str] = []

    override = os.environ.get(_ENV_DLL_DIR, "").strip()
    if override:
        dirs += [p.strip() for p in override.split(",") if p.strip()]

    try:
        import importlib.util

        # 只定位不执行：不能用 import sherpa_onnx，那会先把 DLL 加载进去
        spec = importlib.util.find_spec("sherpa_onnx")
        if spec is not None and spec.origin:
            pkg_dir = os.path.dirname(os.path.abspath(spec.origin))
            for root, _sub, files in os.walk(pkg_dir):
                if any(f.lower().endswith(".dll") for f in files):
                    dirs.append(os.path.abspath(root))
    except Exception:
        # 包还没装上的情况下不该让后端启动失败，静默跳过
        pass

    # 去重且保持顺序：先 override 再包内目录
    seen: set[str] = set()
    unique: list[str] = []
    for d in dirs:
        key = os.path.normcase(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def _prepare() -> list[str]:
    """把候选目录加入 DLL 搜索路径。仅 Windows 需要。"""
    if sys.platform != "win32":
        return _ADDED

    for directory in candidate_dirs():
        if not os.path.isdir(directory):
            continue
        try:
            os.add_dll_directory(directory)
        except (AttributeError, OSError, FileNotFoundError):
            # AttributeError: 非 Windows；OSError/FileNotFoundError: 路径无效
            continue
        # 同时前置到 PATH：仍有部分加载路径会走 PATH 搜索，双份保险
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
        _ADDED.append(directory)

    if os.environ.get(_ENV_FORCE_DIRS, "").strip() == "1":
        _force_default_dll_directories()

    return _ADDED


def _force_default_dll_directories() -> None:
    """排障开关：收紧进程级 DLL 搜索策略，确保 user dirs 排在 System32 之前。

    默认关闭。因为 SetDefaultDllDirectories 是**进程级且不可撤销**的，一旦调用，
    后续所有 DLL 加载都不再搜索 PATH 与当前目录。本项目 rapidocr / onnxruntime
    的加载路径不在我们掌控内，贸然收紧可能按下葫芦浮起瓢。只在确认需要时手动打开。
    """
    try:
        import ctypes

        LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000
        ctypes.windll.kernel32.SetDefaultDllDirectories(
            LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
        )
    except Exception:
        pass


def added_dirs() -> list[str]:
    """返回已注册的 DLL 目录，供自检接口读取。"""
    return list(_ADDED)


_prepare()
