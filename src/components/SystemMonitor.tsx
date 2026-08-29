import { useEffect, useState, useRef, useCallback } from "react";
import { getSystemMetrics, type SystemMetrics } from "../lib/api";
import "./SystemMonitor.css";

const STORAGE_KEY = "jarvis-system-monitor-pos";

interface Point {
  x: number;
  y: number;
}

function loadSavedPosition(): Point {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (typeof parsed.x === "number" && typeof parsed.y === "number") {
        return parsed;
      }
    }
  } catch {
    // ignore
  }
  return { x: window.innerWidth - 252, y: 80 };
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export default function SystemMonitor() {
  const [metrics, setMetrics] = useState<SystemMetrics["metrics"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [collapsed, setCollapsed] = useState(false);

  const [position, setPosition] = useState<Point>(() => loadSavedPosition());
  const positionRef = useRef(position);
  const dragRef = useRef<{ startX: number; startY: number; initialX: number; initialY: number } | null>(null);
  const monitorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    positionRef.current = position;
  }, [position]);

  const fetchMetrics = async () => {
    try {
      const data = await getSystemMetrics();
      if (data.success) {
        setMetrics(data.metrics);
        setError(null);
      } else {
        setError("metrics unavailable");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);
    return () => clearInterval(interval);
  }, []);

  // Keep inside viewport on resize
  useEffect(() => {
    const handleResize = () => {
      const el = monitorRef.current;
      const width = el?.offsetWidth ?? 220;
      const height = el?.offsetHeight ?? 160;
      setPosition((prev) => ({
        x: clamp(prev.x, 8, window.innerWidth - width - 8),
        y: clamp(prev.y, 8, window.innerHeight - height - 8),
      }));
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;

    const el = monitorRef.current;
    const width = el?.offsetWidth ?? 220;
    const height = el?.offsetHeight ?? 160;

    const nextX = clamp(dragRef.current.initialX + dx, 8, window.innerWidth - width - 8);
    const nextY = clamp(dragRef.current.initialY + dy, 8, window.innerHeight - height - 8);

    setPosition({ x: nextX, y: nextY });
  }, []);

  const handleMouseUp = useCallback(() => {
    dragRef.current = null;
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", handleMouseMove);
    document.removeEventListener("mouseup", handleMouseUp);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(positionRef.current));
    } catch {
      // ignore
    }
  }, [handleMouseMove]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    // Only drag from header, allow toggle click to still work
    if ((e.target as HTMLElement).closest(".monitor-toggle")) return;
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      initialX: positionRef.current.x,
      initialY: positionRef.current.y,
    };
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  }, [handleMouseMove, handleMouseUp]);

  const cpu = metrics?.cpu_percent ?? 0;
  const ram = metrics?.ram_percent ?? 0;
  const disk = metrics?.disk_percent ?? 0;

  return (
    <div
      ref={monitorRef}
      className={`system-monitor ${collapsed ? "collapsed" : ""}`}
      style={{ left: position.x, top: position.y }}
    >
      <div className="monitor-header" onMouseDown={handleMouseDown}>
        <div className="monitor-header-left">
          <span className="monitor-drag-grip" title="拖拽移动">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="9" cy="6" r="1.5" />
              <circle cx="9" cy="12" r="1.5" />
              <circle cx="9" cy="18" r="1.5" />
              <circle cx="15" cy="6" r="1.5" />
              <circle cx="15" cy="12" r="1.5" />
              <circle cx="15" cy="18" r="1.5" />
            </svg>
          </span>
          <span className="monitor-title">SYSTEM</span>
        </div>
        <span className="monitor-toggle" onClick={() => setCollapsed((v) => !v)}>
          {collapsed ? "▲" : "▼"}
        </span>
      </div>
      {!collapsed && (
        <div className="monitor-body">
          {loading && !metrics ? (
            <div className="monitor-loading">扫描中...</div>
          ) : error ? (
            <div className="monitor-error">{error}</div>
          ) : (
            <>
              <MetricBar label="CPU" value={cpu} color="#00d4ff" />
              <MetricBar label="RAM" value={ram} color="#00ff88" />
              <MetricBar label="DISK" value={disk} color="#ffaa00" />
              {metrics?.battery_percent !== undefined && (
                <div className="monitor-battery">
                  BAT {metrics.battery_percent}% {metrics.battery_plugged ? "⚡" : ""}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MetricBar({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div className="metric-row">
      <span className="metric-label">{label}</span>
      <div className="metric-bar-bg">
        <div
          className="metric-bar-fill"
          style={{ width: `${Math.min(100, Math.max(0, value))}%`, background: color }}
        />
      </div>
      <span className="metric-value" style={{ color }}>
        {value.toFixed(0)}%
      </span>
    </div>
  );
}
