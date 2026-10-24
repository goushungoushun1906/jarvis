import React, { useEffect, useId, useRef } from "react";
import { clamp, rgba, type JarvisState, type Tier } from "./motionRefs";
import {
  GLOW_BASE,
  RING_RPS,
  particleCountFor,
  rippleGap,
  type FrameCtx,
  type MotionRegister,
} from "./useJarvisMotion";
import "./EnergyCore.css";

/* ==========================================================================
   EnergyCore —— 中央 AI 能量核心（C 方案「双态融合」）
   --------------------------------------------------------------------------
   ⚠️ 硬约束：只做抽象能量体 / 粒子 / 环阵。**绝不做人脸、不做人形几何体。**
      （3D 形象层已迭代 4 轮全部失败，共同根因就是凭空捏几何体。）

   每帧 DOM 写入清单（≤8，这是死线）：
     1 .ec-shell     transform: scale(...)
     2 .ec-glow      opacity
     3 .ec-ring--1   transform: rotate()
     4 .ec-ring--2   transform: rotate()
     5 .ec-ring--3   transform: rotate()
     6 .ec-arc--a    stroke-dashoffset
     7 .ec-arc--b    stroke-dashoffset
     + Canvas 粒子（位图绘制，不产生任何 style recalc）
   其余全部走 CSS animation（呼吸/噪点/抖动/spinner/栅格），不占 JS 帧预算。
   ========================================================================== */

export interface EnergyCoreProps {
  /** CSS 尺寸（正方形边长）。默认 320。 */
  size?: number;
  /** 唯一 rAF 编排器的注册函数。由 JarvisHud 注入。 */
  register: MotionRegister;
  /** 当前状态色的 RGB（ref）。由 useJarvisMotion 维护，Canvas 粒子用。 */
  coreColorRef?: React.MutableRefObject<{ r: number; g: number; b: number }>;
  className?: string;
}

const VIEW = 320;
const CENTER = VIEW / 2;

/** 三环：半径 + 方向（1 顺时针 / -1 逆时针）+ 虚线样式。规范 MI-05：r1 顺 / r2 逆 / r3 顺。 */
const RINGS = [
  { r: 142, dir: 1, dash: "2 11", sw: 1, op: 0.5 },
  { r: 118, dir: -1, dash: "150 100", sw: 3, op: 0.9 },
  { r: 96, dir: 1, dash: "4 9", sw: 1, op: 0.6 },
] as const;

/** 双反向弧段：青（--core）顺时针 / 紫（--core-2）逆时针。这是 C 方案的签名元素。 */
const ARC_A = { r: 80, sweep: 110 };
const ARC_B = { r: 64, sweep: 96 };

const RIPPLE_POOL = 3;

interface Particle {
  ang: number;
  spd: number;
  baseR: number;
  r: number;
  rv: number;
  a: number;
  size: number;
  px: number;
  py: number;
}

export default function EnergyCore({
  size = 320,
  register,
  coreColorRef,
  className = "",
}: EnergyCoreProps) {
  const rawId = useId();
  const uid = rawId.replace(/[^a-zA-Z0-9_-]/g, "");

  const shellRef = useRef<HTMLDivElement | null>(null);
  const glowRef = useRef<HTMLDivElement | null>(null);
  const ringRefs = useRef<(SVGGElement | null)[]>([]);
  const arcARef = useRef<SVGCircleElement | null>(null);
  const arcBRef = useRef<SVGCircleElement | null>(null);
  const rippleRefs = useRef<(SVGCircleElement | null)[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  /* ---------------------------------------------------------------- */
  /* 每帧消费者：核心缩放 + 辉光 + 三环 + 双弧段                        */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    const shell = shellRef.current;
    const glow = glowRef.current;
    const arcA = arcARef.current;
    const arcB = arcBRef.current;
    if (!shell || !glow || !arcA || !arcB) return;

    // 环角速度：向目标 rps 平滑逼近（状态切换 240ms 内加速/减速，不是硬切）
    const rps = [RING_RPS.idle, RING_RPS.idle, RING_RPS.idle];
    const angle = [0, 0, 0];
    const circA = 2 * Math.PI * ARC_A.r;
    const circB = 2 * Math.PI * ARC_B.r;
    let offA = 0;
    let offB = 0;

    // 弧段 pathLength=360 → dash 单位直接是「度」
    const unitPerSec = (v: number, circ: number) => (v * 360) / circ;
    let lastStatus: JarvisState | null = null;

    // 涟漪池
    let lastEmit = 0;
    const busy: boolean[] = new Array(RIPPLE_POOL).fill(false);
    const running: Animation[] = [];

    const apply = (ctx: FrameCtx) => {
      const st = ctx.status;

      /* --- 弧段虚线：仅在状态变化时写一次（error 时加大 gap 做「断裂」） --- */
      if (st !== lastStatus) {
        lastStatus = st;
        arcA.style.strokeDasharray =
          st === "error" ? "40 30 40 250" : `${ARC_A.sweep} ${360 - ARC_A.sweep}`;
        arcB.style.strokeDasharray =
          st === "error" ? "34 26 34 266" : `${ARC_B.sweep} ${360 - ARC_B.sweep}`;
      }

      /* --- ① 核心缩放：scale = 1 + 0.045*breath + 0.06*bass + 0.10*level --- */
      let scale: number;
      if (st === "offline") {
        scale = 1;
      } else if (ctx.reduced) {
        // reduce：幅度压到 ≤1.02
        scale = 1 + 0.02 * ctx.level;
      } else {
        scale =
          1 + 0.045 * ctx.breath + 0.06 * ctx.bass + 0.1 * ctx.level;
      }
      shell.style.transform = `scale(${clamp(scale, 1, 1.205).toFixed(4)})`;

      /* --- ② 辉光：base + 0.55*level，预烘焙渐变，只动 opacity --- */
      if (ctx.reduced) {
        glow.style.opacity = "0.62";
      } else {
        glow.style.opacity = clamp(GLOW_BASE[st] + 0.55 * ctx.level, 0.12, 0.9).toFixed(3);
      }

      /* --- ③④⑤ 三环旋转。reduce 下环保持静态，形状冗余仍在（规范 §6.4） --- */
      const baseRps = RING_RPS[st] * (1 + 0.8 * ctx.level);
      for (let i = 0; i < RINGS.length; i++) {
        if (ctx.reduced) break;
        const el = ringRefs.current[i];
        if (!el) continue;
        const k = clamp(ctx.dt / 240, 0, 1) * 2.5;
        rps[i] += (baseRps - rps[i]) * k;
        angle[i] += rps[i] * 360 * (ctx.dt / 1000) * RINGS[i].dir;
        if (angle[i] > 3600 || angle[i] < -3600) angle[i] %= 360;
        el.style.transform = `rotate(${angle[i].toFixed(2)}deg)`;
      }

      /* --- ⑥⑦ 弧段扫动：v = 40 + 260*level px/s，双反向 --- */
      if (st !== "offline" && !ctx.reduced) {
        const v = 40 + 260 * ctx.level;
        offA -= unitPerSec(v, circA) * (ctx.dt / 1000);
        offB += unitPerSec(v, circB) * (ctx.dt / 1000);
        if (offA < -360) offA += 360;
        if (offB > 360) offB -= 360;
        arcA.style.strokeDashoffset = offA.toFixed(2);
        arcB.style.strokeDashoffset = offB.toFixed(2);
      }

      /* --- 涟漪：speaking 且 level>0.28，对象池 3 个，池满则跳过不新建 DOM --- */
      if (st === "speaking" && !ctx.reduced && ctx.tier !== "C" && ctx.tier !== "D") {
        if (ctx.level > 0.28 && ctx.t - lastEmit > rippleGap(ctx.level)) {
          for (let i = 0; i < RIPPLE_POOL; i++) {
            if (busy[i]) continue;
            const el = rippleRefs.current[i];
            if (!el || typeof el.animate !== "function") break;
            lastEmit = ctx.t;
            busy[i] = true;
            const anim = el.animate(
              [
                { transform: "scale(1)", opacity: 0.5 },
                { transform: "scale(1.45)", opacity: 0 },
              ],
              { duration: 720, easing: "cubic-bezier(.22,1,.36,1)" }
            );
            running.push(anim);
            const done = () => {
              busy[i] = false;
              const idx = running.indexOf(anim);
              if (idx >= 0) running.splice(idx, 1);
            };
            anim.finished.then(done, done);
            break;
          }
        }
      }
    };

    const unregister = register({ apply });
    return () => {
      unregister();
      running.forEach((a) => a.cancel());
      running.length = 0;
    };
  }, [register]);

  /* ---------------------------------------------------------------- */
  /* Canvas 粒子场（位图绘制，零 DOM 写入）                             */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    const cvs = canvasRef.current;
    if (!cvs) return;
    const ctx2d = cvs.getContext("2d");
    if (!ctx2d) return;

    const dpr = Math.min(1.5, window.devicePixelRatio || 1);
    const px = Math.round(size * dpr);
    cvs.width = px;
    cvs.height = px;
    ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);

    let particles: Particle[] = [];
    let builtFor = "";

    const build = (count: number) => {
      particles = new Array(count);
      for (let i = 0; i < count; i++) {
        const baseR = (0.28 + Math.random() * 0.62) * (size / 2);
        particles[i] = {
          ang: Math.random() * Math.PI * 2,
          spd: (Math.random() - 0.5) * 0.00055 * (1 + (1 - baseR / (size / 2))),
          baseR,
          r: baseR,
          rv: 0,
          a: 0.18 + Math.random() * 0.5,
          size: 0.7 + Math.random() * 1.5,
          px: CENTER,
          py: CENTER,
        };
      }
    };

    const apply = (ctx: FrameCtx) => {
      const count = particleCountFor(ctx.tier, size);
      const key = `${ctx.tier}:${size}`;
      if (key !== builtFor) {
        builtFor = key;
        particles = [];
        if (count > 0) build(count);
      }
      ctx2d.clearRect(0, 0, size, size);
      if (count === 0 || ctx.reduced || ctx.status === "offline") return;

      // radiusMul = 1 + 0.18*level + 0.10*bass，上限 1.28
      const radiusMul = Math.min(1.28, 1 + 0.18 * ctx.level + 0.1 * ctx.bass);
      // visibleRatio：不增删粒子对象，超出索引的 alpha→0（零 GC）
      const visibleRatio = clamp(0.55 + 0.45 * ctx.level, 0, 1);
      const cutoff = Math.floor(count * visibleRatio);
      const trail = 0.6 + 3.4 * ctx.level;

      ctx2d.globalCompositeOperation = "lighter";
      const col = coreColorRef?.current;
      const strokeCol = col ? rgba(col, 0.5) : "rgba(53,224,255,0.5)";

      for (let i = 0; i < count; i++) {
        const p = particles[i];
        p.ang += p.spd * ctx.dt;
        // 弹簧–阻尼：k=0.22 / d=0.84（沿用既有模型，禁止改成独立弹簧，会散）
        p.rv += (p.baseR * radiusMul - p.r) * 0.22;
        p.rv *= 0.84;
        p.r += p.rv;

        const x = CENTER + Math.cos(p.ang) * p.r;
        const y = CENTER + Math.sin(p.ang) * p.r;
        const dx = x - p.px;
        const dy = y - p.py;

        if (i >= cutoff) {
          p.px = x;
          p.py = y;
          continue;
        }

        // 仅当位移 > 1.2px 才画拖尾
        if (dx * dx + dy * dy > 1.44) {
          const len = trail / Math.max(0.001, Math.sqrt(dx * dx + dy * dy));
          ctx2d.strokeStyle = strokeCol;
          ctx2d.lineWidth = Math.max(0.5, p.size * 0.8);
          ctx2d.beginPath();
          ctx2d.moveTo(x - dx * len, y - dy * len);
          ctx2d.lineTo(x, y);
          ctx2d.stroke();
        }

        ctx2d.fillStyle = col ? rgba(col, p.a) : `rgba(53,224,255,${p.a})`;
        ctx2d.beginPath();
        ctx2d.arc(x, y, p.size, 0, Math.PI * 2);
        ctx2d.fill();

        p.px = x;
        p.py = y;
      }
      ctx2d.globalCompositeOperation = "source-over";
    };

    const unregister = register({ apply });
    return () => {
      unregister();
      ctx2d.clearRect(0, 0, size, size);
    };
  }, [register, size, coreColorRef]);

  return (
    <div
      className={`ec ${className}`}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      {/* ① JS 每帧写 transform（scale） */}
      <div className="ec-shell" ref={shellRef}>
        {/* error：横向抖动 60ms ×3（CSS，不占 JS 帧预算） */}
        <div className="ec-shake">
          {/* 打断 T7：scale 1.06→0.94→1.04→1.0（CSS 两段 keyframes） */}
          <div className="ec-bounce">
            <canvas className="ec-particles" ref={canvasRef} />

            {/* ② 预烘焙辉光层，只动 opacity。禁止动画 filter / box-shadow */}
            <div className="ec-glow" ref={glowRef} />

            <svg
              className="ec-svg"
              viewBox={`0 0 ${VIEW} ${VIEW}`}
              preserveAspectRatio="xMidYMid meet"
              focusable="false"
            >
              <defs>
                <radialGradient id={`${uid}-glow`}>
                  <stop offset="0%" stopColor="#ffffff" stopOpacity="0.9" />
                  <stop offset="30%" stopColor="var(--core)" stopOpacity="0.55" />
                  <stop offset="100%" stopColor="var(--core)" stopOpacity="0" />
                </radialGradient>
              </defs>

              {/* 聆听时的「接收栅格」：几何冗余 —— 向内 = 收声 */}
              <g className="ec-grip">
                {Array.from({ length: 24 }).map((_, i) => {
                  const a = (i / 24) * Math.PI * 2;
                  const r0 = 150;
                  const r1 = 158;
                  return (
                    <line
                      key={i}
                      x1={CENTER + Math.cos(a) * r0}
                      y1={CENTER + Math.sin(a) * r0}
                      x2={CENTER + Math.cos(a) * r1}
                      y2={CENTER + Math.sin(a) * r1}
                    />
                  );
                })}
              </g>

              {/* ③④⑤ 三环 */}
              {RINGS.map((ring, i) => (
                <g
                  key={i}
                  className={`ec-ring ec-ring--${i + 1}`}
                  ref={(el) => {
                    ringRefs.current[i] = el;
                  }}
                >
                  <circle
                    cx={CENTER}
                    cy={CENTER}
                    r={ring.r}
                    fill="none"
                    strokeWidth={ring.sw}
                    strokeDasharray={ring.dash}
                    opacity={ring.op}
                    strokeLinecap="round"
                  />
                </g>
              ))}

              {/* ⑥⑦ 双反向弧段（青↔紫），pathLength=360 → dash 单位即「度」 */}
              <circle
                className="ec-arc ec-arc--a"
                ref={arcARef}
                cx={CENTER}
                cy={CENTER}
                r={ARC_A.r}
                fill="none"
                strokeWidth={3.5}
                strokeLinecap="round"
                pathLength={360}
              />
              <circle
                className="ec-arc ec-arc--b"
                ref={arcBRef}
                cx={CENTER}
                cy={CENTER}
                r={ARC_B.r}
                fill="none"
                strokeWidth={3.5}
                strokeLinecap="round"
                pathLength={360}
              />

              {/* 涟漪对象池：SVG circle + non-scaling-stroke，避免 scale 把描边变粗 */}
              <g className="ec-ripples">
                {Array.from({ length: RIPPLE_POOL }).map((_, i) => (
                  <circle
                    key={i}
                    className="ec-ripple"
                    ref={(el) => {
                      rippleRefs.current[i] = el;
                    }}
                    cx={CENTER}
                    cy={CENTER}
                    r={64}
                    fill="none"
                    strokeWidth={1.5}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
              </g>

              {/* 思考时的中心三段 spinner */}
              <g className="ec-spinner">
                <circle
                  cx={CENTER}
                  cy={CENTER}
                  r={38}
                  fill="none"
                  strokeWidth={2}
                  strokeDasharray="34 205"
                  strokeLinecap="round"
                  pathLength={239}
                />
                <circle
                  cx={CENTER}
                  cy={CENTER}
                  r={30}
                  fill="none"
                  strokeWidth={2}
                  strokeDasharray="26 162"
                  strokeLinecap="round"
                  pathLength={188}
                  opacity={0.7}
                />
              </g>

              {/* 呼吸核（静态渐变底图，呼吸由 shell 的 scale 承担） */}
              <circle className="ec-pulse" cx={CENTER} cy={CENTER} r={44} fill={`url(#${uid}-glow)`} />
              <circle className="ec-inner-ring" cx={CENTER} cy={CENTER} r={22} fill="none" strokeWidth={1.2} />
              <circle className="ec-dot" cx={CENTER} cy={CENTER} r={6} />
            </svg>
          </div>
        </div>
      </div>

      {/* error 噪点层：静态 conic-gradient 底图，只动 rotate + opacity */}
      <div className="ec-noise" />
      {/* offline 去饱和层：静态 grayscale filter，只动 opacity（不动参数） */}
      <div className="ec-gray" />
    </div>
  );
}
