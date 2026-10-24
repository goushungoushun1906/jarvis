import { useState, useRef, useCallback, useEffect } from "react";

interface UseAudioPlayer {
  /** 播完或出错都会回调 onEnded，上层靠它推进队列 / 清零"在出声"状态。 */
  play: (audioUrl: string | Blob, onEnded?: () => void) => void;
  stop: () => void;
  isPlaying: boolean;
  currentBlob: Blob | null;
  /**
   * 播放中的实时音量（0..1），由 WebAudio AnalyserNode 每帧更新。
   *
   * 刻意用 ref 而不是 state：这个值每帧都在变，走 state 会让整棵组件树
   * 以 60fps 重渲染。动画侧自己每帧读 ref.current 即可。
   */
  levelRef: React.MutableRefObject<number>;
}

export default function useAudioPlayer(): UseAudioPlayer {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentBlob, setCurrentBlob] = useState<Blob | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);

  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaElementAudioSourceNode | null>(null);
  const levelRef = useRef<number>(0);
  const rafRef = useRef<number>(0);
  const bufRef = useRef<Uint8Array | null>(null);

  const stopLevelLoop = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    levelRef.current = 0;
  }, []);

  // 每帧采样一次时域数据，算出 RMS 落到 levelRef
  const startLevelLoop = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    const tick = () => {
      const analyser = analyserRef.current;
      if (analyser) {
        if (!bufRef.current || bufRef.current.length !== analyser.fftSize) {
          bufRef.current = new Uint8Array(analyser.fftSize);
        }
        analyser.getByteTimeDomainData(bufRef.current as Uint8Array<ArrayBuffer>);
        let sum = 0;
        for (let i = 0; i < bufRef.current.length; i++) {
          const v = (bufRef.current[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / bufRef.current.length);
        // 语音 RMS 通常在 0.02~0.2，放大到 0..1 便于驱动动画
        levelRef.current = Math.min(1, rms * 4.5);
      }
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }, []);

  const ensureAudioContext = useCallback(() => {
    if (audioCtxRef.current) return audioCtxRef.current;
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext })
        .webkitAudioContext;
    if (!Ctor) return null;
    try {
      const ctx = new Ctor();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.6;
      analyser.connect(ctx.destination);
      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
      return ctx;
    } catch {
      return null;
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopLevelLoop();
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {});
        audioCtxRef.current = null;
      }
    };
  }, [stopLevelLoop]);

  const play = useCallback(
    (audioUrl: string | Blob, onEnded?: () => void) => {
      // Stop previous playback
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.onended = null;
        audioRef.current.onerror = null;
      }
      if (sourceRef.current) {
        try {
          sourceRef.current.disconnect();
        } catch {
          // 已断开
        }
        sourceRef.current = null;
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
      // 播完/出错都走同一条收尾路径，并回调上层——上层靠它把"回答进行中"
      // 的状态真正清零（分句播报时还要靠它推进下一句）。
      const settle = () => {
        setIsPlaying(false);
        stopLevelLoop();
        onEnded?.();
      };
      audio.onended = settle;
      audio.onerror = settle;
      audio.onplay = () => {
        setIsPlaying(true);
      };

      // 接上 analyser 才能拿到真实音量（失败也不影响播放）
      try {
        const ctx = ensureAudioContext();
        if (ctx && analyserRef.current) {
          if (ctx.state === "suspended") void ctx.resume();
          const src = ctx.createMediaElementSource(audio);
          src.connect(analyserRef.current);
          sourceRef.current = src;
          startLevelLoop();
        }
      } catch {
        // 某些环境（无音频设备）建不了 AudioContext，静默降级
      }

      audioRef.current = audio;
      audio.play().catch(() => {
        setIsPlaying(false);
        stopLevelLoop();
      });
    },
    [ensureAudioContext, startLevelLoop, stopLevelLoop]
  );

  const stop = useCallback(() => {
    stopLevelLoop();
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current = null;
    }
    if (sourceRef.current) {
      try {
        sourceRef.current.disconnect();
      } catch {
        // 已断开
      }
      sourceRef.current = null;
    }
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
    setIsPlaying(false);
    setCurrentBlob(null);
  }, [stopLevelLoop]);

  return { play, stop, isPlaying, currentBlob, levelRef };
}
