import { useRef } from "react";
import type { MutableRefObject } from "react";

/* ==========================================================================
   共享可变引用 + 音频映射数学
   --------------------------------------------------------------------------
   死线：**每帧 setState = 0**。所有每帧数据必须走 ref。
   这个文件里出现的任何东西都不允许触发 React 重渲染。
   ========================================================================== */

/** 六态。与 src/lib/types.ts 的 JavisStatus 保持同构（JarvisHud.tsx 里有编译期断言）。 */
export type JarvisState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "error"
  | "offline";

/**
 * speaking 的子阶段，决定振幅驱动源。**不是独立状态**，不参与迁移表。
 * - `stream`：SSE 首 token → SSE done，用合成包络
 * - `tts`   ：TTS play() → onended，用真实 AnalyserNode 电平
 * - `null`  ：不在播报，或正处在「SSE done → TTS 起播」的 200–800ms 空档
 *             （此时 status 仍是 speaking，HUD 显示 SYNTHESIZING，振幅走低频呼吸，
 *              **绝不闪回 idle** —— 这是规范 §10 第 2 条高危项的解法）
 */
export type SpeakPhase = "stream" | "tts" | null;

/** 性能档位。由 useJarvisMotion 自动探测，写入 root.dataset.tier，不 setState。 */
export type Tier = "A" | "B" | "C" | "D";

/** 响应式档位。由 ResizeObserver 写入 root.dataset.size，不 setState。 */
export type Size = "L" | "M" | "S" | "XS" | "F";

/** 频谱条数：S 40 / M 48 / L 64 / XS 24；降级后 16 / 0。 */
export const BAND_COUNT = 64;

export interface JarvisMotionRefs {
  /** 实时电平 0..1。麦克风 RMS 或 TTS AnalyserNode RMS，每帧写入。 */
  levelRef: MutableRefObject<number>;
  /** 频谱，长度 BAND_COUNT，每项 0..1，每帧写入。 */
  bandsRef: MutableRefObject<Float32Array>;
  /** speaking 子阶段。 */
  phaseRef: MutableRefObject<SpeakPhase>;
}

export function createBands(): Float32Array {
  return new Float32Array(BAND_COUNT);
}

/** 创建一组共享 ref。一个应用实例只需一组。 */
export function createMotionRefs(): JarvisMotionRefs {
  return {
    levelRef: { current: 0 },
    bandsRef: { current: createBands() },
    phaseRef: { current: null },
  };
}

/**
 * 组件内创建一组 ref（推荐用法：在 App 顶层创建一次，往 HUD 传）。
 * 注意：这不是 state，改变 .current 不会重渲染。
 */
export function useMotionRefs(): JarvisMotionRefs {
  const r = useRef<JarvisMotionRefs | null>(null);
  if (r.current === null) r.current = createMotionRefs();
  return r.current;
}

/* -------------------------------------------------------------------------- */
/* 模块级兜底：调用方不传 levelRef / bandsRef / phaseRef 时使用。               */
/* 好处：任何地方（甚至非 React 代码）都能拿到同一组 ref 往里写数据。            */
/* -------------------------------------------------------------------------- */
let shared: JarvisMotionRefs | null = null;
export function getSharedMotionRefs(): JarvisMotionRefs {
  if (shared === null) shared = createMotionRefs();
  return shared;
}

/* ========================================================================== */
/* 音频映射数学（规范 §4.1，直接照抄数值）                                       */
/* ========================================================================== */

/**
 * RMS → 感知电平。替换项目里现有的 `rms * 4.5` 线性放大。
 * 语音 RMS 常见 0.02~0.18；pow 0.7 让弱声更可见。
 */
export function normalizeLevel(rms: number): number {
  const n = Math.min(1, Math.max(0, rms) / 0.18);
  return Math.pow(n, 0.7);
}

/** 每帧 attack / release 平滑，杜绝抖动。attack 快、release 慢。 */
export function smoothLevel(prev: number, target: number): number {
  const k = target > prev ? 0.45 : 0.12;
  return prev + (target - prev) * k;
}

/**
 * 频谱 → BAND_COUNT 条对数分频。只取前 128 个 bin（≈0–5.5kHz @44.1k）。
 * 直接写进 out，零分配。
 */
export function writeBands(bins: Uint8Array, out: Float32Array, bars = BAND_COUNT): void {
  const binsUsed = Math.min(128, bins.length);
  for (let i = 0; i < bars; i++) {
    const lo = Math.floor(Math.pow(i / bars, 2.2) * binsUsed);
    const hi = Math.max(lo + 1, Math.floor(Math.pow((i + 1) / bars, 2.2) * binsUsed));
    let s = 0;
    for (let j = lo; j < hi; j++) s += bins[j];
    out[i] = s / (hi - lo) / 255;
  }
}

/** 三频段：bass 0-6 / mid 7-30 / treble 31-63。 */
export function splitBands(bands: Float32Array): { bass: number; mid: number; treble: number } {
  let bass = 0;
  for (let i = 0; i <= 6 && i < bands.length; i++) bass += bands[i];
  let mid = 0;
  for (let i = 7; i <= 30 && i < bands.length; i++) mid += bands[i];
  let treble = 0;
  for (let i = 31; i < bands.length; i++) treble += bands[i];
  return { bass: bass / 7, mid: mid / 24, treble: treble / Math.max(1, bands.length - 31) };
}

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/* ========================================================================== */
/* 颜色：唯一真源在 CSS（--core），Canvas 需要拿到具体色值才能画。               */
/* 做法：状态变化时用 getComputedStyle 读一次（不是每帧），缓存在 ref 里。        */
/* ========================================================================== */

export interface RGB {
  r: number;
  g: number;
  b: number;
}

const HEX_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;

/** 解析 CSS 颜色字符串（支持 #rgb / #rrggbb / rgb() / rgba()）。解析失败返回 null。 */
export function parseColor(input: string): RGB | null {
  const s = input.trim();
  const hex = HEX_RE.exec(s);
  if (hex) {
    let h = hex[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
    };
  }
  const fn = /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i.exec(s);
  if (fn) return { r: +fn[1], g: +fn[2], b: +fn[3] };
  return null;
}

/** 逐帧向目标色逼近（时间常数 ~420ms/60fps）。给 Canvas 用，一次只有 1 个元素。 */
export function approachRGB(cur: RGB, target: RGB, dt: number, durMs = 420): void {
  const k = clamp(dt / durMs, 0, 1) * 2.2;
  cur.r += (target.r - cur.r) * k;
  cur.g += (target.g - cur.g) * k;
  cur.b += (target.b - cur.b) * k;
}

export function rgba(c: RGB, a: number): string {
  return `rgba(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)},${a})`;
}

/* ========================================================================== */
/* 唤醒守卫：解决规范 §10 第 3 条 —— TTS 自激死循环                              */
/* ========================================================================== */

export interface WakeGuard {
  /** 进入 thinking / speaking 时调用：立刻挂起唤醒监听。 */
  suspend(): void;
  /** 打断结束后调用：再等 cooldown 毫秒才允许恢复（防 TTS 尾音被识别成指令）。 */
  releaseAfterInterrupt(): void;
  /** 唤醒循环每轮开麦前问一句。 */
  canWake(): boolean;
}

/**
 * 默认 cooldown 400ms（规范 T7 / T12）。
 * 用法：把 `canWake()` 接进现有 useWakeWord 的循环判断里；
 *      进 thinking/speaking 调 suspend()，打断处理完调 releaseAfterInterrupt()。
 */
export function createWakeGuard(cooldownMs = 400): WakeGuard {
  let suspended = false;
  let resumeAt = 0;
  return {
    suspend() {
      suspended = true;
      resumeAt = 0;
    },
    releaseAfterInterrupt() {
      suspended = false;
      resumeAt = Date.now() + cooldownMs;
    },
    canWake() {
      if (suspended) return false;
      return Date.now() >= resumeAt;
    },
  };
}
