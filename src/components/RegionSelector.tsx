import React, { useEffect, useRef, useState, useCallback } from "react";

export interface Region {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface RegionSelectorProps {
  onCapture: (region: Region) => void;
  onCancel: () => void;
}

export default function RegionSelector({ onCapture, onCancel }: RegionSelectorProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [startPos, setStartPos] = useState<{ x: number; y: number } | null>(null);
  const [currentPos, setCurrentPos] = useState<{ x: number; y: number } | null>(null);

  const getScreenRegion = useCallback((rect: { left: number; top: number; width: number; height: number }): Region => {
    // 将浏览器视口坐标转换为屏幕坐标（近似，适用于 Electron / 浏览器）
    return {
      left: Math.round(rect.left + window.screenX),
      top: Math.round(rect.top + window.screenY),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
    setStartPos({ x: e.clientX, y: e.clientY });
    setCurrentPos({ x: e.clientX, y: e.clientY });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging || !startPos) return;
    setCurrentPos({ x: e.clientX, y: e.clientY });
  };

  const handleMouseUp = () => {
    if (!isDragging || !startPos || !currentPos) return;
    setIsDragging(false);

    const left = Math.min(startPos.x, currentPos.x);
    const top = Math.min(startPos.y, currentPos.y);
    const width = Math.abs(currentPos.x - startPos.x);
    const height = Math.abs(currentPos.y - startPos.y);

    if (width < 4 || height < 4) {
      // 点击未形成有效选区，视为取消
      onCancel();
      return;
    }

    onCapture(getScreenRegion({ left, top, width, height }));
  };

  const handleTouchStart = (e: React.TouchEvent) => {
    e.preventDefault();
    const touch = e.touches[0];
    setIsDragging(true);
    setStartPos({ x: touch.clientX, y: touch.clientY });
    setCurrentPos({ x: touch.clientX, y: touch.clientY });
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!isDragging || !startPos) return;
    const touch = e.touches[0];
    setCurrentPos({ x: touch.clientX, y: touch.clientY });
  };

  const handleTouchEnd = () => {
    handleMouseUp();
  };

  const selectionRect = (() => {
    if (!startPos || !currentPos) return null;
    const left = Math.min(startPos.x, currentPos.x);
    const top = Math.min(startPos.y, currentPos.y);
    const width = Math.abs(currentPos.x - startPos.x);
    const height = Math.abs(currentPos.y - startPos.y);
    return { left, top, width, height };
  })();

  const tooltipText = selectionRect
    ? `${selectionRect.width} × ${selectionRect.height}`
    : "拖动鼠标框选屏幕区域，按 Esc 取消";

  return (
    <div
      ref={overlayRef}
      className="region-selector-overlay"
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      <div className="region-selector-hint">
        <span>{tooltipText}</span>
        <button className="region-selector-cancel" onClick={onCancel}>
          取消
        </button>
      </div>

      {selectionRect && selectionRect.width > 0 && selectionRect.height > 0 && (
        <>
          <div
            className="region-selector-dim"
            style={{
              left: 0,
              top: 0,
              width: "100%",
              height: `${selectionRect.top}px`,
            }}
          />
          <div
            className="region-selector-dim"
            style={{
              left: 0,
              top: `${selectionRect.top}px`,
              width: `${selectionRect.left}px`,
              height: `${selectionRect.height}px`,
            }}
          />
          <div
            className="region-selector-dim"
            style={{
              left: `${selectionRect.left + selectionRect.width}px`,
              top: `${selectionRect.top}px`,
              width: `calc(100% - ${selectionRect.left + selectionRect.width}px)`,
              height: `${selectionRect.height}px`,
            }}
          />
          <div
            className="region-selector-dim"
            style={{
              left: 0,
              top: `${selectionRect.top + selectionRect.height}px`,
              width: "100%",
              height: `calc(100% - ${selectionRect.top + selectionRect.height}px)`,
            }}
          />
          <div
            className="region-selector-box"
            style={{
              left: `${selectionRect.left}px`,
              top: `${selectionRect.top}px`,
              width: `${selectionRect.width}px`,
              height: `${selectionRect.height}px`,
            }}
          >
            <span className="region-selector-size">
              {selectionRect.width} × {selectionRect.height}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
