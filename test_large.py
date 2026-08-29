"""Test large model recognition with real audio."""
import sys, os, wave, time
sys.path.insert(0, "server")
sys.path.insert(0, "server/libs")

from app.stt import transcribe_wav, _MODEL_PATH
print(f"Model: {os.path.basename(_MODEL_PATH)}")

import pyaudio
print("Recording 4s... Please speak NOW!")
p = pyaudio.PyAudio()
stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                input=True, frames_per_buffer=4096)
frames = [stream.read(4096) for _ in range(int(16000/4096*4))]
stream.stop_stream(); stream.close(); p.terminate()

buf = "server/test_large.wav"
with wave.open(buf, "wb") as wf:
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
    for f in frames: wf.writeframes(f)
with open(buf, "rb") as fp:
    wav = fp.read()
print(f"WAV: {len(wav)} bytes")

start = time.time()
text = transcribe_wav(wav)
print(f"Result ({int((time.time()-start)*1000)}ms): \"{text}\"")
