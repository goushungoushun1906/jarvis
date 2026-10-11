"""把密钥存进系统凭据管理器，而不是明文留在 .env 里。

为什么不用 keyring：它靠 stevedore / entrypoints 在运行时动态发现 backend，
PyInstaller 静态扫描扫不到这些入口点，打包后要么退化成"找不到 backend"、
要么直接崩。这里直接 ctypes 调 advapi32 的 CredRead/CredWrite，零第三方
依赖，也就不存在打包丢失的问题。

非 Windows 平台一律返回"不支持"，调用方回退到环境变量 / .env，行为不变。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger("jarvis")

_SUPPORTED = sys.platform == "win32"

# 凭据管理器里的目标名前缀。所有 JARVIS 密钥都挂在 "JARVIS/" 下，用户在
# 「凭据管理器 → Windows 凭据」里能一眼认出这是本应用的条目。
_PREFIX = "JARVIS/"

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _advapi32():
    """加载 advapi32 并声明用到的四个函数签名。

    惰性加载：导入本模块不应因为平台或环境问题而让整个后端起不来。
    """
    dll = ctypes.WinDLL("advapi32", use_last_error=True)
    dll.CredReadW.restype = wintypes.BOOL
    dll.CredReadW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    dll.CredWriteW.restype = wintypes.BOOL
    dll.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    dll.CredDeleteW.restype = wintypes.BOOL
    dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    dll.CredFree.argtypes = [ctypes.c_void_p]
    return dll


def _target(name: str) -> str:
    return f"{_PREFIX}{name}"


def is_supported() -> bool:
    """当前平台能否使用系统凭据管理器。"""
    return _SUPPORTED


def get_secret(name: str) -> str | None:
    """读取密钥。不存在或平台不支持时返回 None。"""
    if not _SUPPORTED:
        return None
    try:
        dll = _advapi32()
        pcred = ctypes.POINTER(_CREDENTIALW)()
        if not dll.CredReadW(_target(name), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
            return None
        try:
            cred = pcred.contents
            if not cred.CredentialBlobSize:
                return None
            blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return blob.decode("utf-8")
        finally:
            dll.CredFree(pcred)
    except Exception as e:
        # 凭据管理器不可用时必须静默降级到 .env，不能让后端起不来
        logger.warning("读取凭据 '%s' 失败，回退到 .env：%s", name, e)
        return None


def set_secret(name: str, value: str) -> None:
    """写入密钥。失败时抛 RuntimeError，让调用方能告诉用户没存进去。"""
    if not _SUPPORTED:
        raise RuntimeError("当前平台不支持系统凭据管理器")
    if not value:
        raise ValueError("密钥不能为空")

    dll = _advapi32()
    blob = value.encode("utf-8")
    # 缓冲区必须在调用期间存活，所以显式绑定到局部变量
    buf = ctypes.create_string_buffer(blob)

    cred = _CREDENTIALW()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = _target(name)
    cred.UserName = name
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE

    if not dll.CredWriteW(ctypes.byref(cred), 0):
        raise RuntimeError(
            f"写入凭据失败：{ctypes.WinError(ctypes.get_last_error())}"
        )


def delete_secret(name: str) -> bool:
    """删除密钥。原本就不存在时返回 False。"""
    if not _SUPPORTED:
        return False
    try:
        dll = _advapi32()
        ok = dll.CredDeleteW(_target(name), CRED_TYPE_GENERIC, 0)
        if not ok:
            return False
        return True
    except Exception as e:
        logger.warning("删除凭据 '%s' 失败：%s", name, e)
        return False
