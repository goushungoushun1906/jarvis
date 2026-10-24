"""离线回放验证：证明 sherpa 唤醒链路「该响的时候响、不该响的时候不响」。

实喊冒烟要人参与，跑一次几分钟；这个脚本无人值守，几秒出结果，适合改完模型、
改完唤醒词、改完阈值之后立刻自检一遍。

两组断言：

    负样本  模型自带的 test_wavs/ 说的是「文森特卡索」「周望军」等无关内容，
            一条都不该命中。命中就说明阈值太松或唤醒词太短。
    正样本  用 Windows 自带 TTS（SAPI）现场合成一遍唤醒词再回放。TTS 音色和
            真人有差距，所以这里只做**存在性**断言：合成音都认不出来，
            真人就更没戏，必须回头查唤醒词的拼音 token。

用法：
    python scripts/dev/verify_sherpa_offline.py                 # 负样本 + TTS 正样本
    python scripts/dev/verify_sherpa_offline.py --positive a.wav  # 指定正样本
    python scripts/dev/verify_sherpa_offline.py --threshold 0.2 # 顺带试阈值
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app import sherpa_runtime  # noqa: E402,F401  副作用：注册 DLL 目录
from app.wakeword import SherpaEngine, load_wake_config  # noqa: E402


def read_wav(path: str, target_rate: int = 16000):
    """读 wav → (采样率, float32 归一化样本)。非 16k 时线性重采样。"""
    import numpy as np

    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())

    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != target_rate:
        length = int(round(len(data) * target_rate / rate))
        idx = np.linspace(0, len(data) - 1, length)
        data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        rate = target_rate
    return rate, data


def synth_tts(text: str, out_path: str) -> bool:
    """用 Windows SAPI 合成中文语音到 wav。失败返回 False。"""
    try:
        import win32com.client
    except ImportError:
        print("  缺少 pywin32，无法合成，跳过正样本")
        return False

    try:
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        # 优先挑中文语音，否则合成出来的会是英文音素，测了等于没测
        for voice in speaker.GetVoices():
            desc = voice.GetDescription()
            if "Chinese" in desc or "中文" in desc or "Huihui" in desc or "Yaoyao" in desc:
                speaker.Voice = voice
                print(f"  TTS 语音：{desc}")
                break

        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Open(out_path, 3)  # SSFMCreateForWrite
        speaker.AudioOutputStream = stream
        speaker.Speak(text)
        stream.Close()
        return os.path.isfile(out_path) and os.path.getsize(out_path) > 1024
    except Exception as e:
        print(f"  TTS 合成失败：{e}")
        return False


def run_wav(engine: SherpaEngine, path: str, frame: int) -> tuple[bool, int]:
    """回放一个 wav，返回 (是否命中, 命中时的帧序号)。"""
    rate, samples = read_wav(path, engine.sample_rate)
    pcm = (samples * 32768.0).astype("int16")
    for i in range(0, len(pcm) - frame + 1, frame):
        index, _score = engine.process(pcm[i : i + frame])
        if index >= 0:
            return True, i // frame
    return False, -1


def main() -> int:
    parser = argparse.ArgumentParser(description="sherpa 唤醒离线回放验证")
    parser.add_argument("--positive", default="", help="正样本 wav，缺省用 TTS 合成")
    parser.add_argument("--threshold", type=float, default=None, help="覆盖触发阈值")
    parser.add_argument("--model-dir", default="", help="模型目录，默认自动发现")
    args = parser.parse_args()

    cfg = load_wake_config()
    if args.model_dir.strip():
        os.environ["JARVIS_SHERPA_MODEL_DIR"] = args.model_dir.strip()
        cfg = load_wake_config()
    if cfg.engine != "sherpa":
        print(f"当前引擎是 {cfg.engine}，本脚本只针对 sherpa。")
        return 1
    if not cfg.sherpa_ready:
        print("模型未就绪，先跑： python scripts/dev/download_sherpa_kws.py")
        return 1
    if args.threshold is not None:
        cfg.sherpa_threshold = args.threshold

    keyword = cfg.keyword_names[0] if cfg.keyword_names else "?"
    print("=" * 56)
    print("sherpa 离线回放验证")
    print("=" * 56)
    print(f"唤醒词   : {', '.join(cfg.keyword_names) or '(未配置)'}")
    print(f"触发阈值 : {cfg.sherpa_threshold}")
    print(f"关键词表 : {cfg.sherpa_keywords_file}")
    print()

    engine = SherpaEngine(cfg)
    ok = True

    try:
        # --- 负样本 ---
        wav_dir = os.path.join(os.path.dirname(cfg.sherpa_tokens), "test_wavs")
        negatives = sorted(glob.glob(os.path.join(wav_dir, "*.wav")))
        if not negatives:
            print("未找到负样本目录，跳过：", wav_dir)
        else:
            print(f"--- 负样本 {len(negatives)} 条（都不该命中）---")
            for path in negatives:
                hit, frame_no = run_wav(engine, path, engine.frame_length)
                mark = "命中(误报)" if hit else "未命中"
                print(f"  {os.path.basename(path):12s} {mark}")
                if hit:
                    ok = False
                    print(f"      ↑ 在第 {frame_no} 帧误触发，阈值需要调大")

        # --- 正样本 ---
        print("\n--- 正样本 ---")
        positive = args.positive.strip()
        tmp = ""
        if not positive:
            handle, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(handle)
            if synth_tts(keyword, tmp):
                positive = tmp
            else:
                print("  （无正样本可测，请用 --positive 指定录音）")
        if positive:
            hit, frame_no = run_wav(engine, positive, engine.frame_length)
            print(f"  {os.path.basename(positive)} → {'命中' if hit else '未命中'}")
            if not hit:
                ok = False
                print("      ↑ 合成音都认不出，检查 keywords.txt 的拼音 token 是否正确")
    finally:
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        engine.close()

    print("\n" + ("离线回放验证通过" if ok else "离线回放验证未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
