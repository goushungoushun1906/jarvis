import { useState, useEffect, useCallback, useRef } from "react";
import type { AppConfig, ConnectionStatus } from "../lib/types";
import "./PluginManagement.css";

const API_BASE = "http://127.0.0.1:18200";

interface PluginInfo {
  name: string;
  version: string;
  description: string;
  author: string;
  enabled: boolean;
  commands: Array<{
    name: string;
    description: string;
    parameters: Record<string, unknown>;
  }>;
  config_schema: Array<{
    key: string;
    label: string;
    type: string;
    default: unknown;
    description: string;
    options: string[];
  }>;
}

interface PluginConfig {
  key: string;
  label: string;
  type: string;
  default: unknown;
  description: string;
  options: string[];
  value: unknown;
}

interface PluginManagementProps {
  connectionStatus: ConnectionStatus;
  onConfigUpdate: (config: AppConfig) => void;
}

/** Map known plugin names to emoji icons and color classes. */
const PLUGIN_ICONS: Record<string, { emoji: string; cls: string }> = {
  calendar: { emoji: "\uD83D\uDCC5", cls: "calendar" },
  clipboard: { emoji: "\uD83D\uDCCB", cls: "clipboard" },
  reminder: { emoji: "\u23F0", cls: "reminder" },
  system_monitor: { emoji: "\uD83D\uDCBB", cls: "system_monitor" },
  weather: { emoji: "\uD83C\uDF24\uFE0F", cls: "weather" },
};

function getPluginIcon(name: string) {
  const key = Object.keys(PLUGIN_ICONS).find((k) => name.toLowerCase().includes(k));
  if (key) return PLUGIN_ICONS[key];
  return { emoji: name.charAt(0).toUpperCase(), cls: "default" };
}

export default function PluginManagement({
  connectionStatus,
  onConfigUpdate,
}: PluginManagementProps) {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [configuringPlugin, setConfiguringPlugin] = useState<string | null>(null);
  const [pluginConfig, setPluginConfig] = useState<PluginConfig[]>([]);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [configSaving, setConfigSaving] = useState(false);
  const [configMsg, setConfigMsg] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchPlugins = useCallback(async () => {
    if (connectionStatus !== "online") {
      setLoading(false);
      return;
    }
    try {
      const resp = await fetch(`${API_BASE}/api/plugins`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setPlugins(data);
      setError(null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [connectionStatus]);

  useEffect(() => {
    fetchPlugins();
  }, [fetchPlugins]);

  const togglePlugin = async (name: string, enabled: boolean) => {
    try {
      const endpoint = enabled
        ? `${API_BASE}/api/plugins/${name}/enable`
        : `${API_BASE}/api/plugins/${name}/disable`;
      const resp = await fetch(endpoint, { method: "POST" });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw new Error(body.detail || body.error || `HTTP ${resp.status}`);
      }
      fetchPlugins();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setTimeout(() => setError(null), 5000);
    }
  };

  const reloadPlugin = async (name: string) => {
    try {
      const resp = await fetch(`${API_BASE}/api/plugins/${name}/reload`, { method: "POST" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      fetchPlugins();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setTimeout(() => setError(null), 5000);
    }
  };

  const uninstallPlugin = async (name: string) => {
    if (!confirm(`确定要卸载插件 "${name}" 吗？此操作不可撤销。`)) return;
    try {
      const resp = await fetch(`${API_BASE}/api/plugins/${name}/uninstall`, { method: "POST" });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw new Error(body.detail || body.error || `HTTP ${resp.status}`);
      }
      fetchPlugins();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setTimeout(() => setError(null), 5000);
    }
  };

  const installPlugin = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const formData = new FormData();
      formData.append("zip_file", file);
      const resp = await fetch(`${API_BASE}/api/plugins/install`, {
        method: "POST",
        body: formData,
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw new Error(body.detail || body.error || `HTTP ${resp.status}`);
      }
      fetchPlugins();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setTimeout(() => setError(null), 5000);
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const openConfig = async (name: string) => {
    try {
      const resp = await fetch(`${API_BASE}/api/plugins/${name}/config`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setPluginConfig(data.schema);
      const vals: Record<string, unknown> = {};
      for (const item of data.schema) {
        vals[item.key] = item.value;
      }
      setConfigValues(vals);
      setConfiguringPlugin(name);
      setConfigMsg(null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setTimeout(() => setError(null), 5000);
    }
  };

  const saveConfig = async () => {
    if (!configuringPlugin) return;
    setConfigSaving(true);
    try {
      const resp = await fetch(`${API_BASE}/api/plugins/${configuringPlugin}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configValues),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
        throw new Error(body.detail || body.error || `HTTP ${resp.status}`);
      }
      setConfigMsg("配置已保存");
      setTimeout(() => {
        setConfiguringPlugin(null);
        setConfigMsg(null);
      }, 1500);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setConfigMsg(`保存失败: ${message}`);
    } finally {
      setConfigSaving(false);
    }
  };

  if (loading) {
    return <div className="plugin-mgmt-loading">加载中...</div>;
  }

  return (
    <div className="plugin-mgmt-container">
      {/* ---- Header ---- */}
      <div className="plugin-mgmt-header">
        <div className="plugin-mgmt-header-left">
          <h2 className="plugin-mgmt-title">插件</h2>
          <p className="plugin-mgmt-subtitle">安装和管理插件，扩展贾维斯的能力</p>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip"
          style={{ display: "none" }}
          onChange={installPlugin}
        />
        <button className="plugin-mgmt-upload-btn" onClick={() => fileInputRef.current?.click()}>
          + 安装插件
        </button>
      </div>

      {/* ---- Tabs ---- */}
      <div className="plugin-mgmt-tabs">
        <button className="plugin-mgmt-tab active">
          已安装 {plugins.length}
        </button>
      </div>

      {/* ---- Error banner ---- */}
      {error && (
        <div className="plugin-mgmt-error">
          <span>{error}</span>
          <button className="plugin-mgmt-error-close" onClick={() => setError(null)}>
            ✕
          </button>
        </div>
      )}

      {/* ---- Content ---- */}
      {plugins.length === 0 ? (
        <div className="plugin-mgmt-empty">
          <span className="plugin-mgmt-empty-icon">📦</span>
          <p>暂无已安装的插件</p>
        </div>
      ) : (
        <div className="plugin-mgmt-grid">
          {plugins.map((plugin) => {
            const icon = getPluginIcon(plugin.name);
            const hasConfig = plugin.config_schema && plugin.config_schema.length > 0;

            return (
              <div
                key={plugin.name}
                className={`plugin-mgmt-card${!plugin.enabled ? " disabled-card" : ""}`}
              >
                {/* Hover actions (top-right) */}
                <div className="plugin-mgmt-card-actions">
                  <button title="重载" onClick={() => reloadPlugin(plugin.name)}>
                    ↻
                  </button>
                  {hasConfig && (
                    <button title="配置" onClick={() => openConfig(plugin.name)}>
                      ⚙
                    </button>
                  )}
                  <button
                    className="danger"
                    title="卸载"
                    onClick={() => uninstallPlugin(plugin.name)}
                  >
                    🗑
                  </button>
                </div>

                {/* Icon */}
                <div className={`plugin-mgmt-card-icon ${icon.cls}`}>
                  {icon.emoji}
                </div>

                {/* Body */}
                <div className="plugin-mgmt-card-body">
                  <p className="plugin-mgmt-card-name">{plugin.name}</p>
                  <p className="plugin-mgmt-card-desc">{plugin.description}</p>
                  <p className="plugin-mgmt-card-meta">
                    <span>v{plugin.version}</span>
                    <span>{plugin.author}</span>
                    <span>{plugin.commands.length} 个命令</span>
                  </p>

                  {/* Footer (visible on hover) */}
                  <div className="plugin-mgmt-card-footer">
                    <button onClick={() => reloadPlugin(plugin.name)}>↻ 重载</button>
                    {hasConfig && (
                      <button onClick={() => openConfig(plugin.name)}>⚙ 配置</button>
                    )}
                    <button
                      className="danger"
                      onClick={() => uninstallPlugin(plugin.name)}
                    >
                      🗑 卸载
                    </button>
                  </div>
                </div>

                {/* Toggle */}
                <div className="plugin-mgmt-card-right">
                  <label className="plugin-mgmt-toggle">
                    <input
                      type="checkbox"
                      checked={plugin.enabled}
                      onChange={() => togglePlugin(plugin.name, !plugin.enabled)}
                    />
                    <span className="plugin-mgmt-toggle-track" />
                    <span className="plugin-mgmt-toggle-thumb" />
                  </label>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ---- Config Modal ---- */}
      {configuringPlugin && (
        <div
          className="plugin-mgmt-config-overlay"
          onClick={(e) => {
            if (e.target === e.currentTarget) setConfiguringPlugin(null);
          }}
        >
          <div className="plugin-mgmt-config-modal">
            <div className="plugin-mgmt-config-modal-header">
              <h3>配置: {configuringPlugin}</h3>
              <button
                className="plugin-mgmt-config-close-btn"
                onClick={() => setConfiguringPlugin(null)}
              >
                ✕
              </button>
            </div>

            {configMsg && (
              <div
                className={`plugin-mgmt-config-msg ${
                  configMsg.startsWith("保存失败") ? "error" : "success"
                }`}
              >
                {configMsg}
              </div>
            )}

            {pluginConfig.map((item) => (
              <div key={item.key} className="plugin-mgmt-config-field">
                <label>{item.label || item.key}</label>
                {item.type === "select" && item.options ? (
                  <select
                    value={String(configValues[item.key] ?? item.default ?? "")}
                    onChange={(e) =>
                      setConfigValues((prev) => ({ ...prev, [item.key]: e.target.value }))
                    }
                  >
                    {item.options.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                ) : item.type === "number" ? (
                  <input
                    type="number"
                    value={Number(configValues[item.key] ?? item.default ?? 0)}
                    onChange={(e) =>
                      setConfigValues((prev) => ({
                        ...prev,
                        [item.key]: Number(e.target.value),
                      }))
                    }
                  />
                ) : (
                  <input
                    type="text"
                    value={String(configValues[item.key] ?? item.default ?? "")}
                    onChange={(e) =>
                      setConfigValues((prev) => ({ ...prev, [item.key]: e.target.value }))
                    }
                  />
                )}
                {item.description && (
                  <span className="plugin-mgmt-config-desc">{item.description}</span>
                )}
              </div>
            ))}

            <div className="plugin-mgmt-config-actions">
              <button
                className="cancel-btn"
                onClick={() => setConfiguringPlugin(null)}
              >
                取消
              </button>
              <button
                className="save-btn"
                onClick={saveConfig}
                disabled={configSaving}
              >
                {configSaving ? "保存中..." : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
