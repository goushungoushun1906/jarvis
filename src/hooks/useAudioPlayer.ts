import { useState, useRef, useCallback, useEffect } from "react";

interface UseAudioPlayer {
  play: (audioUrl: string | Blob) => void;
  stop: () => void;
  isPlaying: boolean;
  currentBlob: Blob | null;
}

export default function useAudioPlayer(): UseAudioPlayer {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentBlob, setCurrentBlob] = useState<Blob | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, []);

  const play = useCallback((audioUrl: string | Blob) => {
    // Stop previous playback
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }

    let url: string;
    if (audioUrl instanceof Blob) {
      url = URL.createObjectURL(audioUrl);
      blobUrlRef.current = url;
      setCurrentBlob(audioUrl);
    } else {
      url = audioUrl;
      setCurrentBlob(null);
    }

    const audio = new Audio(url);
    audio.onended = () => {
      setIsPlaying(false);
    };
    audio.onerror = () => {
      setIsPlaying(false);
    };
    audio.onplay = () => {
      setIsPlaying(true);
    };

    audioRef.current = audio;
    audio.play().catch(() => {
      setIsPlaying(false);
    });
  }, []);

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
    setIsPlaying(false);
    setCurrentBlob(null);
  }, []);

  return { play, stop, isPlaying, currentBlob };
}
