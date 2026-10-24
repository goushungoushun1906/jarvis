import React, { useEffect, useRef } from "react";
import { clamp, rgba, type RGB } from "./motionRefs";
import { barCountFor, type FrameCtx, type MotionRegister } from "./useJarvisMotion";
import "./WaveformBar.css";

/* ==========================================================================
   WaveformBar —— 频谱条 / 波形可视化
   --------------------------------------------------------------------------
   ⚠️ 与规范 §4.2 的一处**有意偏离**（性能取舍，已评估）：
   规范写的是 64 个 DOM div 各写一次 `transform: scaleY()`。但那等于每帧对
   64 个元素做 inline 写入，直接打破「每帧 ≤8 个白名单元素」的死线，且 64 个
   独立合成层不现实（规范 §7.2 第 10 条：合成层 ≤12）。
   因此这里改用 **Canvas**：每帧 DOM 写入 = 0（位图绘制不触发 style recalc），
   频谱条数 64/32/16 与降级规则完全照搬规范 §7.4。
   视觉结果与 DOM 方案一致（条宽 3px / gap 3px / 高 4–56px / 峰值帽）。

   静音时基线高度 4px **不隐藏**——隐藏会让人以为没在工作（规范 §2.3）。
   ========================================================================== */

export interface WaveformBarProps {
  /** 唯一 rAF 编排器的注册函数。 */
  register: MotionRegister;
  /** 当前状态色（ref），由 useJarvisMotion 维护。 */
  coreColorRef?: React.MutableRefObject<RGB>;
  /** 条数上限。最终条数还会受 tier / reduced-motion 约束（min）。 */
  count?: number;
  /** 单条最大高度（px）。默认 56。 */
  maxHeight?: number;
  /** CSS 宽度（px）。默认 480。 */
  width?: number;
  className?: string;
}

export default function WaveformBar({
  register,
  coreColorRef,
  count = 64,
  maxHeight = 56,
  width = 480,
  className = "",
}: WaveformBarProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const cvs = canvasRef.current;
    if (!cvs) return;
    const g = cvs.getContext("2d");
    if (!g) return;

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const h = maxHeight + 6; // 顶部留 6px 给峰值帽
    cvs.width = Math.round(width * dpr);
    cvs.height = Math.round(h * dpr);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);

    let heights: Float32Array = new Float32Array(count);
    let peaks: Float32Array = new Float32Array(count);
    let acc = 0;

    const apply = (ctx: FrameCtx) => {
      const n = Math.min(count, barCountFor(ctx.tier, ctx.reduced));
      if (n <= 0) {
        g.clearRect(0, 0, width, h);
        return;
      }
      if (heights.length !== n) {
        heights = new Float32Array(n);
        peaks = new Float32Array(n);
      }

      // reduced-motion / Tier C：降到 10fps（保留"有声音"的证据，去掉闪烁）
      acc += ctx.dt;
      if (ctx.reduced && acc < 100) return;
      acc = 0;

      g.clearRect(0, 0, width, h);

      const src = ctx.bands;
      const step = Math.max(1, Math.floor(src.length / n));
      const gap = 3;
      const bw = Math.max(1, (width - gap * (n - 1)) / n);
      const col = coreColorRef?.current;

      for (let i = 0; i < n; i++) {
        const band = src[Math.min(src.length - 1, i * step)];
        // h = 4 + 52 * pow(band, 1.15)；回落 h = max(target, prev*0.86)
        const target = 4 + 52 * Math.pow(clamp(band, 0, 1), 1.15);
        const hp = Math.max(target, heights[i] * 0.86);
        heights[i] = hp;
        // 峰值帽每帧下落 0.9px
        peaks[i] = Math.max(hp, peaks[i] - 0.9);

        const x = i * (bw + gap);
        const y = h - hp;

        g.fillStyle = col ? rgba(col, 0.85) : "rgba(53,224,255,0.85)";
        g.fillRect(x, y, bw, hp);

        g.fillStyle = col ? rgba(col, 0.55) : "rgba(53,224,255,0.55)";
        g.fillRect(x, h - peaks[i] - 2, bw, 2);
      }
    };

    const unregister = register({ apply });
    return () => {
      unregister();
      g.clearRect(0, 0, width, h);
    };
  }, [register, coreColorRef, count, maxHeight, width]);

  return (
    <div className={`wf ${className}`} style={{ width }}>
      <canvas
        className="wf-canvas"
        ref={canvasRef}
        style={{ width, height: maxHeight + 6 }}
        aria-hidden="true"
      />
    </div>
  );
}
