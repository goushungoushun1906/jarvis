import React, { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import type { JarvisState, SpeakPhase } from "./motionRefs";
import type { MotionRegister } from "./useJarvisMotion";
import "./StatusBar.css";

/* ==========================================================================
   StatusBar —— 六态状态指示器
   --------------------------------------------------------------------------
   无障碍硬要求：**状态绝不能只靠颜色传达**。
   本组件至少提供四重冗余（第五重「几何形状」由 EnergyCore 承担）：
     ① 颜色 --core-text
     ② 文字 STANDBY / LISTENING / PROCESSING / TRANSMITTING|SPEAKING|SYNTHESIZING / FAULT / NO LINK
     ③ 图标符号（独立几何形状，色盲可辨）
     ④ 动效节律（状态点脉冲周期 2000/500/800/300/500step/0ms）
   ========================================================================== */

export interface StatusBarProps {
  status: JarvisState;
  /** speaking 子阶段，决定 HUD 文案（TRANSMITTING / SPEAKING / SYNTHESIZING）。 */
  phase?: SpeakPhase;
  /**
   * phase 是 ref（每帧读，不 setState）。传入后组件会向唯一 rAF 注册一个
   * 「仅在 phase 变化时写一次 textContent」的目标 —— 不是每帧写，不占帧预算。
   */
  phaseRef?: MutableRefObject<SpeakPhase>;
  register?: MotionRegister;
  /** 传入则六枚胶囊可点击（演示 / 调试用）。 */
  onSelect?: (status: JarvisState) => void;
  /** XS 档只留点 + 图标时设为 false。 */
  showLabel?: boolean;
  /** 显示中文副标签。 */
  showZh?: boolean;
  className?: string;
}

interface Meta {
  en: string;
  zh: string;
  /** 状态点脉冲周期（ms）。0 = 完全静止（仅 offline）。 */
  rhythm: number;
  step: boolean;
}

export const STATUS_META: Record<JarvisState, Meta> = {
  idle: { en: "STANDBY", zh: "待机", rhythm: 2000, step: false },
  listening: { en: "LISTENING", zh: "聆听", rhythm: 500, step: false },
  thinking: { en: "PROCESSING", zh: "思考", rhythm: 800, step: false },
  speaking: { en: "SPEAKING", zh: "播报", rhythm: 300, step: false },
  error: { en: "FAULT", zh: "异常", rhythm: 500, step: true },
  offline: { en: "NO LINK", zh: "离线", rhythm: 0, step: false },
};

/** speaking 的三段文案。phase===null 表示「SSE done → TTS 起播」的空档期。 */
export function speakingLabel(phase: SpeakPhase): string {
  if (phase === "stream") return "TRANSMITTING";
  if (phase === "tts") return "SPEAKING";
  return "SYNTHESIZING";
}

export function statusLabel(status: JarvisState, phase: SpeakPhase): string {
  if (status === "speaking") return speakingLabel(phase);
  return STATUS_META[status].en;
}

/* ------------------------------ 图标（几何形状冗余） ------------------------------ */

const Icon = ({ s }: { s: JarvisState }) => {
  const p = {
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (s) {
    // 单点 + 完整圆环
    case "idle":
      return (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <circle cx="8" cy="8" r="6" {...p} />
          <circle cx="8" cy="8" r="2.2" fill="currentColor" stroke="none" />
        </svg>
      );
    // 内收括号 ⟩⟨ = 收声
    case "listening":
      return (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d="M6.4 3.6 2.6 8l3.8 4.4" {...p} />
          <path d="M9.6 3.6 13.4 8l-3.8 4.4" {...p} />
        </svg>
      );
    // 旋转三点 ⋰
    case "thinking":
      return (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <circle cx="3.6" cy="10.4" r="1.3" fill="currentColor" stroke="none" />
          <circle cx="8" cy="5.6" r="1.6" fill="currentColor" stroke="none" />
          <circle cx="12.4" cy="10.4" r="1.3" fill="currentColor" stroke="none" />
        </svg>
      );
    // 声波 )))
    case "speaking":
      return (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d="M3.2 5.4A5.4 5.4 0 0 1 3.2 10.6" {...p} />
          <path d="M6.6 3.4A8.4 8.4 0 0 1 6.6 12.6" {...p} />
          <path d="M10 1.6A11.2 11.2 0 0 1 10 14.4" {...p} />
        </svg>
      );
    // 六边形内 !
    case "error":
      return (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d="M8 1.8l5.4 3.1v6.2L8 14.2 2.6 11.1V4.9z" {...p} />
          <path d="M8 5.8v3.1" {...p} />
          <circle cx="8" cy="11" r="0.75" fill="currentColor" stroke="none" />
        </svg>
      );
    // 断开的环 ⊘
    case "offline":
      return (
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <circle cx="8" cy="8" r="6" {...p} />
          <path d="M3.6 12.4 12.4 3.6" {...p} />
        </svg>
      );
  }
};

const ORDER: JarvisState[] = ["idle", "listening", "thinking", "speaking", "error", "offline"];

export default function StatusBar({
  status,
  phase,
  phaseRef,
  register,
  onSelect,
  showLabel = true,
  showZh = true,
  className = "",
}: StatusBarProps) {
  const meta = STATUS_META[status];
  const initialPhase = phase ?? phaseRef?.current ?? null;
  const en = statusLabel(status, initialPhase);
  const enRef = useRef<HTMLSpanElement | null>(null);

  // phase 走 ref，React 不会重渲染 —— 这里用注册式目标在 phase 变化时写一次文本。
  // 只在变化那一帧写，不是每帧写。
  useEffect(() => {
    if (!register || !phaseRef) return;
    let last: SpeakPhase | undefined;
    return register({
      apply: (ctx) => {
        if (ctx.phase === last) return;
        last = ctx.phase;
        const el = enRef.current;
        if (el) el.textContent = statusLabel(ctx.status, ctx.phase);
      },
    });
  }, [register, phaseRef]);

  return (
    <div
      className={`sb ${className}`}
      role="group"
      aria-label="贾维斯运行状态"
      data-status={status}
    >
      {/* 当前状态：颜色 + 图标 + 文字 + 节律，四重编码 */}
      <div className="sb-current">
        <span
          className={`sb-dot ${meta.step ? "sb-dot--step" : ""}`}
          style={{ animationDuration: meta.rhythm > 0 ? `${meta.rhythm}ms` : undefined }}
          aria-hidden="true"
        />
        <span className="sb-icon hud-color-text" aria-hidden="true">
          <Icon s={status} />
        </span>
        {showLabel && (
          <span className="sb-text">
            <span className="sb-en hud-label hud-color-text" ref={enRef}>
              {en}
            </span>
            {showZh && <span className="sb-zh">{meta.zh}</span>}
          </span>
        )}
      </div>

      {/* 六枚胶囊：当前态高亮。非颜色冗余：即使关掉颜色，图标形状仍可区分 */}
      <div className="sb-list">
        {ORDER.map((s) => {
          const active = s === status;
          const cls = `sb-pill${active ? " sb-pill--on" : ""}`;
          const inner = (
            <>
              <span className="sb-pill-icon" aria-hidden="true">
                <Icon s={s} />
              </span>
              {showLabel && <span className="sb-pill-tx">{STATUS_META[s].zh}</span>}
            </>
          );
          return onSelect ? (
            <button
              key={s}
              type="button"
              className={cls}
              aria-current={active ? "true" : undefined}
              aria-label={`切换到${STATUS_META[s].zh}状态`}
              onClick={() => onSelect(s)}
            >
              {inner}
            </button>
          ) : (
            <span
              key={s}
              className={cls}
              aria-current={active ? "true" : undefined}
              title={STATUS_META[s].zh}
            >
              {inner}
            </span>
          );
        })}
      </div>
    </div>
  );
}
