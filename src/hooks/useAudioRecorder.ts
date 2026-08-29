import { useState, useRef, useCallback, useEffect } from "react";

interface UseAudioRecorder {
  isAvailable: boolean;
  isRecording: boolean;
  audioBlob: Blob | null;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  error: string | null;
  debugInfo: { chunks: number; duration: number; streamActive: boolean; hasAudio: boolean };
}

/**
 * Records audio using MediaRecorder API + AudioWorklet for raw PCM capture.
 * Uses the device's native sample rate to avoid resampling/clipping artifacts.
 * Audio is resampled to 16kHz mono in JS before encoding as WAV.
 */
export default function useAudioRecorder(): UseAudioRecorder {
  const [isRecording, setIsRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [debugInfo, setDebugInfo] = useState({ chunks: 0, duration: 0, streamActive: false, hasAudio: false });

  const audioContextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startTimeRef = useRef<number>(0);
  const nativeSampleRateRef = useRef<number>(48000);

  const isAvailable =
    typeof navigator !== "undefined" &&
    typeof AudioContext !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia;

  /** Resample from native rate to 16kHz using linear interpolation */
  const resampleTo16k = (input: Float32Array, inputRate: number): Float32Array => {
    if (inputRate === 16000) return input;
    const ratio = inputRate / 16000;
    const outputLength = Math.round(input.length / ratio);
    const output = new Float32Array(outputLength);
    for (let i = 0; i < outputLength; i++) {
      const srcIndex = i * ratio;
      const idx = Math.floor(srcIndex);
      const frac = srcIndex - idx;
      if (idx + 1 < input.length) {
        output[i] = input[idx] * (1 - frac) + input[idx + 1] * frac;
      } else {
        output[i] = input[idx] || 0;
      }
    }
    return output;
  };

  /** Encode Float32Array samples as WAV blob (16-bit PCM, mono, 16kHz) */
  const encodeWAV = (samples: Float32Array[]): Blob => {
    // Merge all chunks
    const totalLength = samples.reduce((acc, c) => acc + c.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of samples) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }

    const sampleRate = 16000;
    const numChannels = 1;
    const bytesPerSample = 2;
    const dataSize = merged.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    // WAV header
    const writeStr = (off: number, s: string) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
    writeStr(0, "RIFF");
    view.setUint32(4, 36 + dataSize, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * numChannels * bytesPerSample, true);
    view.setUint16(32, numChannels * bytesPerSample, true);
    view.setUint16(34, bytesPerSample * 8, true);
    writeStr(36, "data");
    view.setUint32(40, dataSize, true);

    // PCM data
    for (let i = 0; i < merged.length; i++) {
      const s = Math.max(-1, Math.min(1, merged[i]));
      view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff);
    }

    return new Blob([buffer], { type: "audio/wav" });
  };

  const startRecording = useCallback(async () => {
    setError(null);
    setAudioBlob(null);
    chunksRef.current = [];

    try {
      // Request mic WITHOUT forcing sampleRate — let the browser use native rate
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Do NOT specify sampleRate or channelCount — use device defaults
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      streamRef.current = stream;

      // Use AudioContext at the device's native sample rate
      const ctx = new AudioContext();
      audioContextRef.current = ctx;
      nativeSampleRateRef.current = ctx.sampleRate;

      const source = ctx.createMediaStreamSource(stream);
      sourceRef.current = source;

      // Use ScriptProcessorNode (deprecated but widely supported)
      const bufferSize = 4096;
      const processor = ctx.createScriptProcessor(bufferSize, 1, 1);

      processor.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);

        // Resample to 16kHz
        const resampled = resampleTo16k(input, ctx.sampleRate);
        chunksRef.current.push(resampled);

        // Debug: check signal
        let hasSignal = false;
        let maxVal = 0;
        for (let i = 0; i < resampled.length; i++) {
          const a = Math.abs(resampled[i]);
          if (a > 0.01) hasSignal = true;
          if (a > maxVal) maxVal = a;
        }
        setDebugInfo({
          chunks: chunksRef.current.length,
          duration: Math.round((Date.now() - startTimeRef.current) / 100) / 10,
          streamActive: true,
          hasAudio: hasSignal,
          // @ts-expect-error adding maxAmp for debug
          maxAmp: (maxVal * 100).toFixed(0),
        });
      };

      source.connect(processor);
      processor.connect(ctx.destination);
      startTimeRef.current = Date.now();
      setIsRecording(true);

      timerRef.current = setTimeout(() => stopRecording(), 30000);
    } catch (err: any) {
      if (err.name === "NotAllowedError") {
        setError("麦克风权限被拒绝，请在浏览器设置中允许");
      } else if (err.name === "NotFoundError") {
        setError("未找到麦克风设备");
      } else {
        setError(`无法启动录音: ${err.message}`);
      }
      setIsRecording(false);
    }
  }, []);

  const stopRecording = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }

    if (sourceRef.current) { sourceRef.current.disconnect(); sourceRef.current = null; }
    if (audioContextRef.current) { audioContextRef.current.close(); audioContextRef.current = null; }
    if (streamRef.current) { streamRef.current.getTracks().forEach((t) => t.stop()); streamRef.current = null; }

    if (chunksRef.current.length > 0) {
      const blob = encodeWAV(chunksRef.current);
      setAudioBlob(blob);
    }

    setIsRecording(false);
    setDebugInfo((d) => ({ ...d, streamActive: false }));
  }, []);

  useEffect(() => {
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, []);

  return { isAvailable, isRecording, audioBlob, startRecording, stopRecording, error, debugInfo };
}
