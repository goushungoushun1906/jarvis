"""pytest 全局 fixture。

为什么要有这个文件：项目此前没有 conftest.py，每个测试文件都各自定义一遍
anyio_backend。异步用例依赖 anyio 插件提供 event loop，缺了它装饰器不起作用，
报错却指向业务代码，排查很费时间。统一放在这里，各文件既能复用也能覆盖。
"""
from __future__ import annotations

import os
import sys

import pytest

# 让 `from app.xxx import ...` 在直接跑 pytest 时可用
_SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)


@pytest.fixture
def anyio_backend():
    return "asyncio"
