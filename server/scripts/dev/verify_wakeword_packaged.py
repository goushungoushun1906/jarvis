"""验证打包后的语音唤醒是否真的可用。

为什么需要这个脚本
------------------
语音唤醒的打包失效是"静默"的：模型文件、原生库、内置关键词目录里少任何一个，
后端都不会报错，只是"喊了没反应"。更糟的是 pvporcupine 在 import 阶段就要
列举内置关键词目录，那个目录漏掉会直接让整个后端起不来 —— 表现为应用根本
启动，而不是唤醒失灵，排查方向会完全跑偏。

这个脚本对真实的 backend.exe 做四件事：

1. 拉起打包产物（或 --dev 跑开发态）
2. 读 /api/wakeword/info，逐项核对打包自检（self_check）
3. 确认能枚举到麦克风设备（证明录音库正常）
4. 上传一段含唤醒词的音频走离线回放，断言能命中（证明模型真的配套）

用法：
    python scripts/dev/verify_wakeword_packaged.py [--exe PATH] [--dev] [--wav PATH]

退出码 0 表示全部通过，非 0 表示有失败项（会在输出里标出是哪一条）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import wave

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_EXE = os.path.join(SERVER_DIR, "backend-dist", "backend.exe")

SAMPLE_TEXT = "你好贾维斯"
FAILURES: list[str] = []


def resolve_port() -> int:
    """用独立端口，避免撞上正在运行的 JARVIS（默认 18200）。"""
    return int(os.environ.get("JARVIS_VERIFY_PORT", "18233"))


def check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def start_backend(use_dev: bool, exe_path: str) -> subprocess.Popen:
    if use_dev:
        venv_py = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")
        if not os.path.exists(venv_py):
            venv_py = sys.executable
        cmd = [venv_py, "-m", "uvicorn", "main:app",
               "--host", "127.0.0.1", "--port", str(resolve_port())]
        cwd = SERVER_DIR
    else:
        cmd = [exe_path]
        cwd = os.path.dirname(exe_path) or "."
    env = {**os.environ, "SERVER_PORT": str(resolve_port())}
    if not use_dev:
        # 打包版默认要求 per-request token；本脚本只关心唤醒链路，显式关掉。
        env["JARVIS_DISABLE_API_TOKEN"] = "1"
    return subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(base: str, timeout: int = 240) -> bool:
    """打包版要先把 ~300MB 解压到 _MEIPASS，启动比 dev 慢得多。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3, trust_env=False) as c:
                if c.get(f"{base}/health").status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def build_sample_wav(path: str) -> str | None:
    """合成一段唤醒词音频。没有 edge_tts / ffmpeg 时返回 None。

    回放测试需要的是"真的说了这句话"的音频；纯正弦波只能证明链路通，
    证明不了模型配套，所以这里宁可跳过也不造假。
    """
    import shutil
    import asyncio
    import tempfile

    if not shutil.which("ffmpeg"):
        return None
    mp3 = os.path.join(tempfile.gettempdir(), "jarvis_wake_sample.mp3")
    try:
        import edge_tts

        async def synth() -> None:
            comm = edge_tts.Communicate(SAMPLE_TEXT, "zh-CN-XiaoxiaoNeural")
            await comm.save(mp3)

        asyncio.run(synth())
    except Exception as e:
        print(f"  [SKIP] 无法合成样本音频（{e}）")
        return None

    subprocess.run(
        ["ffmpeg", "-y", "-i", mp3, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return path if os.path.isfile(path) else None


def make_silent_wav(path: str, seconds: float = 1.0) -> str:
    """兜底用的静音音频：至少能证明采样率格式处理是对的。"""
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00" * int(16000 * seconds * 2))
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", default=DEFAULT_EXE, help="backend.exe 路径")
    parser.add_argument("--dev", action="store_true", help="改用开发态 uvicorn")
    parser.add_argument("--wav", default="", help="自备的 16k 单声道 wav")
    args = parser.parse_args()

    if not args.dev and not os.path.isfile(args.exe):
        print(f"找不到打包产物：{args.exe}")
        print("先执行打包，或加 --dev 用开发态验证。")
        return 2

    base = f"http://127.0.0.1:{resolve_port()}"
    print(f"启动后端（{base}）…")
    proc = start_backend(args.dev, args.exe)
    try:
        if not wait_ready(base):
            print("后端未能在 240 秒内就绪")
            return 2

        with httpx.Client(timeout=15, trust_env=False) as client:
            # 1. 引擎状态与自检
            print("\n1) 唤醒引擎状态与打包自检")
            info = client.get(f"{base}/api/wakeword/info").json()
            print(f"    引擎：{info.get('engine')}　状态：{info.get('status')}")
            check("已配置 AccessKey", bool(info.get("access_key_set")))

            sc = info.get("self_check") or {}
            check("中文声学参数存在", bool(sc.get("model_exists")), sc.get("model_path", ""))
            check("关键词模型存在",
                  bool(sc.get("keyword_paths_exist") and all(sc["keyword_paths_exist"])),
                  ", ".join(sc.get("keyword_paths") or []))
            check("原生库存在", bool(sc.get("library_exists")), sc.get("library_path", ""))
            check("pvporcupine 可导入", bool(sc.get("pvporcupine_importable")))
            check("pyaudio 可导入", bool(sc.get("pyaudio_importable")))

            # 2. 设备枚举
            print("\n2) 麦克风设备")
            try:
                devices = client.get(f"{base}/api/wakeword/devices").json().get("devices", [])
                check("能枚举到输入设备", len(devices) > 0, f"共 {len(devices)} 个")
            except Exception as e:
                check("能枚举到输入设备", False, str(e))

            # 3. 离线回放
            print("\n3) 离线回放判定")
            wav_path = args.wav
            if not wav_path:
                candidate = os.path.join(
                    os.environ.get("TEMP", "."), "jarvis_wake_sample.wav"
                )
                wav_path = build_sample_wav(candidate) or ""
            if wav_path and os.path.isfile(wav_path):
                with open(wav_path, "rb") as f:
                    files = {"file": ("sample.wav", f, "audio/wav")}
                    try:
                        r = client.post(f"{base}/api/wakeword/test", files=files)
                        r.raise_for_status()
                        result = r.json()
                        check("回放命中唤醒词", bool(result.get("hit")),
                              result.get("keyword", "") or "")
                    except Exception as e:
                        check("回放命中唤醒词", False, str(e))
            else:
                print("  [SKIP] 没有可用于回放的音频样本，跳过（不影响前两项结论）")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} 项未通过：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
