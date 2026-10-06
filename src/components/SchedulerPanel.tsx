import { useState, useEffect, useCallback } from "react";
import "./SchedulerPanel.css";

interface SchedulerInfo {
  status: "running" | "stopped";
  jobs: SchedulerJob[];
}

interface SchedulerJob {
  id: string;
  name: string;
  cron: string;
  enabled: boolean;
  next_run?: string;
  last_run?: string;
}

interface SchedulerPanelProps {
  onClose: () => void;
}

export default function SchedulerPanel({ onClose }: SchedulerPanelProps) {
  const [info, setInfo] = useState<SchedulerInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  const fetchInfo = useCallback(async () => {
    try {
      const resp = await fetch("http://127.0.0.1:18200/api/scheduler/info");
      if (resp.ok) {
        const data = await resp.json();
        setInfo(data);
      }
    } catch { /* backend offline */ } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchInfo();
    const interval = setInterval(fetchInfo, 30000);
    return () => clearInterval(interval);
  }, [fetchInfo]);

  const handleToggle = async (jobId: string, enabled: boolean) => {
    try {
      await fetch(`http://127.0.0.1:18200/api/scheduler/jobs/${jobId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      fetchInfo();
    } catch (e) { console.error("Toggle job failed:", e); }
  };

  const handleDelete = async (jobId: string) => {
    try {
      await fetch(`http://127.0.0.1:18200/api/scheduler/jobs/${jobId}`, { method: "DELETE" });
      fetchInfo();
    } catch (e) { console.error("Delete job failed:", e); }
  };

  const handleTriggerDigest = async () => {
    setTriggering(true);
    try {
      await fetch("http://127.0.0.1:18200/api/scheduler/trigger-digest", { method: "POST" });
      fetchInfo();
    } catch (e) { console.error("Trigger digest failed:", e); }
    setTimeout(() => setTriggering(false), 2000);
  };

  const handleStartStop = async () => {
    const nextStatus = info?.status === "running" ? "stop" : "start";
    try {
      await fetch(`http://127.0.0.1:18200/api/scheduler/${nextStatus}`, { method: "POST" });
      fetchInfo();
    } catch (e) { console.error("Start/stop failed:", e); }
  };

  return (
    <div className="scheduler-overlay" onClick={onClose}>
      <div className="scheduler-panel" onClick={e => e.stopPropagation()}>
        <div className="scheduler-header">
          <span className="scheduler-title">定时任务</span>
          <button className="scheduler-close" onClick={onClose} title="关闭">×</button>
        </div>

        <div className="scheduler-body">
          {loading ? (
            <div className="scheduler-loading">加载中…</div>
          ) : info ? (
            <>
              {/* Status control */}
              <div className="scheduler-status-bar">
                <span className={`scheduler-status-badge ${info.status}`}>
                  {info.status === "running" ? "运行中" : "已停止"}
                </span>
                <button className="scheduler-toggle-btn" onClick={handleStartStop}>
                  {info.status === "running" ? "停止调度" : "启动调度"}
                </button>
                <button className="scheduler-digest-btn" onClick={handleTriggerDigest} disabled={triggering}>
                  {triggering ? "触发中…" : "立即触发早报"}
                </button>
              </div>

              {/* Job list */}
              {info.jobs.length === 0 ? (
                <div className="scheduler-empty">暂无定时任务</div>
              ) : (
                <div className="scheduler-jobs">
                  {info.jobs.map(job => (
                    <div key={job.id} className="scheduler-job-card">
                      <div className="scheduler-job-info">
                        <div className="scheduler-job-name">{job.name}</div>
                        <div className="scheduler-job-cron">{job.cron}</div>
                        {job.next_run && (
                          <div className="scheduler-job-meta">下次执行: {job.next_run}</div>
                        )}
                      </div>
                      <div className="scheduler-job-actions">
                        <label className="scheduler-switch">
                          <input
                            type="checkbox"
                            checked={job.enabled}
                            onChange={e => handleToggle(job.id, e.target.checked)}
                          />
                          <span className="scheduler-switch-track" />
                        </label>
                        <button className="scheduler-delete-btn" onClick={() => handleDelete(job.id)} title="删除任务">
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                          </svg>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="scheduler-error">无法连接调度服务</div>
          )}
        </div>
      </div>
    </div>
  );
}
