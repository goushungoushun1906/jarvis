"""
Record real audio from microphone, upload to JARVIS STT endpoint.
Tests the full pipeline with real speech.
"""
import sys, os, wave, json, urllib.request, time, struct, io

sys.path.insert(0, r"C:\Users\HONOR\Documents\trae work09\jarvis\server\libs")

STT_URL = "http://127.0.0.1:18200/api/voice/stt"
TEMP_DIR = r"C:\Users\HONOR\Documents\trae work09"
WAV_PATH = os.path.join(TEMP_DIR, "test_recording.wav")

def record_with_pyaudio(duration=4, sr=16000):
    try:
        import pyaudio
    except ImportError:
        print("Installing pyaudio...")
        os.system(f'pip install pyaudio --target "{TEMP_DIR}"')
        try:
            sys.path.insert(0, TEMP_DIR)
            import pyaudio
        except ImportError:
            return None

    print(f"\n=== Recording {duration}s — Please speak now! ===")
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=sr,
                    input=True, frames_per_buffer=4096)
    frames = []
    for _ in range(0, int(sr / 4096 * duration)):
        frames.append(stream.read(4096))
    stream.stop_stream()
    stream.close()
    p.terminate()

    with wave.open(WAV_PATH, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        for f in frames:
            wf.writeframes(f)
    with open(WAV_PATH, "rb") as f:
        return f.read()

def record_with_sounddevice(duration=4, sr=16000):
    import sounddevice as sd
    print(f"\n=== Recording {duration}s — Please speak now! ===")
    audio = sd.rec(int(duration * sr), samplerate=sr, channels=1, dtype='int16')
    sd.wait()
    with wave.open(WAV_PATH, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(audio.tobytes())
    with open(WAV_PATH, "rb") as f:
        return f.read()

def upload_stt(wav_bytes):
    boundary = "----RealTest123"
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="audio"; filename="recording.wav"\r\n'
            f"Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(STT_URL, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    start = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
        return {"status": resp.status, "ms": round((time.time()-start)*1000),
                "text": result.get("text",""), "info": result.get("info","")}

def main():
    print("=== Real Audio STT Test ===")
    wav = None
    for fn in [record_with_sounddevice, record_with_pyaudio]:
        try:
            wav = fn()
            if wav and len(wav) > 1000: break
        except Exception as e:
            print(f"  {fn.__name__} failed: {e}")
    if not wav:
        print("No microphone access. Using synthetic audio.")
        n = 4*16000; buf = io.BytesIO()
        with wave.open(buf,"wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
            for i in range(n):
                v = int(16000*(0.4*((i%160)/160*2-1)+0.3*((i%80)/80*2-1))) if (i%3200)<2000 else 0
                wf.writeframes(struct.pack("<h",max(-32767,min(32767,v))))
        buf.seek(0); wav = buf.read()
    print(f"\nWAV: {len(wav)} bytes")
    try:
        r = upload_stt(wav)
        print(f"Status: {r['status']}  Time: {r['ms']}ms  Text: '{r['text']}'")
        if r.get("info"): print(f"Info: {r['info']}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
