"""Tests for offline STT model resolution and hotword repair.

这些用例存在的理由都来自真实故障，不是为覆盖率凑的：

1. 打包版 STT 静默失效 —— models/ 没打进 exe，路径查不到就回落到网络
   Whisper，表现为"14 秒后返回空"。所以模型根目录必须支持 frozen 与
   外部目录覆盖。
2. is_model_available() 曾经只要"能 import faster_whisper"就返回 True，
   而实际加载会去 HuggingFace 下载模型，把离线环境卡成超时。
3. small 模型把"贾维斯"稳定识别成"假 为 为师"，2GB 大模型虽然能识别对
   但要 25s；热词纠错是为了在保留速度的前提下修回产品名。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from app import stt


def test_vosk_dir_prefers_env_override(tmp_path, monkeypatch):
    """JARVIS_STT_MODEL 可精确到模型目录，用于强制切换大/小模型。"""
    model = tmp_path / "custom-model"
    model.mkdir()
    monkeypatch.setenv("JARVIS_STT_MODEL", str(model))
    assert stt._default_vosk_dir() == str(model)


def test_vosk_dir_env_root_is_searched(tmp_path, monkeypatch):
    """JARVIS_STT_MODEL_DIR 指定模型根目录。"""
    (tmp_path / "vosk" / "vosk-model-small-cn-0.22").mkdir(parents=True)
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_STT_MODEL", raising=False)
    assert stt._default_vosk_dir() == str(
        tmp_path / "vosk" / "vosk-model-small-cn-0.22"
    )


def test_vosk_dir_prefers_small_over_large(tmp_path, monkeypatch):
    """两个模型都在时选小的：大模型 2GB，加载要 25s，不适合语音交互。"""
    root = tmp_path / "models"
    (root / "vosk" / "vosk-model-small-cn-0.22").mkdir(parents=True)
    (root / "vosk" / "vosk-model-cn-0.22").mkdir(parents=True)
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(root))
    monkeypatch.delenv("JARVIS_STT_MODEL", raising=False)
    assert stt._default_vosk_dir().endswith("vosk-model-small-cn-0.22")


def test_vosk_dir_falls_back_to_large(tmp_path, monkeypatch):
    """没有小模型时用大模型。"""
    root = tmp_path / "models"
    (root / "vosk" / "vosk-model-cn-0.22").mkdir(parents=True)
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(root))
    monkeypatch.delenv("JARVIS_STT_MODEL", raising=False)
    assert stt._default_vosk_dir().endswith("vosk-model-cn-0.22")


def test_vosk_dir_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path / "none"))
    monkeypatch.delenv("JARVIS_STT_MODEL", raising=False)
    assert stt._default_vosk_dir() is None


def test_frozen_search_includes_exe_dir_and_meipass(monkeypatch):
    """打包态必须同时搜索 exe 同级（外部可替换）与 _MEIPASS（包内）。"""
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", "")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:\\dist\\backend.exe", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "C:\\temp\\_MEI123", raising=False)

    roots = stt._candidate_model_roots()
    assert any(r.endswith("dist\\models") for r in roots)
    assert any(r.endswith("_MEI123\\models") for r in roots)


def test_is_model_available_requires_local_files(tmp_path, monkeypatch):
    """只装了 faster_whisper 但没有本地模型时，必须判为不可用。

    否则 WhisperModel("tiny") 会联网下载，把离线语音链路卡成超时。
    """
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_STT_MODEL", raising=False)
    monkeypatch.setenv("JARVIS_STT_ALLOW_DOWNLOAD", "")
    # 没有任何模型目录
    assert stt.is_model_available() is False


def test_is_model_available_true_with_vosk(tmp_path, monkeypatch):
    (tmp_path / "vosk" / "vosk-model-small-cn-0.22").mkdir(parents=True)
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_STT_MODEL", raising=False)
    assert stt.is_model_available() is True


def test_model_status_reports_search_roots(tmp_path, monkeypatch):
    """排查打包版时，光看"不可用"没用，必须能看到搜索过哪些目录。"""
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_STT_MODEL", raising=False)
    status = stt.model_status()
    assert "search_roots" in status
    assert "vosk_model" in status
    assert "available" in status
    json.dumps(status)  # 必须可序列化（要经 HTTP 返回）


# ─── 热词纠错 ────────────────────────────────────────────────────────


def test_hotword_fixes_product_name():
    """Vosk small 实测把"贾维斯"识别成"假 为 为师"。"""
    assert stt._apply_hotword_fixes("你好 假 为 为师 现在 几点 了") == (
        "你好 贾维斯 现在 几点 了"
    )


def test_hotword_fixes_compact_form():
    assert stt._apply_hotword_fixes("你好假维斯,现在级电了") == "你好贾维斯,现在级电了"


def test_hotword_fixes_leaves_normal_chinese():
    assert stt._apply_hotword_fixes("今天天气不错") == "今天天气不错"


def test_hotword_fixes_preserves_english_spaces():
    """英文不能被无条件去空格 —— 中文模型也会识别英文。"""
    assert stt._apply_hotword_fixes("hello world") == "hello world"


def test_hotword_fixes_empty():
    assert stt._apply_hotword_fixes("") == ""


def test_hotword_fixes_env_extension(monkeypatch):
    monkeypatch.setenv("JARVIS_STT_HOTWORDS", "小爱同学=小艺, 天穹=天工")
    fixes = stt._hotword_fixes()
    assert fixes["小爱同学"] == "小艺"
    assert fixes["天穹"] == "天工"


def test_hotword_fixes_env_applied(monkeypatch):
    monkeypatch.setenv("JARVIS_STT_HOTWORDS", "打开灯=开灯")
    assert stt._apply_hotword_fixes("请 打开灯") == "请 开灯"
