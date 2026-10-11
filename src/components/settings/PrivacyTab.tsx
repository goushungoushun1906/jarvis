import { useCallback, useEffect, useState } from "react";
import { deleteSecret, getSecretsStatus, saveSecret } from "../../lib/api";
import type { SecretsStatus } from "../../lib/api";

interface PrivacyStats {
  conversation_count: number;
  message_count: number;
  db_size_mb: number;
}

const API_BASE = "http://127.0.0.1:18200";

/** 隐私与数据：统计、导出、清除。 */
export default function PrivacyTab({ active }: { active: boolean }) {
  const [stats, setStats] = useState<PrivacyStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);
  const [secrets, setSecrets] = useState<SecretsStatus | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [secretBusy, setSecretBusy] = useState(false);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/api/privacy/stats`);
      if (resp.ok) {
        setStats(await resp.json());
      } else {
        setMsg("获取数据统计失败");
      }
    } catch {
      setMsg("获取数据统计失败: 无法连接服务器");
    } finally {
      setLoading(false);
    }
  }, []);

  // 只在切到本 tab 且还没有数据时拉一次
  useEffect(() => {
    if (active && !stats) {
      void fetchStats();
    }
    if (active && !secrets) {
      void fetchSecrets();
    }
  }, [active, stats, secrets, fetchStats]);

  const fetchSecrets = useCallback(async () => {
    try {
      setSecrets(await getSecretsStatus());
    } catch {
      // 后端是旧版时没有这个端点，密钥卡片就不显示，不影响其他设置
      setSecrets(null);
    }
  }, []);

  const flash = (text: string) => {
    setMsg(text);
    setTimeout(() => setMsg(null), 3000);
  };

  const handleSaveKey = async () => {
    if (!keyInput.trim()) {
      flash("请输入要保存的 API Key");
      return;
    }
    setSecretBusy(true);
    try {
      await saveSecret("llm_api_key", keyInput.trim());
      setKeyInput("");
      await fetchSecrets();
      flash("已存入系统凭据管理器");
    } catch (e) {
      flash(`存入失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSecretBusy(false);
    }
  };

  const handleDeleteKey = async () => {
    setSecretBusy(true);
    try {
      await deleteSecret("llm_api_key");
      await fetchSecrets();
      flash("已从凭据管理器移除，回退到 .env");
    } catch (e) {
      flash(`移除失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSecretBusy(false);
    }
  };

  const handleExport = async () => {
    setLoading(true);
    setMsg(null);
    try {
      const resp = await fetch(`${API_BASE}/api/privacy/export`);
      if (!resp.ok) {
        setMsg("导出失败: 服务器返回错误");
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `jarvis-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      flash("数据导出成功");
    } catch {
      flash("导出失败: 无法连接服务器");
    } finally {
      setLoading(false);
    }
  };

  const handlePurge = async () => {
    setLoading(true);
    setMsg(null);
    setShowPurgeConfirm(false);
    try {
      const resp = await fetch(`${API_BASE}/api/privacy/purge`, { method: "DELETE" });
      if (!resp.ok) {
        setMsg("清除数据失败: 服务器返回错误");
        return;
      }
      setStats(null);
      flash("所有数据已清除");
    } catch {
      flash("清除数据失败: 无法连接服务器");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="settings-section">
      <div className="settings-section-title">隐私与数据</div>

      {msg && (
        <div
          style={{
            padding: "8px 12px",
            borderRadius: "8px",
            fontSize: "12px",
            marginBottom: "16px",
            background: msg.includes("成功")
              ? "rgba(16,185,129,0.1)"
              : "rgba(239,68,68,0.1)",
            color: msg.includes("成功") ? "var(--success)" : "var(--error)",
            border: `1px solid ${
              msg.includes("成功") ? "rgba(16,185,129,0.2)" : "rgba(239,68,68,0.2)"
            }`,
          }}
        >
          {msg}
        </div>
      )}

      {/* 数据统计 */}
      <div
        style={{
          padding: "16px",
          borderRadius: "8px",
          marginBottom: "16px",
          background: "var(--bg-secondary, rgba(255,255,255,0.03))",
          border: "1px solid var(--border, rgba(255,255,255,0.06))",
        }}
      >
        <div style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "12px" }}>
          数据统计
        </div>
        {loading && !stats ? (
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>加载中...</div>
        ) : stats ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            <div className="about-row">
              <span className="label">对话数量</span>
              <span className="value">{stats.conversation_count}</span>
            </div>
            <div className="about-row">
              <span className="label">消息总数</span>
              <span className="value">{stats.message_count}</span>
            </div>
            <div className="about-row">
              <span className="label">数据库大小</span>
              <span className="value">{stats.db_size_mb.toFixed(2)} MB</span>
            </div>
          </div>
        ) : (
          <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>无数据</div>
        )}
      </div>

      {/* 密钥存储 */}
      {secrets?.supported && (
        <div
          style={{
            padding: "16px",
            borderRadius: "8px",
            marginBottom: "16px",
            background: "var(--bg-secondary, rgba(255,255,255,0.03))",
            border: "1px solid var(--border, rgba(255,255,255,0.06))",
          }}
        >
          <div style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "12px" }}>
            密钥存储
          </div>

          <div className="about-row" style={{ marginBottom: "10px" }}>
            <span className="label">LLM API Key</span>
            <span
              className="value"
              style={{
                color:
                  secrets.sources.llm_api_key === "keychain"
                    ? "var(--success)"
                    : "var(--text-muted)",
              }}
            >
              {secrets.sources.llm_api_key === "keychain"
                ? "系统凭据管理器"
                : secrets.sources.llm_api_key === "env"
                  ? ".env 明文"
                  : "未配置"}
            </span>
          </div>

          <input
            type="password"
            className="setting-input"
            placeholder={
              secrets.sources.llm_api_key === "keychain"
                ? "已存于凭据管理器，可粘贴新值覆盖"
                : "粘贴 API Key 以存入系统凭据管理器"
            }
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            style={{ width: "100%", marginBottom: "10px" }}
          />

          <div style={{ display: "flex", gap: "8px" }}>
            <button
              className="setting-btn"
              onClick={handleSaveKey}
              disabled={secretBusy}
              style={{ flex: 1 }}
            >
              {secretBusy ? "处理中..." : "存入凭据管理器"}
            </button>
            {secrets.sources.llm_api_key === "keychain" && (
              <button
                className="setting-btn"
                onClick={handleDeleteKey}
                disabled={secretBusy}
                style={{ flex: 1 }}
              >
                移除
              </button>
            )}
          </div>

          <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "10px", lineHeight: 1.6 }}>
            存入后由 Windows 凭据管理器保管，优先级高于 <code>.env</code>；<code>.env</code>{" "}
            里那份不会被删除，只是不再生效。
          </div>
        </div>
      )}

      {/* 操作按钮 */}
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <button
          className="setting-btn"
          onClick={handleExport}
          disabled={loading}
          style={{ width: "100%" }}
        >
          {loading ? "处理中..." : "导出数据"}
        </button>

        {!showPurgeConfirm ? (
          <button
            className="setting-btn"
            onClick={() => setShowPurgeConfirm(true)}
            disabled={loading}
            style={{
              width: "100%",
              background: "rgba(239,68,68,0.1)",
              color: "var(--error)",
              border: "1px solid rgba(239,68,68,0.2)",
            }}
          >
            清除所有数据
          </button>
        ) : (
          <div
            style={{
              padding: "12px",
              borderRadius: "8px",
              background: "rgba(239,68,68,0.06)",
              border: "1px solid rgba(239,68,68,0.15)",
            }}
          >
            <div style={{ fontSize: "12px", color: "var(--error)", marginBottom: "10px" }}>
              确定要清除所有数据吗？此操作不可恢复。
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                className="setting-btn"
                onClick={handlePurge}
                disabled={loading}
                style={{
                  flex: 1,
                  background: "rgba(239,68,68,0.15)",
                  color: "var(--error)",
                  border: "1px solid rgba(239,68,68,0.3)",
                }}
              >
                {loading ? "清除中..." : "确认清除"}
              </button>
              <button
                className="setting-btn"
                onClick={() => setShowPurgeConfirm(false)}
                style={{ flex: 1 }}
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
