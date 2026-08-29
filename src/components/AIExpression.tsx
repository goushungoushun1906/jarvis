import React from "react";
import "./AIExpression.css";

export type ExpressionState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "error"
  | "offline";

interface AIExpressionProps {
  state: ExpressionState;
  className?: string;
  compact?: boolean;
}

export default function AIExpression({
  state,
  className = "",
  compact = false,
}: AIExpressionProps) {
  return (
    <div className={`ai-expression ${compact ? "compact" : ""} ${state} ${className}`}>
      {/* Outer glow */}
      <div className="exp-outer-glow" />

      {/* Ring system */}
      <div className="exp-rings">
        <div className="exp-ring ring-1" />
        <div className="exp-ring ring-2" />
        <div className="exp-ring ring-3" />
        <div className="exp-ring ring-4" />
      </div>

      {/* Core eye */}
      <div className="exp-core">
        <div className="exp-core-inner" />
        <div className="exp-core-dot" />
        {/* Scan line */}
        <div className="exp-scan-line" />
      </div>

      {/* Face arcs — morphing expression */}
      <div className="exp-face">
        <div className="exp-face-arc arc-left" />
        <div className="exp-face-arc arc-right" />
        <div className="exp-face-arc arc-bottom" />
      </div>

      {/* Wave equalizer */}
      <div className="exp-wave">
        {Array.from({ length: 24 }).map((_, i) => (
          <div
            key={i}
            className="exp-wave-bar"
            style={{ "--bar-delay": `${i * 0.06}s` } as React.CSSProperties}
          />
        ))}
      </div>

      {/* Floating particles */}
      <div className="exp-particles">
        {Array.from({ length: 12 }).map((_, i) => (
          <div
            key={i}
            className="exp-particle"
            style={{
              "--p-angle": `${i * 30}deg`,
              "--p-delay": `${i * 0.25}s`,
              "--p-dist": `${60 + (i % 3) * 15}px`,
              "--p-size": `${2 + (i % 3)}px`,
            } as React.CSSProperties}
          />
        ))}
      </div>

      {/* Status text */}
      {!compact && (
        <div className="exp-status">
          {state === "idle" && "J.A.R.V.I.S"}
          {state === "listening" && "LISTENING..."}
          {state === "thinking" && "PROCESSING..."}
          {state === "speaking" && "SPEAKING"}
          {state === "error" && "ERROR"}
          {state === "offline" && "OFFLINE"}
        </div>
      )}
    </div>
  );
}
