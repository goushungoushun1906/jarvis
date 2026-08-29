import { useEffect, useMemo, useState } from "react";
import type { CostsSummary, LLMCallRecord, CostsByModel, CostsByDay, ModelTier } from "../lib/types";
import { getCostsSummary, getRecentCosts, getCostsByModel, getCostsByDay } from "../lib/api";

const TIER_LABELS: Record<ModelTier, string> = {
  fast: "快速",
  mid: "均衡",
  deep: "深度",
};

const TIER_COLORS: Record<ModelTier, string> = {
  fast: "#10b981",
  mid: "#3b82f6",
  deep: "#8b5cf6",
};

export default function CostDashboard() {
  const [days, setDays] = useState<number>(30);
  const [summary, setSummary] = useState<CostsSummary | null>(null);
  const [calls, setCalls] = useState<LLMCallRecord[]>([]);
  const [byModel, setByModel] = useState<CostsByModel[]>([]);
  const [byDay, setByDay] = useState<CostsByDay[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, r, m, d] = await Promise.all([
        getCostsSummary(days),
        getRecentCosts(50, 0),
        getCostsByModel(days),
        getCostsByDay(days),
      ]);
      setSummary(s);
      setCalls(r.calls);
      setByModel(m.models);
      setByDay(d.days_data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载成本数据失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAll();
  }, [days]);

  const totalChars = useMemo(
    () => (summary?.total_input_chars || 0) + (summary?.total_output_chars || 0),
    [summary]
  );

  const successRate = useMemo(() => {
    if (!summary || summary.total_calls === 0) return 0;
    return Math.round((summary.successful_calls / summary.total_calls) * 100);
  }, [summary]);

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  const formatCost = (cost: number) => {
    if (cost < 0.001) return "$0.000";
    return `$${cost.toFixed(4)}`;
  };

  const formatNumber = (n: number) => n.toLocaleString("zh-CN");

  return (
    <div className="cost-dashboard">
      <div className="cost-dashboard-header">
        <div>
          <div className="cost-dashboard-title">调用成本看板</div>
          <div className="cost-dashboard-subtitle">基于字符数估算，仅供参考</div>
        </div>
        <div className="cost-range-select">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              className={`cost-range-btn ${days === d ? "active" : ""}`}
              onClick={() => setDays(d)}
            >
              近{d}天
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="cost-status cost-status-error">
          {error}
          <button className="cost-retry-btn" onClick={fetchAll}>重试</button>
        </div>
      )}

      {loading && !summary ? (
        <div className="cost-loading">加载中...</div>
      ) : summary ? (
        <>
          {/* Summary cards */}
          <div className="cost-summary-grid">
            <div className="cost-card">
              <div className="cost-card-label">总调用次数</div>
              <div className="cost-card-value">{formatNumber(summary.total_calls)}</div>
              <div className="cost-card-foot">
                <span className="cost-success">{summary.successful_calls} 成功</span>
                {summary.failed_calls > 0 && (
                  <span className="cost-error"> · {summary.failed_calls} 失败</span>
                )}
              </div>
            </div>
            <div className="cost-card">
              <div className="cost-card-label">估算成本</div>
              <div className="cost-card-value">{formatCost(summary.total_cost)}</div>
              <div className="cost-card-foot">{formatNumber(totalChars)} 字符</div>
            </div>
            <div className="cost-card">
              <div className="cost-card-label">平均耗时</div>
              <div className="cost-card-value">{formatDuration(summary.avg_duration_ms)}</div>
              <div className="cost-card-foot">
                总计 {formatDuration(summary.total_duration_ms)}
              </div>
            </div>
            <div className="cost-card">
              <div className="cost-card-label">成功率</div>
              <div className="cost-card-value">{successRate}%</div>
              <div className="cost-card-foot">基于近 {days} 天数据</div>
            </div>
          </div>

          {/* Model distribution */}
          {byModel.length > 0 && (
            <div className="cost-section">
              <div className="cost-section-title">模型分布</div>
              <div className="cost-model-list">
                {byModel.map((m) => (
                  <div key={`${m.model}-${m.tier}`} className="cost-model-row">
                    <div className="cost-model-info">
                      <div className="cost-model-name">{m.model}</div>
                      <div className="cost-model-meta">
                        <span
                          className="cost-tier-badge"
                          style={{
                            background: `${TIER_COLORS[m.tier]}20`,
                            color: TIER_COLORS[m.tier],
                            border: `1px solid ${TIER_COLORS[m.tier]}40`,
                          }}
                        >
                          {TIER_LABELS[m.tier]}
                        </span>
                        <span>{formatNumber(m.calls)} 次调用</span>
                      </div>
                    </div>
                    <div className="cost-model-stats">
                      <div className="cost-model-stat">
                        <div className="cost-model-stat-value">{formatCost(m.cost)}</div>
                        <div className="cost-model-stat-label">成本</div>
                      </div>
                      <div className="cost-model-stat">
                        <div className="cost-model-stat-value">{formatDuration(m.duration_ms)}</div>
                        <div className="cost-model-stat-label">耗时</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Daily trend */}
          {byDay.length > 0 && (
            <div className="cost-section">
              <div className="cost-section-title">每日趋势</div>
              <div className="cost-day-list">
                {byDay.map((d) => (
                  <div key={d.day} className="cost-day-row">
                    <div className="cost-day-label">{d.day}</div>
                    <div className="cost-day-bar-wrap">
                      <div
                        className="cost-day-bar"
                        style={{
                          width: `${Math.min(100, Math.max(4, d.calls * 4))}%`,
                        }}
                      />
                    </div>
                    <div className="cost-day-values">
                      <span>{formatNumber(d.calls)} 次</span>
                      <span className="cost-day-cost">{formatCost(d.cost)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent calls */}
          {calls.length > 0 && (
            <div className="cost-section">
              <div className="cost-section-title">最近调用</div>
              <div className="cost-table-wrap">
                <table className="cost-table">
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>模型</th>
                      <th>层级</th>
                      <th>输入</th>
                      <th>输出</th>
                      <th>耗时</th>
                      <th>成本</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {calls.map((c) => (
                      <tr key={c.id} className={c.success ? "" : "cost-row-failed"}>
                        <td>{new Date(c.timestamp).toLocaleString("zh-CN")}</td>
                        <td className="cost-table-mono">{c.model}</td>
                        <td>
                          <span
                            className="cost-tier-badge"
                            style={{
                              background: `${TIER_COLORS[c.tier]}20`,
                              color: TIER_COLORS[c.tier],
                              border: `1px solid ${TIER_COLORS[c.tier]}40`,
                            }}
                          >
                            {TIER_LABELS[c.tier]}
                          </span>
                        </td>
                        <td>{formatNumber(c.input_chars)}</td>
                        <td>{formatNumber(c.output_chars)}</td>
                        <td>{formatDuration(c.duration_ms)}</td>
                        <td>{formatCost(c.cost_estimate)}</td>
                        <td>
                          {c.success ? (
                            <span className="cost-success">成功</span>
                          ) : (
                            <span className="cost-error" title={c.error || undefined}>失败</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {byModel.length === 0 && calls.length === 0 && !loading && (
            <div className="cost-empty">近 {days} 天内暂无调用记录</div>
          )}
        </>
      ) : null}
    </div>
  );
}
