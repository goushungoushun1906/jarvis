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
  /** 分句播报：把刚生成的整句排进队列，边生成边念。 */
  enqueue(content: string): void;
  /**
   * 是否"正在出声"——覆盖**合成中 + 播放中**两段。
   *
   * 为什么不能只看 isPlaying：TTS 字节到了浏览器才置 isPlaying=true，
   * 而"流完 → 合成 1.8s → 才播"这段空档里 isPlaying 是 false，连续对话
   * 就在这个空档开录，把自己的播报声录了进去（串音的根源）。
   */
  speaking: boolean;
  speakingRef: React.MutableRefObject<boolean>;
  stop(): void;
  /** 录音转写结果 → 交给外部发送，同时回填输入框。 */
  handleTranscript(text: string, send: (text: string) => void): void;
  /** TTS 播放中的实时音量（0..1），每帧更新，供形象动画读取。 */
  levelRef: React.MutableRefObject<number>;
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
  const { play: playAudio, stop: stopAudio, isPlaying, levelRef } = useAudioPlayer();
  const lastPlayedRef = useRef<string>("");

  const voiceModeRef = useRef<VoiceMode>("auto");
  const selectedVoiceRef = useRef<string>("");

  useEffect(() => {
    voiceModeRef.current = voiceMode;
  }, [voiceMode]);
  useEffect(() => {
    selectedVoiceRef.current = selectedVoice;
  }, [selectedVoice]);

  // "正在出声"：覆盖**合成中 + 播放中**全程（详见接口注释）。
  const [speaking, setSpeaking] = useState(false);
  const speakingRef = useRef(false);

  // 播放结束 → 状态回落 idle。
  // 必须连 speaking 一起看：分句播报时两句之间有切换空档，
  // 那会儿 isPlaying 已经是 false，只看它会让状态在 speaking/idle 之间闪。
  useEffect(() => {
    if (!isPlaying && !speakingRef.current && javisStatus === "speaking") {
      setJavisStatus("idle");
    }
  }, [isPlaying, speaking, javisStatus, setJavisStatus]);

  // ── 分句播报队列 ────────────────────────────────────────────────
  // 旧行为是"整段回答全部生成完才合成、才播"，用户体感就是「思考完还要
  // 再干等一两秒才出声」。现在 LLM 每吐出一个完整句子就立刻合成播放，
  // 后面的句子排队跟着念——首句出声时间从"整段生成完"提前到"第一句生成完"。
  const queueRef = useRef<string[]>([]);
  const playingRef = useRef(false);
  // 每次清空队列都换一代：旧的异步合成回调回来时会被判定为"过期"而丢弃，
  // 避免手动播放把上一轮还在排队的句子接着念出来。
  const genRef = useRef(0);

  const setSpeakingSafe = useCallback((value: boolean) => {
    speakingRef.current = value;
    setSpeaking(value);
  }, []);

  const advanceQueue = useCallback(async () => {
    if (playingRef.current) return;
    const mine = genRef.current;
    const next = queueRef.current.shift();
    if (next === undefined || next.trim() === "") {
      if (genRef.current === mine) setSpeakingSafe(false);
      return;
    }
    playingRef.current = true;
    try {
      const blob = await synthesizeSpeech(
        stripMarkdown(next),
        selectedVoiceRef.current || undefined
      );
      if (genRef.current !== mine) return; // 已被新的播放清掉
      lastPlayedRef.current = next;
      setJavisStatus("speaking");
      playAudio(blob, () => {
        playingRef.current = false;
        void advanceQueue();
      });
    } catch {
      // 单句失败不能卡住整条队列，跳过继续念下一句
      playingRef.current = false;
      void advanceQueue();
    }
  }, [playAudio, setJavisStatus, setSpeakingSafe]);

  const enqueue = useCallback(
    (content: string) => {
      const text = content.trim();
      if (!text) return;
      setSpeakingSafe(true);
      queueRef.current.push(text);
      void advanceQueue();
    },
    [advanceQueue, setSpeakingSafe]
  );

  const play = useCallback(
    async (content: string) => {
      if (!content.trim()) return;
      // 手动播放 = 打断当前所有播报，只念这一条
      stopAudio();
      genRef.current += 1;
      playingRef.current = false;
      queueRef.current = [];
      enqueue(content);
    },
    [enqueue, stopAudio]
  );

  const stop = useCallback(() => {
    stopAudio();
    genRef.current += 1;
    playingRef.current = false;
    queueRef.current = [];
    setSpeakingSafe(false);
    setJavisStatus("idle");
  }, [stopAudio, setJavisStatus, setSpeakingSafe]);

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
    speaking,
    speakingRef,
    setVoiceMode,
    setSelectedVoice,
    setListening,
    play,
    enqueue,
    stop,
    handleTranscript,
    levelRef,
  };
}
