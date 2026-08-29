import { useState, useRef, useCallback, useEffect } from "react";

interface UseSpeechRecognition {
  isAvailable: boolean;
  isListening: boolean;
  transcript: string;
  startListening: () => void;
  stopListening: () => void;
  error: string | null;
}

export default function useSpeechRecognition(): UseSpeechRecognition {
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognition | null>(null);

  const SpeechRecognitionCtor =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  const isAvailable = !!SpeechRecognitionCtor;

  useEffect(() => {
    if (!SpeechRecognitionCtor) return;

    const recognition = new SpeechRecognitionCtor();
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let finalTranscript = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finalTranscript += result[0].transcript;
        }
      }
      if (finalTranscript) {
        setTranscript((prev) => prev + finalTranscript);
      }
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      const err = event.error;
      if (err === "no-speech") {
        // No speech detected, not a real error — just stop
        setIsListening(false);
        return;
      }
      if (err === "aborted") {
        // User-initiated stop
        setIsListening(false);
        return;
      }
      if (err === "not-allowed") {
        setError("麦克风权限被拒绝，请在浏览器设置中允许麦克风访问");
        setIsListening(false);
        return;
      }
      setError(`语音识别错误: ${err}`);
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognition;

    return () => {
      recognition.abort();
    };
  }, [SpeechRecognitionCtor]);

  const startListening = useCallback(() => {
    if (!recognitionRef.current) {
      setError("浏览器不支持语音识别");
      return;
    }
    setError(null);
    setTranscript("");
    try {
      recognitionRef.current.start();
      setIsListening(true);
    } catch {
      setError("无法启动语音识别");
    }
  }, []);

  const stopListening = useCallback(() => {
    if (recognitionRef.current && isListening) {
      recognitionRef.current.stop();
    }
  }, [isListening]);

  return {
    isAvailable,
    isListening,
    transcript,
    startListening,
    stopListening,
    error,
  };
}
