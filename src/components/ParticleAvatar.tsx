import React, { useEffect, useRef } from "react";
import "./ParticleAvatar.css";

export type AvatarState = "idle" | "listening" | "thinking" | "speaking" | "error" | "offline";

interface ParticleAvatarProps {
  state: AvatarState;
  /**
   * TTS 播放中的实时音量（0..1）。
   *
   * 用 ref 而不是 number prop：这个值每帧都在变，走 prop 会让 React 以 60fps
   * 重渲染整棵子树。动画循环自己每帧读 ref.current 就够了。
   */
  levelRef?: React.MutableRefObject<number>;
  className?: string;
}

const STATE_CONFIG: Record<AvatarState, { hue: number; sat: number; light: number; glow: string; speed: number }> = {
  idle: { hue: 210, sat: 80, light: 55, glow: "rgba(59,130,246,.28)", speed: 0.5 },
  listening: { hue: 190, sat: 90, light: 55, glow: "rgba(34,211,238,.35)", speed: 1.1 },
  thinking: { hue: 260, sat: 80, light: 60, glow: "rgba(139,92,246,.38)", speed: 2.2 },
  speaking: { hue: 195, sat: 95, light: 58, glow: "rgba(34,211,238,.45)", speed: 3.0 },
  error: { hue: 0, sat: 80, light: 60, glow: "rgba(248,113,113,.40)", speed: 0.3 },
  offline: { hue: 215, sat: 20, light: 50, glow: "rgba(148,163,184,.15)", speed: 0.2 },
};

interface Particle {
  theta: number;
  phi: number;
  baseR: number;
  r: number;
  size: number;
  phase: number;
  speed: number;
  band: number;
}

function createParticles(count: number): Particle[] {
  const pts: Particle[] = [];
  for (let i = 0; i < count; i++) {
    const theta = Math.acos(2 * Math.random() - 1);
    const phi = Math.random() * Math.PI * 2;
    // 椭球头部 + 嘴部外凸
    let baseR = 42 + Math.random() * 88;
    if (Math.random() < 0.18) baseR = 120 + Math.random() * 45; // 外层稀薄
    // 嘴部区域（下半球）轻微外凸
    if (theta > Math.PI * 0.55) baseR *= 1.08 + Math.random() * 0.06;
    pts.push({
      theta,
      phi,
      baseR,
      r: baseR,
      size: 1.2 + Math.random() * 2.2,
      phase: Math.random() * Math.PI * 2,
      speed: 0.6 + Math.random() * 0.8,
      band: Math.floor(Math.random() * 8),
    });
  }
  return pts;
}

const ParticleAvatar: React.FC<ParticleAvatarProps> = ({ state, levelRef, className = "" }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>(createParticles(1200));
  const stateRef = useRef(state);
  const levelRefOfRef = useRef(levelRef);
  const energyRef = useRef(0);
  const timeRef = useRef(0);

  useEffect(() => { stateRef.current = state; }, [state]);
  useEffect(() => { levelRefOfRef.current = levelRef; }, [levelRef]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    let raf = 0;
    const frame = (t: number) => {
      const dt = 0.016;
      timeRef.current += dt;
      const cfg = STATE_CONFIG[stateRef.current];
      const rawLevel = levelRefOfRef.current?.current ?? 0;
      const targetEnergy = rawLevel * (stateRef.current === "speaking" ? 1.0 : 0.55) + (stateRef.current === "thinking" ? 0.18 : 0);
      energyRef.current += (targetEnergy - energyRef.current) * 0.12;
      const energy = energyRef.current;

      const w = rect.width;
      const h = rect.height;
      const cx = w / 2;
      const cy = h / 2 - 12;

      ctx.clearRect(0, 0, w, h);

      // 背景网格
      ctx.save();
      ctx.strokeStyle = "rgba(59,130,246,.06)";
      ctx.lineWidth = 1;
      const gridSize = 28;
      const offset = (timeRef.current * 4) % gridSize;
      for (let x = -gridSize; x < w + gridSize; x += gridSize) {
        ctx.beginPath();
        ctx.moveTo(x + offset * 0.3, 0);
        ctx.lineTo(x + offset * 0.3, h);
        ctx.stroke();
      }
      for (let y = -gridSize; y < h + gridSize; y += gridSize) {
        ctx.beginPath();
        ctx.moveTo(0, y + offset * 0.3);
        ctx.lineTo(w, y + offset * 0.3);
        ctx.stroke();
      }
      ctx.restore();

      // 中央深色收敛核心
      const coreGrad = ctx.createRadialGradient(cx, cy, 4, cx, cy, 110 + energy * 40);
      coreGrad.addColorStop(0, "rgba(15,23,42,.55)");
      coreGrad.addColorStop(0.45, "rgba(30,58,138,.22)");
      coreGrad.addColorStop(1, "rgba(30,58,138,0)");
      ctx.fillStyle = coreGrad;
      ctx.beginPath();
      ctx.ellipse(cx, cy + 10, 72 + energy * 18, 88 + energy * 22, 0, 0, Math.PI * 2);
      ctx.fill();

      // 外层光晕
      const glowSize = 140 + energy * 90;
      const glow = ctx.createRadialGradient(cx, cy, 20, cx, cy, glowSize);
      glow.addColorStop(0, cfg.glow);
      glow.addColorStop(1, "rgba(59,130,246,0)");
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(cx, cy, glowSize, 0, Math.PI * 2);
      ctx.fill();

      const pts = particlesRef.current;
      const cloudScale = 1 + energy * 0.28 + Math.sin(timeRef.current * cfg.speed) * 0.03;
      const breath = 1 + Math.sin(timeRef.current * cfg.speed * 1.3) * 0.04;

      // 计算粒子位置
      const projected = pts.map((p, i) => {
        const slowDrift = Math.sin(timeRef.current * 0.7 + p.phase) * 0.02;
        let r = p.baseR * cloudScale * breath + energy * (6 + p.band * 2) + slowDrift * 10;
        const maxR = p.baseR > 110 ? 175 : 150;
        if (r > maxR) r = maxR - (r - maxR) * 0.35;
        p.r += (r - p.r) * 0.12;

        const rotY = timeRef.current * 0.08 * cfg.speed + energy * 0.4;
        const rotX = Math.sin(timeRef.current * 0.12) * 0.08;

        let x = p.r * Math.sin(p.theta) * Math.cos(p.phi);
        let y = p.r * Math.cos(p.theta) * 0.92; // 压扁一点像人脸
        let z = p.r * Math.sin(p.theta) * Math.sin(p.phi);

        // 绕 Y 轴
        const xr = x * Math.cos(rotY) - z * Math.sin(rotY);
        const zr = x * Math.sin(rotY) + z * Math.cos(rotY);
        x = xr; z = zr;

        // 绕 X 轴轻微摆动
        const yr = y * Math.cos(rotX) - z * Math.sin(rotX);
        const zr2 = y * Math.sin(rotX) + z * Math.cos(rotX);
        y = yr; z = zr2;

        // 说话时局部抖动
        if (energy > 0.05) {
          const shake = energy * (2 + Math.sin(timeRef.current * 12 + i) * 1.5);
          x += (Math.random() - 0.5) * shake;
          y += (Math.random() - 0.5) * shake;
        }

        const scale = 260 / (260 + z);
        return {
          sx: cx + x * scale,
          sy: cy - y * scale,
          z,
          size: Math.max(0.6, p.size * scale * (1 + energy * 0.5)),
          alpha: 0.35 + 0.65 * Math.max(0, (z + 80) / 160) + energy * 0.35,
        };
      });

      // 按 z 排序
      projected.sort((a, b) => a.z - b.z);

      // 画拖尾
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < projected.length; i++) {
        const p = projected[i];
        if (p.alpha < 0.2) continue;
        const a = p.alpha * (0.4 + energy * 0.5);
        ctx.fillStyle = `hsla(${cfg.hue},${cfg.sat}%,${cfg.light}%,${a})`;
        ctx.beginPath();
        ctx.arc(p.sx, p.sy, p.size, 0, Math.PI * 2);
        ctx.fill();
        // 小拖尾
        if (energy > 0.1) {
          ctx.fillStyle = `hsla(${cfg.hue},${cfg.sat}%,${cfg.light + 10}%,${a * 0.35})`;
          ctx.beginPath();
          ctx.arc(p.sx - energy * 3, p.sy - energy * 2, p.size * 0.7, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();

      // 底部频谱条
      const barCount = 40;
      const barW = 3;
      const radius = 118 + energy * 30;
      ctx.save();
      ctx.translate(cx, cy + 12);
      for (let i = 0; i < barCount; i++) {
        const ang = (i / barCount) * Math.PI * 2 + timeRef.current * 0.25 * cfg.speed;
        const h = 8 + Math.sin(timeRef.current * 3 + i * 0.7) * 4 + energy * (18 + Math.sin(i * 1.1) * 10);
        const x = Math.cos(ang) * radius;
        const y = Math.sin(ang) * radius * 0.25;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(ang + Math.PI / 2);
        const grad = ctx.createLinearGradient(0, 0, 0, -h);
        grad.addColorStop(0, `hsla(${cfg.hue},${cfg.sat}%,${cfg.light}%,0)`);
        grad.addColorStop(1, `hsla(${cfg.hue},${cfg.sat}%,${cfg.light}%,${0.55 + energy * 0.3})`);
        ctx.fillStyle = grad;
        ctx.fillRect(-barW / 2, 0, barW, -h);
        ctx.restore();
      }
      ctx.restore();

      // 状态文字
      ctx.save();
      ctx.font = "600 12px ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont";
      ctx.textAlign = "center";
      ctx.fillStyle = `hsla(${cfg.hue},${cfg.sat}%,${cfg.light}%,.85)`;
      const labels: Record<AvatarState, string> = {
        idle: "JARVIS",
        listening: "聆听中…",
        thinking: "思考中…",
        speaking: "说话中…",
        error: "连接异常",
        offline: "离线",
      };
      ctx.fillText(labels[stateRef.current], cx, h - 28);
      ctx.restore();

      raf = requestAnimationFrame(frame);
    };

    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className={`particle-avatar ${className}`}>
      <canvas ref={canvasRef} className="particle-avatar-canvas" />
    </div>
  );
};

export default ParticleAvatar;
