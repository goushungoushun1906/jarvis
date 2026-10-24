import React from "react";
import "./HudBackground.css";

/* ==========================================================================
   HudBackground —— 背景网格 + 四角括号 + 扫描线
   --------------------------------------------------------------------------
   必须很淡，不能抢戏：网格 ≤.06、扫描线 ≤.10、括号 ≤.55 但只占 26px。
   全部走 CSS animation（合成属性），不进 JS 帧预算。
   纯装饰：aria-hidden + pointer-events:none。
   ========================================================================== */

export interface HudBackgroundProps {
  /** 关闭扫描线（Tier B 以下由 CSS 自动关）。 */
  scan?: boolean;
  grid?: boolean;
  brackets?: boolean;
  className?: string;
}

export default function HudBackground({
  scan = true,
  grid = true,
  brackets = true,
  className = "",
}: HudBackgroundProps) {
  return (
    <div className={`hb ${className}`} aria-hidden="true">
      {grid && <div className="hb-grid" />}
      <div className="hb-vignette" />
      {scan && <div className="hb-scan" />}
      {brackets && (
        <>
          <span className="hb-corner hb-corner--tl" />
          <span className="hb-corner hb-corner--tr" />
          <span className="hb-corner hb-corner--bl" />
          <span className="hb-corner hb-corner--br" />
        </>
      )}
    </div>
  );
}
