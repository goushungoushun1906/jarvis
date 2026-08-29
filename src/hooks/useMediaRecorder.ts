import { useState, useRef, useCallback, useEffect } from "react";

interface UseMediaRecorder {
  isAvailable: boolean;
  isRecording: boolean;
  audioBlob: Blob | null;
  startRecording: () => Promise<void>;
  stopRecording: () => void;
  error: string | null;
}

export default function useMediaRecorder(): UseMediaRecorder {
  const [isRecording, setIsRecording] = useState(false);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isAvailable =
    typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

  const startRecording = useCallback(async () => {
    setError(null);
    setAudioBlob(null);
    chunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      // Use webm/opus codec (widely supported, good quality, small size)
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "audio/mp4";

      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mimeType });
        setAudioBlob(blob);
        setIsRecording(false);

        // Clean up stream
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
      };

      recorder.onerror = () => {
        setError("录音错误");
        setIsRecording(false);
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
      };

      recorder.start(100); // Collect data every 100ms
      setIsRecording(true);

      // Auto-stop after 30 seconds max
      timerRef.current = setTimeout(() => {
        if (mediaRecorderRef.current?.state === "recording") {
          recorder.stop();
        }
      }, 30000);
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
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

  return {
    isAvailable,
    isRecording,
    audioBlob,
    startRecording,
    stopRecording,
    error,
  };
}
