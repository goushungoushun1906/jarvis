import { useCallback, useEffect, useRef, useState } from "react";
import type { VoiceMode, JavisStatus } from "../lib/types";
import { synthesizeSpeech } from "../lib/api";
import { stripMarkdown } from "../lib/stripMarkdown";
import useAudioPlayer from "./useAudioPlayer";

export interface VoicePipeline {
  voiceMode: VoiceMode;
  voiceModeRef: React.MutableRefObject<VoiceMode>;
  selectedVoice: string;
  selectedVoiceRef: React.MutableRefObject<string>;
  isListening: boolean;
  isPlaying: boolean;
  setVoiceMode(mode: VoiceMode): void;
  setSelectedVoice(voice: string): void;
  setListening(value: boolean): void;
  /** 手动播放某条消息（不经过流式管线）。 */
  play(content: string): Promise<void>;
  stop(): void;
  /** 录音转写结果 → 交给外部发送，同时回填输入框。 */
  handleTranscript(text: string, send: (text: string) => void): void;
}

/**
 * 语音管线：TTS 合成 + 播放状态 + 麦克风转写入口。
 *
 * `javisStatus` 由外部传入并在此处推进——"speaking" 属于语音状态机的一部分。
 */
export function useVoicePipeline(
  javisStatus: JavisStatus,
  setJavisStatus: (status: JavisStatus) => void
): VoicePipeline {
  const [voiceMode, setVoiceMode] = useState<VoiceMode>("auto");
  const [selectedVoice, setSelectedVoice] = useState<string>("");
  const [isListening, setListening] = useState(false);
  const { play: playAudio, stop: stopAudio, isPlaying } = useAudioPlayer();
  const lastPlayedRef = useRef<string>("");

  const voiceModeRef = useRef<VoiceMode>("auto");
  const selectedVoiceRef = useRef<string>("");

  useEffect(() => {
    voiceModeRef.current = voiceMode;
  }, [voiceMode]);
  useEffect(() => {
    selectedVoiceRef.current = selectedVoice;
  }, [selectedVoice]);

  // 播放结束 → 状态回落 idle
  useEffect(() => {
    if (!isPlaying && javisStatus === "speaking") {
      setJavisStatus("idle");
    }
  }, [isPlaying, javisStatus, setJavisStatus]);

  const play = useCallback(
    async (content: string) => {
      try {
        const blob = await synthesizeSpeech(
          stripMarkdown(content),
          selectedVoiceRef.current || undefined
        );
        lastPlayedRef.current = content;
        playAudio(blob);
        setJavisStatus("speaking");
      } catch {
        // TTS 失败保持静默，不打扰主流程
      }
    },
    [playAudio, setJavisStatus]
  );

  const stop = useCallback(() => {
    stopAudio();
    setJavisStatus("idle");
  }, [stopAudio, setJavisStatus]);

  const handleTranscript = useCallback((text: string, send: (t: string) => void) => {
    if (!text.trim()) return;
    send(text.trim());
  }, []);

  return {
    voiceMode,
    voiceModeRef,
    selectedVoice,
    selectedVoiceRef,
    isListening,
    isPlaying,
    setVoiceMode,
    setSelectedVoice,
    setListening,
    play,
    stop,
    handleTranscript,
  };
}
