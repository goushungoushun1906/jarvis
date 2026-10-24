import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject, RefObject } from "react";
import {
  BAND_COUNT,
  approachRGB,
  clamp,
  createWakeGuard,
  getSharedMotionRefs,
  lerp,
  normalizeLevel,
  parseColor,
  smoothLevel,
  splitBands,
  type JarvisState,
  type RGB,
  type SpeakPhase,
  type Tier,
  type WakeGuard,
} from "./motionRefs";

/* ==========================================================================
   useJarvisMotion —— 全局唯一一个 requestAnimationFrame 编排器
   --------------------------------------------------------------------------
   硬性约束：
   · 禁止任何组件自己再起第二个 rAF。所有每帧动效必须 register 到这里。
   · 每帧 setState = 0。apply() 内只允许写 inline transform / opacity /
     strokeDashoffset，或往 Canvas 画。
   · 每帧写 inline 的 DOM 元素 ≤ 8 个，且**绝不往根元素写 CSS 变量**
     （变量一变，所有后代 style recalc，>200 元素 = 3ms+）。
   ========================================================================== */

/** 每帧上下文（由唯一 rAF 构造，只读）。类型里**故意不含任何 setter**。 */
export interface FrameCtx {
  /** 距上一帧毫秒数，已钳位到 ≤50ms（卡顿后防大跳）。 */
  dt: number;
  /** 循环启动至今的毫秒数。 */
  t: number;
  /** 平滑后的感知电平 0..1。 */
  level: number;
  bass: number;
  mid: number;
  treble: number;
  /** 长度 BAND_COUNT 的频谱，0..1。真实频谱缺失时为合成值。 */
  bands: Float32Array;
  status: JarvisState;
  phase: SpeakPhase;
  tier: Tier;
  /** 呼吸曲线 0..1（offline 恒为 0）。已按状态的 --breath-dur 计算。 */
  breath: number;
  /** true = 需要降级为静态/极低动效。 */
  reduced: boolean;
}

/** 注册式动画目标。apply 内**禁止** setState / setProperty / 增删 DOM。 */
export interface MotionTarget {
  apply(ctx: FrameCtx): void;
}

export type MotionRegister = (t: MotionTarget) => () => void;

export interface UseJarvisMotionOptions {
  /** HUD 根元素。data-tier / data-phase / data-motion / data-size 写在这里。 */
  rootRef: RefObject<HTMLElement | null>;
  /** 当前状态。低频变化，走 props；循环内部从 ref 读，不重建循环。 */
  status: JarvisState;
  /** speaking 子阶段。ref，每帧读。 */
  phaseRef?: MutableRefObject<SpeakPhase>;
  /** 实时电平。不传则完全走合成包络。 */
  levelRef?: MutableRefObject<number>;
  /** 频谱。不传则由合成包络推导。 */
  bandsRef?: MutableRefObject<Float32Array>;
  /** 强制开关；不传则跟随系统 prefers-reduced-motion。 */
  reducedMotion?: boolean;
  /** 强制档位；不传则自动探测。 */
  tier?: Tier;
  /** 档位变化时回调（低频，可选）。 */
  onTierChange?: (tier: Tier) => void;
}

export interface JarvisMotion {
  /** 注册一个每帧消费者。返回清理函数，务必在 useEffect cleanup 里调用（防 HMR 重复注册）。 */
  register: MotionRegister;
  /** 注册一个数据源（麦克风 / AnalyserNode 采样）。先于所有消费者执行。 */
  registerSampler: MotionRegister;
  /** 当前档位（ref，非 state）。 */
  tierRef: MutableRefObject<Tier>;
  /** 当前状态色（ref）。Canvas 用；状态变化时从 CSS 读一次，不是每帧。 */
  coreColorRef: MutableRefObject<RGB>;
  /** 打断后恢复唤醒的守卫（TTS 自激防线）。 */
  wakeGuard: WakeGuard;
}

/* --------------------------- 状态常量表 --------------------------- */

/** 呼吸周期（ms）。offline = 0（唯一完全静止态）。规范 §2.2。 */
const BREATH_MS: Record<JarvisState, number> = {
  idle: 4000,
  listening: 1800,
  thinking: 1200,
  speaking: 600,
  error: 1200,
  offline: 0,
};

/** 环转速基线 rps。规范 §4.2。 */
export const RING_RPS: Record<JarvisState, number> = {
  idle: 0.06,
  listening: 0.1,
  thinking: 0.25,
  speaking: 0.14,
  error: 0,
  offline: 0,
};

/** 辉光强度基线。规范 §4.2。 */
export const GLOW_BASE: Record<JarvisState, number> = {
  idle: 0.35,
  listening: 0.5,
  thinking: 0.42,
  speaking: 0.55,
  error: 0.6,
  offline: 0.12,
};

/** 档位 → 粒子数（再按面积缩放）。规范 §7.3。 */
export function particleCountFor(tier: Tier, size: number): number {
  if (tier === "C" || tier === "D") return 0;
  const base = tier === "A" ? 900 : 400;
  const area = (size / 320) * (size / 320);
  return Math.max(0, Math.round(base * Math.min(1, area)));
}

/** 档位 / reduce → 频谱条数。规范 §7.4。 */
export function barCountFor(tier: Tier, reduced: boolean): number {
  if (tier === "D") return 0;
  if (reduced || tier === "C") return 16;
  if (tier === "B") return 32;
  return BAND_COUNT;
}

/* --------------------------- 初始档位探测 --------------------------- */

function detectInitialTier(): Tier {
  try {
    const cores = navigator.hardwareConcurrency || 4;
    if (cores <= 2) return "D";
    const c = document.createElement("canvas");
    const gl = c.getContext("webgl") || c.getContext("webgl2");
    if (!gl) return "C";
  } catch {
    return "C";
  }
  return "A";
}

/* --------------------------- 合成包络 --------------------------- */
/**
 * 没有真实音频时的兜底。**视觉上必须仍有起伏**，否则"播报中"看起来像卡住。
 * 规范 §1.4：stream → 0.35 + 0.15*sin(t/220ms)；
 *            speaking 空档 → 0.18 低频呼吸。
 */
function syntheticLevel(status: JarvisState, phase: SpeakPhase, t: number): number {
  switch (status) {
    case "speaking": {
      if (phase === "stream") return 0.35 + 0.15 * Math.sin(t / 220);
      if (phase === "tts") return 0.3 + 0.2 * Math.sin(t / 260);
      // phase === null：SSE done → TTS 起播的空档期，低频呼吸维持"在播报"观感
      return 0.18 + 0.08 * Math.sin(t / 900);
    }
    case "listening":
      // 无麦克风权限时的兜底：像有人在轻声说话
      return 0.16 + 0.1 * Math.sin(t / 380) + 0.06 * Math.sin(t / 173);
    case "thinking":
      return 0.14 + 0.06 * Math.sin(t / 300);
    case "error":
      return 0.1 + 0.05 * Math.sin(t / 140);
    default:
      return 0;
  }
}

/** 由合成电平推导一条形状可信的频谱（低频高、高频低 + 逐条抖动）。 */
function syntheticBands(out: Float32Array, level: number, t: number): void {
  const n = out.length;
  for (let i = 0; i < n; i++) {
    const u = i / (n - 1);
    const tilt = Math.pow(1 - u, 1.35) * 0.75 + 0.25;
    const wob = 0.6 + 0.4 * Math.sin(t / (140 + i * 9) + i * 0.7);
    out[i] = clamp(level * tilt * wob, 0, 1);
  }
}

/* ========================================================================== */
/*                                主 hook                                     */
/* ========================================================================== */

export function useJarvisMotion(opts: UseJarvisMotionOptions): JarvisMotion {
  const {
    rootRef,
    status,
    phaseRef,
    levelRef,
    bandsRef,
    reducedMotion,
    tier: forcedTier,
    onTierChange,
  } = opts;

  const targetsRef = useRef<MotionTarget[]>([]);
  const samplersRef = useRef<MotionTarget[]>([]);

  const statusRef = useRef<JarvisState>(status);
  const reducedRef = useRef<boolean>(!!reducedMotion);
  const tierRef = useRef<Tier>(forcedTier ?? "A");
  const coreColorRef = useRef<RGB>({ r: 53, g: 224, b: 255 });

  const levelExtRef = levelRef ?? getSharedMotionRefs().levelRef;
  const bandsExtRef = bandsRef ?? getSharedMotionRefs().bandsRef;
  const phaseSrcRef = phaseRef ?? getSharedMotionRefs().phaseRef;

  // 把 props 同步进 ref：循环只依赖 []，不会因为 props 变化而重启
  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  /* ------------------- 档位 + data-tier（写 dataset，不 setState） ------------------- */
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const initial = forcedTier ?? detectInitialTier();
    tierRef.current = initial;
    el.dataset.tier = initial;
    onTierChange?.(initial);
  }, [forcedTier, rootRef, onTierChange]);

  /* ------------------- 状态色：状态变化时从 CSS 读一次 ------------------- */
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const raw = getComputedStyle(el).getPropertyValue("--core");
    const parsed = parseColor(raw);
    if (parsed) coreColorRef.current = parsed;
  }, [status, rootRef]);

  /* ------------------- prefers-reduced-motion ------------------- */
  useEffect(() => {
    const el = rootRef.current;
    if (typeof window.matchMedia !== "function") {
      reducedRef.current = !!reducedMotion;
      if (el) el.dataset.motion = reducedRef.current ? "off" : "on";
      return;
    }
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      // 显式 prop 优先于系统设置
      reducedRef.current = reducedMotion !== undefined ? reducedMotion : mq.matches;
      if (el) el.dataset.motion = reducedRef.current ? "off" : "on";
    };
    apply();
    if (typeof mq.addEventListener === "function") {
      mq.addEventListener("change", apply);
      return () => mq.removeEventListener("change", apply);
    }
    return undefined;
  }, [reducedMotion, rootRef]);

  /* ------------------- 响应式档位（ResizeObserver，节流 100ms） ------------------- */
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let timer = 0;
    const ro = new ResizeObserver((entries) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const r = entries[0]?.contentRect;
        if (!r) return;
        const w = r.width;
        const h = r.height;
        el.dataset.size =
          w >= 1440 ? "L" : w >= 1200 ? "M" : w >= 1000 ? "S" : w >= 760 ? "XS" : "F";
        el.dataset.vsize = h >= 860 ? "tall" : h >= 640 ? "mid" : "short";
      }, 100);
    });
    ro.observe(el);
    return () => {
      window.clearTimeout(timer);
      ro.disconnect();
    };
  }, [rootRef]);

  /* ------------------- 注册 ------------------- */
  const register = useCallback<MotionRegister>((t) => {
    targetsRef.current.push(t);
    return () => {
      const i = targetsRef.current.indexOf(t);
      if (i >= 0) targetsRef.current.splice(i, 1);
    };
  }, []);

  const registerSampler = useCallback<MotionRegister>((t) => {
    samplersRef.current.push(t);
    return () => {
      const i = samplersRef.current.indexOf(t);
      if (i >= 0) samplersRef.current.splice(i, 1);
    };
  }, []);

  /* ------------------- 唯一 rAF ------------------- */
  useEffect(() => {
    const el = rootRef.current;
    let raf = 0;
    let last = performance.now();
    const startedAt = last;
    let smoothed = 0;
    let lastPhase: SpeakPhase = phaseSrcRef.current;

    // 降级采样：跳过前 90 帧；切后台不计入；降档后 10s 冻结
    let frameCount = 0;
    const samples: number[] = [];
    let lastTierChange = 0;

    const bands = new Float32Array(BAND_COUNT);
    const colorTarget: RGB = { ...coreColorRef.current };

    const tick = (now: number) => {
      const rawDt = now - last;
      last = now;
      const dt = Math.min(50, Math.max(0, rawDt));
      const t = now - startedAt;
      const st = statusRef.current;
      const reduced = reducedRef.current;
      const phase = phaseSrcRef.current;

      /* --- 1. 数据源：先跑 sampler（麦克风 / AnalyserNode） --- */
      for (let i = 0; i < samplersRef.current.length; i++) {
        samplersRef.current[i].apply({
          dt, t, level: smoothed, bass: 0, mid: 0, treble: 0,
          bands, status: st, phase, tier: tierRef.current, breath: 0, reduced,
        });
      }

      /* --- 2. 电平：真实优先，缺失则合成兜底 --- */
      const raw = levelExtRef.current || 0;
      let target: number;
      if (raw > 0.012) {
        // 人说话通常比 TTS 小 → listening 增益 ×1.35
        target = clamp(normalizeLevel(raw) * (st === "listening" ? 1.35 : 1), 0, 1);
      } else if (st === "speaking" || st === "listening" || st === "thinking" || st === "error") {
        target = clamp(syntheticLevel(st, phase, t), 0, 1);
      } else {
        target = 0;
      }
      smoothed = smoothLevel(smoothed, target);
      if (smoothed < 0.0015) smoothed = 0;

      /* --- 3. 频谱 --- */
      const ext = bandsExtRef.current;
      let hasReal = false;
      if (ext && ext.length >= BAND_COUNT) {
        let sum = 0;
        for (let i = 0; i < BAND_COUNT; i++) sum += ext[i];
        hasReal = sum > 0.02;
      }
      if (hasReal && ext) {
        for (let i = 0; i < BAND_COUNT; i++) bands[i] = ext[i];
      } else {
        syntheticBands(bands, smoothed, t);
      }
      const { bass, mid, treble } = splitBands(bands);

      /* --- 4. 呼吸曲线（offline 恒 0） --- */
      const bd = BREATH_MS[st];
      const breath = bd > 0 ? 0.5 - 0.5 * Math.cos((2 * Math.PI * (t % bd)) / bd) : 0;

      /* --- 5. 颜色：向目标色逼近 420ms（只给 Canvas 用，1 个元素） --- */
      approachRGB(colorTarget, coreColorRef.current, dt, 420);

      /* --- 6. 派发给所有消费者 --- */
      const ctx: FrameCtx = {
        dt, t, level: smoothed, bass, mid, treble,
        bands, status: st, phase, tier: tierRef.current, breath, reduced,
      };
      const list = targetsRef.current;
      for (let i = 0; i < list.length; i++) list[i].apply(ctx);

      /* --- 7. data-phase：只在变化时写（不是每帧） --- */
      if (phase !== lastPhase) {
        lastPhase = phase;
        if (el) el.dataset.phase = phase ?? "";
      }

      /* --- 8. 降级探测 --- */
      if (!forcedTier) {
        frameCount++;
        const hidden = typeof document !== "undefined" && document.visibilityState === "hidden";
        // 启动前 90 帧必然超标（编译/首绘），切后台 rAF 被节流到 1fps 也会爆表 → 都不采样
        if (frameCount > 90 && !hidden) {
          samples.push(rawDt);
          if (samples.length > 120) samples.shift();
          if (samples.length >= 120 && now - lastTierChange > 10000) {
            const sorted = samples.slice().sort((a, b) => a - b);
            const p95 = sorted[Math.floor(sorted.length * 0.95)];
            const next: Tier = p95 > 48 ? "D" : p95 > 32 ? "C" : p95 > 22 ? "B" : "A";
            if (next !== tierRef.current) {
              tierRef.current = next;
              lastTierChange = now;
              samples.length = 0;
              if (el) el.dataset.tier = next;
              onTierChange?.(next);
            }
          }
        }
      }

      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);

    /* 切后台：停 rAF；恢复：重置 last 再启动（否则 dt 会是几万毫秒） */
    const onVis = () => {
      if (document.visibilityState === "hidden") {
        if (raf) {
          cancelAnimationFrame(raf);
          raf = 0;
        }
      } else if (!raf) {
        last = performance.now();
        raf = requestAnimationFrame(tick);
      }
    };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVis);
    };
    // 循环只建一次：所有输入都走 ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [register, registerSampler, rootRef, levelExtRef, bandsExtRef, phaseSrcRef, forcedTier, onTierChange]);

  const wakeGuardRef = useRef<WakeGuard | null>(null);
  if (wakeGuardRef.current === null) wakeGuardRef.current = createWakeGuard(400);

  return { register, registerSampler, tierRef, coreColorRef, wakeGuard: wakeGuardRef.current };
}

/* ========================================================================== */
/*  useMicLevel —— 真实麦克风电平/频谱，喂进共享 ref                            */
/* -------------------------------------------------------------------------- */
/*  它**不自己起 rAF**：采样作为一个 sampler 挂进唯一循环，保证全局只有一个 rAF。 */
/* ========================================================================== */

export type MicStatus = "off" | "requesting" | "live" | "denied" | "unsupported";

export interface UseMicLevelOptions {
  registerSampler: MotionRegister;
  levelRef: MutableRefObject<number>;
  bandsRef: MutableRefObject<Float32Array>;
  /** true 时自动开麦。 */
  active: boolean;
}

export interface MicLevelApi {
  status: MicStatus;
  start: () => Promise<void>;
  stop: () => void;
}

export function useMicLevel(opts: UseMicLevelOptions): MicLevelApi {
  const { registerSampler, levelRef, bandsRef, active } = opts;
  const [status, setStatus] = useState<MicStatus>("off");

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const timeBufRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const freqBufRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const wantRef = useRef(false);

  const stop = useCallback(() => {
    wantRef.current = false;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((tr) => tr.stop());
      streamRef.current = null;
    }
    analyserRef.current = null;
    if (ctxRef.current) {
      void ctxRef.current.close().catch(() => undefined);
      ctxRef.current = null;
    }
    levelRef.current = 0;
    bandsRef.current.fill(0);
    setStatus("off");
  }, [bandsRef, levelRef]);

  const start = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("unsupported");
      return;
    }
    if (wantRef.current) return;
    wantRef.current = true;
    setStatus("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      if (!wantRef.current) {
        stream.getTracks().forEach((tr) => tr.stop());
        return;
      }
      const Ctor: typeof AudioContext =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctor();
      // autoplay policy：挂起态需要一次 resume
      if (ctx.state === "suspended") await ctx.resume().catch(() => undefined);
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      // fftSize 1024 → 频带分辨率 43Hz/bin @44.1k（规范 §4.1）
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.72;
      src.connect(analyser);

      streamRef.current = stream;
      ctxRef.current = ctx;
      analyserRef.current = analyser;
      timeBufRef.current = new Uint8Array(analyser.fftSize);
      freqBufRef.current = new Uint8Array(analyser.frequencyBinCount);
      setStatus("live");
    } catch {
      wantRef.current = false;
      setStatus("denied");
    }
  }, []);

  useEffect(() => {
    if (active) void start();
    else stop();
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // 采样挂进唯一 rAF，且先于所有消费者执行
  useEffect(() => {
    return registerSampler({
      apply: () => {
        const an = analyserRef.current;
        if (!an) return;
        const tb = timeBufRef.current;
        const fb = freqBufRef.current;
        if (tb) {
          an.getByteTimeDomainData(tb);
          let sum = 0;
          for (let i = 0; i < tb.length; i++) {
            const v = (tb[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / tb.length);
          levelRef.current = Math.min(1, rms * 4.5);
        }
        if (fb) {
          an.getByteFrequencyData(fb);
          // 简易对数分频：与 writeBands 同形，这里只取 0–5.5kHz 的 128 个 bin
          const out = bandsRef.current;
          const n = out.length;
          const binsUsed = Math.min(128, fb.length);
          for (let i = 0; i < n; i++) {
            const lo = Math.floor(Math.pow(i / n, 2.2) * binsUsed);
            const hi = Math.max(lo + 1, Math.floor(Math.pow((i + 1) / n, 2.2) * binsUsed));
            let s = 0;
            for (let j = lo; j < hi; j++) s += fb[j];
            out[i] = s / (hi - lo) / 255;
          }
        }
      },
    });
  }, [registerSampler, levelRef, bandsRef]);

  return { status, start, stop };
}

/* ========================================================================== */
/*  工具：把 status 变化播报出去（aria-live，最小驻留 800ms）                    */
/* ========================================================================== */

export const STATUS_LABEL_ZH: Record<JarvisState, string> = {
  idle: "待机",
  listening: "正在聆听",
  thinking: "正在思考",
  speaking: "正在播报",
  error: "出现错误",
  offline: "连接已断开",
};

/**
 * 最小驻留 800ms：thinking 一闪而过不播报，否则读屏会被刷屏。
 * 返回已稳定的播报文本。
 */
export function useStatusAnnouncer(status: JarvisState): string {
  const [announced, setAnnounced] = useState("");
  useEffect(() => {
    const id = window.setTimeout(() => setAnnounced(STATUS_LABEL_ZH[status]), 800);
    return () => window.clearTimeout(id);
  }, [status]);
  return announced;
}

/** 涟漪发射间隔：lerp(900ms, 220ms, level)。 */
export function rippleGap(level: number): number {
  return lerp(900, 220, clamp(level, 0, 1));
}
