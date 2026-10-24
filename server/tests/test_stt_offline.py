"""Tests for offline STT model resolution.

这些用例存在的理由都来自真实故障，不是为覆盖率凑的：

1. 打包版 STT 静默失效 —— models/ 没打进 exe，路径查不到就回落到网络
   Whisper，表现为"14 秒后返回空"。所以模型根目录必须支持 frozen 与
   外部目录覆盖。
2. is_model_available() 曾经只要"能 import faster_whisper"就返回 True，
   而实际加载会去 HuggingFace 下载模型，把离线环境卡成超时。
3. 唤醒不再走语音识别。音译词“贾维斯”曾被小模型稳定听成“假为为师”，   靠一张热词纠错表兜底；改用专用关键词引擎后（见 docs/WAKE_WORD_DESIGN.md），   那张表连同前端音近正则一并删除 —— 留着只会把正常文本里的同音词改坏。
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



# ── Paraformer（中文指令识别的新主力）────────────────────────────────
#
# 这一组要锁住的是"换了引擎但没生效"这类静默失败：模型目录名带日期版本号，
# 拼路径必然踩空；采样率不对不会报错，只会输出一堆错字。

def _make_paraformer_dir(base, name="sherpa-onnx-paraformer-zh-small-2024-03-09"):
    d = base / "models" / "asr" / name
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n", encoding="utf-8")
    (d / "model.int8.onnx").write_bytes(b"int8")
    return d


def test_paraformer_dir_found_by_scanning(tmp_path, monkeypatch):
    """目录名带日期后缀，只能"扫目录 + 验内容"，不能拼路径。"""
    d = _make_paraformer_dir(tmp_path)
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("JARVIS_ASR_MODEL_DIR", raising=False)
    assert stt._default_paraformer_dir() == str(d)


def test_paraformer_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_ASR_MODEL_DIR", str(tmp_path))
    (tmp_path / "tokens.txt").write_text("<blk> 0\n", encoding="utf-8")
    (tmp_path / "model.onnx").write_bytes(b"fp32")
    assert stt._default_paraformer_dir() == str(tmp_path)


def test_paraformer_dir_ignores_incomplete_dir(tmp_path, monkeypatch):
    """只有词表没有模型，等于没装——必须跳过而不是返回一个坏目录。"""
    d = tmp_path / "models" / "asr" / "broken-2024-01-01"
    d.mkdir(parents=True)
    (d / "tokens.txt").write_text("<blk> 0\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("JARVIS_ASR_MODEL_DIR", raising=False)
    assert stt._default_paraformer_dir() is None


def test_paraformer_files_prefers_int8(tmp_path):
    (tmp_path / "tokens.txt").write_text("<blk> 0\n", encoding="utf-8")
    (tmp_path / "model.onnx").write_bytes(b"big")
    (tmp_path / "model.int8.onnx").write_bytes(b"small")
    model, tokens = stt._paraformer_files(str(tmp_path))
    assert model.endswith("model.int8.onnx")
    assert tokens.endswith("tokens.txt")


def test_paraformer_files_none_without_tokens(tmp_path):
    (tmp_path / "model.int8.onnx").write_bytes(b"x")
    assert stt._paraformer_files(str(tmp_path)) is None


def test_resample_doubles_length_at_double_rate():
    import numpy as np
    src = np.arange(8000, dtype=np.float32)
    out = stt._resample(src, 8000, 16000)
    assert len(out) == 16000
    # 端点必须保持，否则整段音频会被拉伸错位
    assert out[0] == 0 and abs(out[-1] - 7999) < 1


def test_resample_is_identity_when_rate_matches():
    import numpy as np
    src = np.arange(100, dtype=np.float32)
    out = stt._resample(src, 16000, 16000)
    assert len(out) == 100


def test_transcribe_paraformer_returns_none_without_model(monkeypatch):
    monkeypatch.setattr(stt, "_default_paraformer_dir", lambda: None)
    monkeypatch.setattr(stt, "_paraformer", None)
    monkeypatch.setattr(stt, "_PARAFORMER_LOADED", False)
    assert stt._transcribe_paraformer(b"\x00" * 3200, 16000) is None


def test_transcribe_wav_prefers_paraformer(monkeypatch):
    """中文指令识别优先走 Paraformer：它比 Vosk 准一档，不该排在后面。"""
    calls = []
    monkeypatch.setattr(
        stt, "_transcribe_paraformer", lambda pcm, sr: calls.append("paraformer") or "你好贾维斯"
    )
    monkeypatch.setattr(
        stt, "_transcribe_vosk", lambda pcm, sr: calls.append("vosk") or "你好假微丝"
    )
    monkeypatch.setattr(stt, "_transcribe_whisper", lambda pcm, sr: None)

    wav = _wav_bytes(16000, [0] * 1600)
    assert stt.transcribe_wav(wav) == "你好贾维斯"
    assert calls == ["paraformer"], "命中 Paraformer 后不该再跑 Vosk"


def test_transcribe_wav_falls_back_to_vosk(monkeypatch):
    monkeypatch.setattr(stt, "_transcribe_paraformer", lambda pcm, sr: None)
    monkeypatch.setattr(stt, "_transcribe_whisper", lambda pcm, sr: None)
    monkeypatch.setattr(stt, "_transcribe_vosk", lambda pcm, sr: "兜底结果")
    assert stt.transcribe_wav(_wav_bytes(16000, [0] * 1600)) == "兜底结果"


def _wav_bytes(sample_rate: int, samples: list[int]) -> bytes:
    """构造 16bit 单声道 WAV，供上面两条链路测试用。"""
    import struct, io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()
