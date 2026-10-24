import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import type { JavisStatus } from "../../lib/types";
import {
  getSharedMotionRefs,
  type JarvisState,
  type SpeakPhase,
  type Tier,
} from "./motionRefs";
import {
  useJarvisMotion,
  useMicLevel,
  useStatusAnnouncer,
  type MicStatus,
} from "./useJarvisMotion";
import EnergyCore from "./EnergyCore";
import StatusBar from "./StatusBar";
import WaveformBar from "./WaveformBar";
import HudBackground from "./HudBackground";
import "./hud-theme.css";
import "./JarvisHud.css";

/* ==========================================================================
   JarvisHud —— 组合根组件
   --------------------------------------------------------------------------
   · 全局唯一一个 rAF（useJarvisMotion），所有动效向它注册。
   · 每帧 setState = 0。
   · 对外只暴露简洁 props；需要 status 做文案/图标/aria 的只有 StatusBar
     与播报器，其余组件（EnergyCore / WaveformBar）**不接收 status 作为配色
     依据** —— 颜色由根元素 [data-status] 的 CSS 规则给（规范 §9.3）。
   ========================================================================== */

/**
 * 编译期断言：JarvisState 与项目里的 JavisStatus 必须保持同构。
 * 若有人在 types.ts 里改了 JavisStatus 而忘了同步这里，下面这行会编译报错。
 */
type AssertSame<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const _STATE_SYNC: AssertSame<JarvisState, JavisStatus> = true;
void _STATE_SYNC;

export interface HudMessage {
  role: "user" | "assistant";
  text: string;
  time?: string;
}

export interface JarvisHudProps {
  /** 六态。 */
  status: JavisStatus;
  /** speaking 子阶段（stream / tts / null）。不传则用模块级共享 ref。 */
  phaseRef?: MutableRefObject<SpeakPhase>;
  /** 实时电平 0..1（麦克风或 TTS AnalyserNode）。不传则用合成包络兜底。 */
  levelRef?: MutableRefObject<number>;
  /** 频谱，长度 64。不传则由合成包络推导。 */
  bandsRef?: MutableRefObject<Float32Array>;
  /** 打断：停止播报 + 冻结文本流。内部会做 400ms 唤醒 cooldown。 */
  onInterrupt?: () => void;
  /** 强制动效降级开关；不传则跟随系统 prefers-reduced-motion。 */
  reducedMotion?: boolean;
  /** 强制性能档位；不传则自动探测 + 自动降档。 */
  tier?: Tier;
  /** 麦克风接入策略。默认 "auto"（仅在 listening 时开麦）。 */
  micMode?: "off" | "auto" | "always";
  /** 显示右上角「退出 HUD」。 */
  onExit?: () => void;
  /** 传入则六枚状态胶囊可点击（演示 / 调试用）。 */
  onStatusSelect?: (status: JavisStatus) => void;
  /** 可选：真实会话流（不传则不显示会话区）。 */
  messages?: HudMessage[];
  /** 可选：底部输入框发送回调。 */
  onSend?: (text: string) => void;
  /**
   * 按住 Space 说话：按下 / 松开。
   * 两个都传才会启用（少一个就不绑定键盘，避免"按了没反应"）。
   */
  onVoiceStart?: () => void;
  onVoiceStop?: () => void;
  /** 语音唤醒开关（接 useWakeWord）。 */
  wakeEnabled?: boolean;
  wakePhase?: "off" | "listening" | "armed" | "conversation";
  onWakeToggle?: () => void;
  /**
   * 打开完整面板（设置 / 新会话）。
   *
   * HUD 是纯播报界面，没有设置、会话管理、附件这些。这些功能都在下面的
   * 豆包风主界面里，所以回调是「退出 HUD + 打开对应面板」两步一起做。
   */
  onOpenPanel?: (panel: "settings" | "newchat") => void;
  /** 作为覆盖整个窗口的浮层挂载（默认 true）。 */
  overlay?: boolean;
  /** 中央核心尺寸（CSS px）。 */
  coreSize?: number;
  className?: string;
}

const MIC_HINT: Record<MicStatus, string> = {
  off: "MIC OFF",
  requesting: "MIC …",
  live: "MIC LIVE",
  denied: "MIC 无权限 · 合成包络",
  unsupported: "MIC 不支持 · 合成包络",
};

export default function JarvisHud({
  status,
  phaseRef,
  levelRef,
  bandsRef,
  onInterrupt,
  reducedMotion,
  tier,
  micMode = "auto",
  onExit,
  onStatusSelect,
  messages,
  onSend,
  onVoiceStart,
  onVoiceStop,
  wakeEnabled = false,
  wakePhase = "off",
  onWakeToggle,
  onOpenPanel,
  overlay = true,
  coreSize = 320,
  className = "",
}: JarvisHudProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const feedRef = useRef<HTMLDivElement | null>(null);
  const [input, setInput] = useState("");
  const [ptt, setPtt] = useState(false);
  const pttRef = useRef(false);

  const shared = getSharedMotionRefs();
  const motion = useJarvisMotion({
    rootRef,
    status,
    phaseRef: phaseRef ?? shared.phaseRef,
    levelRef: levelRef ?? shared.levelRef,
    bandsRef: bandsRef ?? shared.bandsRef,
    reducedMotion,
    tier,
  });

  const micActive =
    micMode === "always" ? status !== "offline" : micMode === "auto" ? status === "listening" : false;

  const mic = useMicLevel({
    registerSampler: motion.registerSampler,
    levelRef: levelRef ?? shared.levelRef,
    bandsRef: bandsRef ?? shared.bandsRef,
    active: micActive,
  });

  const announced = useStatusAnnouncer(status);

  /* ------------------- TTS 自激防线：进 thinking/speaking 即挂起唤醒 ------------------- */
  useEffect(() => {
    if (status === "thinking" || status === "speaking") motion.wakeGuard.suspend();
  }, [status, motion.wakeGuard]);

  /* ------------------- 打断：核心回弹 + 400ms cooldown ------------------- */
  const handleInterrupt = useCallback(() => {
    const el = rootRef.current;
    if (el) {
      el.dataset.interrupt = "1";
      window.setTimeout(() => {
        if (rootRef.current) delete rootRef.current.dataset.interrupt;
      }, 380);
    }
    // 打断后 400ms 才允许恢复唤醒监听，防 TTS 尾音被当成指令
    motion.wakeGuard.releaseAfterInterrupt();
    onInterrupt?.();
  }, [motion.wakeGuard, onInterrupt]);

  /* ------------------- Esc 打断（全局） ------------------- */
  useEffect(() => {
    if (!onInterrupt) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const t = e.target as HTMLElement | null;
      // 输入框非空时 Esc 优先清空输入，二次 Esc 才打断
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA") && input.trim()) {
        setInput("");
        return;
      }
      handleInterrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handleInterrupt, onInterrupt, input]);

  /* ------------------- 按住 Space 说话（前后端直连的 PTT） ------------------- */
  //
  // 只在「没在打字」时拦截 Space —— 输入框里 Space 就是空格，不能抢。
  // 失焦 / 切后台时强制松手，否则会卡在"一直录音"的状态。
  useEffect(() => {
    if (!onVoiceStart || !onVoiceStop) return;
    const isTyping = (t: EventTarget | null) => {
      const el = t as HTMLElement | null;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    };
    const release = () => {
      if (!pttRef.current) return;
      pttRef.current = false;
      setPtt(false);
      onVoiceStop();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code !== "Space" || e.repeat || isTyping(e.target)) return;
      e.preventDefault();
      pttRef.current = true;
      setPtt(true);
      onVoiceStart();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== "Space" || isTyping(e.target)) return;
      release();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", release);
      release();
    };
  }, [onVoiceStart, onVoiceStop]);

  /* ------------------- 会话流自动滚到底 ------------------- */
  const lastLen = useRef(0);
  useEffect(() => {
    const el = feedRef.current;
    if (!el || !messages) return;
    if (messages.length !== lastLen.current) {
      lastLen.current = messages.length;
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const canSpeak = status !== "offline";
  const showFeed = Array.isArray(messages);
  const bars = useMemo(() => {
    // 会话区存在时窄一点，让位给右侧面板
    return showFeed ? 420 : 480;
  }, [showFeed]);

  const submit = useCallback(() => {
    const text = input.trim();
    if (!text || !onSend) return;
    setInput("");
    onSend(text);
  }, [input, onSend]);

  return (
    <div
      className={`jhud${overlay ? " jhud--overlay" : ""} ${className}`}
      ref={rootRef}
      data-status={status}
      data-motion={reducedMotion ? "off" : "on"}
      role="group"
      aria-label="贾维斯 HUD"
    >
      <HudBackground />

      {/* ---------------- 顶栏：品牌 + 状态条 + 控制 ---------------- */}
      <header className="jhud-top">
        <div className="jhud-brand">
          <span className="jhud-brand-mk hud-color-border" aria-hidden="true">
            <svg viewBox="0 0 20 20" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M10 2.6l6.5 3.8v7.6L10 17.4 3.5 14V6.4L10 2.6z" />
              <circle cx="10" cy="10" r="2.4" fill="currentColor" stroke="none" />
            </svg>
          </span>
          <span className="jhud-brand-tx">
            <b className="hud-label">JARVIS</b>
            <i className="jhud-brand-sub">DUAL-STATE CORE</i>
          </span>
        </div>

        <div className="jhud-top-center">
          <StatusBar
            status={status}
            phaseRef={phaseRef ?? shared.phaseRef}
            register={motion.register}
            onSelect={onStatusSelect}
          />
        </div>

        <div className="jhud-top-right">
          {micMode !== "off" && (
            <span className="jhud-mic hud-label" title="麦克风状态">
              {MIC_HINT[mic.status]}
            </span>
          )}
          {onWakeToggle && (
            <button
              type="button"
              className={`jhud-btn jhud-btn--wake${wakeEnabled ? " on" : ""}${
                wakePhase === "armed" || wakePhase === "conversation" ? " armed" : ""
              }`}
              onClick={onWakeToggle}
              aria-pressed={wakeEnabled}
              title={
                wakeEnabled
                  ? wakePhase === "armed"
                    ? "已唤醒 — 说指令（点击关闭监听）"
                    : wakePhase === "conversation"
                      ? "对话中 — 继续说即可（点击关闭监听）"
                      : "正在监听唤醒词「贾维斯」（点击关闭）"
                  : "开启语音唤醒：说「贾维斯」即可对话"
              }
            >
              <span aria-hidden="true">{wakeEnabled ? "◉" : "○"}</span>
              {wakePhase === "conversation" ? "对话中" : "唤醒"}
            </button>
          )}
          {onInterrupt && (
            <button
              type="button"
              className="jhud-btn jhud-btn--stop"
              onClick={handleInterrupt}
              aria-label="停止播报"
              aria-keyshortcuts="Escape"
            >
              打断
            </button>
          )}
          {onOpenPanel && (
            <>
              <button
                type="button"
                className="jhud-icon-btn"
                onClick={() => onOpenPanel("newchat")}
                title="新建会话"
                aria-label="新建会话"
              >
                <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3">
                  <path d="M2.5 3h11v7.5H6.5L2.5 13.5V3z" />
                </svg>
              </button>
              <button
                type="button"
                className="jhud-icon-btn"
                onClick={() => onOpenPanel("settings")}
                title="设置（模型 / 语音 / MCP / 隐私）"
                aria-label="打开设置"
              >
                <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.3">
                  <circle cx="8" cy="8" r="2.4" />
                  <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2" />
                </svg>
              </button>
            </>
          )}
          {onExit && (
            <button type="button" className="jhud-btn" onClick={onExit} aria-label="退出 HUD 模式">
              退出 HUD
            </button>
          )}
        </div>
      </header>

      {/* ---------------- 主体：中央核心 + 可选会话流 ---------------- */}
      <div className={`jhud-body${showFeed ? " jhud-body--split" : ""}`}>
        <div className="jhud-center">
          <EnergyCore size={coreSize} register={motion.register} coreColorRef={motion.coreColorRef} />
          <div className="jhud-wave">
            <WaveformBar
              register={motion.register}
              coreColorRef={motion.coreColorRef}
              width={bars}
            />
          </div>
        </div>

        {showFeed && (
          <aside className="jhud-feed" aria-label="会话流">
            <div className="jhud-feed-h hud-label">TRANSCRIPT</div>
            <div className="jhud-feed-scroll" ref={feedRef} aria-busy={status === "thinking"}>
              {messages.length === 0 ? (
                <p className="jhud-feed-empty">已就绪。你可以直接说话，也可以打字——我一直在听。</p>
              ) : (
                messages.map((m, i) => (
                  <div key={i} className={`jhud-msg jhud-msg--${m.role}`}>
                    <span className="jhud-msg-who hud-label">
                      {m.role === "user" ? "YOU" : "JARVIS"}
                      {m.time ? ` · ${m.time}` : ""}
                    </span>
                    <span className="jhud-msg-tx">{m.text}</span>
                  </div>
                ))
              )}
            </div>
          </aside>
        )}
      </div>

      {/* ---------------- 底部语音条 ---------------- */}
      {onSend && (
        <footer className="jhud-dock">
          {onVoiceStart && onVoiceStop && (
            <button
              type="button"
              className={`jhud-icon-btn${ptt ? " on" : ""}`}
              onClick={() => {
                if (pttRef.current) {
                  pttRef.current = false;
                  setPtt(false);
                  onVoiceStop();
                } else {
                  pttRef.current = true;
                  setPtt(true);
                  onVoiceStart();
                }
              }}
              disabled={!canSpeak}
              aria-pressed={ptt}
              title={ptt ? "停止录音并发送" : "点击开始说话（或按住 Space）"}
              aria-label={ptt ? "停止录音并发送" : "开始说话"}
            >
              <svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.3">
                <path d="M8 2a2.4 2.4 0 0 1 2.4 2.4v3.8a2.4 2.4 0 0 1-4.8 0V4.4A2.4 2.4 0 0 1 8 2z" />
                <path d="M4.2 8.4a3.8 3.8 0 0 0 7.6 0M8 12.2V14" />
              </svg>
            </button>
          )}
          <input
            className="jhud-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={
              !canSpeak
                ? "连接已断开，暂时无法发送"
                : ptt
                  ? "正在录音… 松开 Space 发送"
                  : onVoiceStart
                    ? "输入消息，按 Enter 发送 · 按住 Space 说话"
                    : "输入消息，按 Enter 发送"
            }
            aria-label="输入消息，按 Enter 发送"
            disabled={!canSpeak}
          />
          <button
            type="button"
            className="jhud-btn jhud-btn--send"
            onClick={submit}
            disabled={!canSpeak || !input.trim()}
          >
            发送
          </button>
        </footer>
      )}

      {/* ---------------- 唯一的 aria-live 出口 ---------------- */}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announced}
      </div>
      {status === "error" && (
        <div className="sr-only" role="alert">
          出现错误，可重试或稍后再试
        </div>
      )}
    </div>
  );
}

/* ========================================================================== */
/*  HudToggle —— 从豆包浅色风进入 HUD 模式的入口按钮                            */
/*  独立于 .jhud 作用域，可直接挂在现有浅色 UI 上。                              */
/* ========================================================================== */

export function HudToggle({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="jhud-toggle" onClick={onClick} aria-label="切换到贾维斯 HUD 模式">
      <span aria-hidden="true">◈</span>
      <span>HUD</span>
    </button>
  );
}
