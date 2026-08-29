import { useState, useEffect, useMemo, useCallback } from "react";
import type { AppConfig, ToneOption, ConnectionStatus } from "../lib/types";
import { updatePersona, checkHealth, listModels, switchModel, addModel, deleteModel } from "../lib/api";
import PluginManagement from "./PluginManagement";
import CostDashboard from "./CostDashboard";
import { t, getLocale, setLocale, onLocaleChange, type Locale } from "../lib/i18n";
import "./SettingsPanel.css";

interface ModelItem {
  id: string;
  name: string;
  model: string;
  base_url: string;
  api_key?: string;
  provider: string;
  is_active: boolean;
}

interface SettingsPanelProps {
  config: AppConfig | null;
  connectionStatus: ConnectionStatus;
  onConfigUpdate: (config: AppConfig) => void;
  onClose: () => void;
}

// Built-in models that should NOT appear in the settings list (redundant / unusable)
const HIDDEN_BUILTIN_IDS = new Set([
  "agnes-flash",
  "gpt4o-mini",
  "gpt4o",
  "deepseek",
  "doubao",
  "claude",
]);

// Default built-in models that cannot be deleted (Agnes 2.0 + local Ollama)
const DEFAULT_BUILTIN_IDS = new Set(["agnes-15", "deepseek-r1-ollama"]);

export default function SettingsPanel({
  config,
  connectionStatus,
  onConfigUpdate,
  onClose,
}: SettingsPanelProps) {
  const [activeTab, setActiveTab] = useState<"models" | "persona" | "plugins" | "privacy" | "costs">("models");
  const [locale, setLocaleState] = useState<Locale>(getLocale());
  const [models, setModels] = useState<ModelItem[]>([]);
  const [localName, setLocalName] = useState(config?.persona_config.name || "JARVIS");
  const [localTone, setLocalTone] = useState<string>(config?.persona_config.tone || "professional");
  const [localOwnerTitle, setLocalOwnerTitle] = useState(config?.persona_config.owner_title || "Boss");
  const [localSelfTitle, setLocalSelfTitle] = useState(config?.persona_config.self_title || "JARVIS");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [switchingId, setSwitchingId] = useState<string | null>(null);

  // Custom model form
  const [customName, setCustomName] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customKey, setCustomKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [addingModel, setAddingModel] = useState(false);

  const fetchModels = useCallback(async () => {
    try {
      const data = await listModels();
      setModels(data);
    } catch {
      // silent
    }
  }, []);

  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  useEffect(() => {
    const unsub = onLocaleChange(setLocaleState);
    return unsub;
  }, []);

  // Show default models (Agnes 2.0, local Ollama) and any user-added custom models
  const visibleModels = useMemo(() => {
    return models.filter((m) => !HIDDEN_BUILTIN_IDS.has(m.id));
  }, [models]);

  // Group models by provider
  const groupedModels = useMemo(() => {
    const groups: Record<string, ModelItem[]> = {};
    for (const m of visibleModels) {
      const provider = m.provider || "自定义";
      if (!groups[provider]) groups[provider] = [];
      groups[provider].push(m);
    }
    return Object.entries(groups).map(([provider, items]) => ({ provider, models: items }));
  }, [visibleModels]);

  // Privacy & Data state
  const [privacyStats, setPrivacyStats] = useState<{ conversation_count: number; message_count: number; db_size_mb: number } | null>(null);
  const [privacyLoading, setPrivacyLoading] = useState(false);
  const [privacyMsg, setPrivacyMsg] = useState<string | null>(null);
  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);

  const fetchPrivacyStats = useCallback(async () => {
    setPrivacyLoading(true);
    try {
      const resp = await fetch("http://127.0.0.1:18200/api/privacy/stats");
      if (resp.ok) {
        const data = await resp.json();
        setPrivacyStats(data);
      } else {
        setPrivacyMsg("获取数据统计失败");
      }
    } catch {
      setPrivacyMsg("获取数据统计失败: 无法连接服务器");
    } finally {
      setPrivacyLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "privacy" && !privacyStats) {
      fetchPrivacyStats();
    }
  }, [activeTab, privacyStats, fetchPrivacyStats]);

  const handleExportData = useCallback(async () => {
    setPrivacyLoading(true);
    setPrivacyMsg(null);
    try {
      const resp = await fetch("http://127.0.0.1:18200/api/privacy/export");
      if (!resp.ok) {
        setPrivacyMsg("导出失败: 服务器返回错误");
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
      setPrivacyMsg("数据导出成功");
    } catch {
      setPrivacyMsg("导出失败: 无法连接服务器");
    } finally {
      setPrivacyLoading(false);
      setTimeout(() => setPrivacyMsg(null), 3000);
    }
  }, []);

  const handlePurgeData = useCallback(async () => {
    setPrivacyLoading(true);
    setPrivacyMsg(null);
    setShowPurgeConfirm(false);
    try {
      const resp = await fetch("http://127.0.0.1:18200/api/privacy/purge", { method: "DELETE" });
      if (!resp.ok) {
        setPrivacyMsg("清除数据失败: 服务器返回错误");
        return;
      }
      setPrivacyStats(null);
      setPrivacyMsg("所有数据已清除");
    } catch {
      setPrivacyMsg("清除数据失败: 无法连接服务器");
    } finally {
      setPrivacyLoading(false);
      setTimeout(() => setPrivacyMsg(null), 3000);
    }
  }, []);

  const handleSwitchModel = async (profileId: string) => {
    setSwitchingId(profileId);
    try {
      await switchModel(profileId);
      await fetchModels();
      // Also refresh full config
      const resp = await fetch("http://127.0.0.1:18200/api/config");
      if (resp.ok) {
        const newConfig = await resp.json();
        onConfigUpdate(newConfig);
      }
      setSaveMsg("模型已切换");
      setTimeout(() => setSaveMsg(null), 2000);
    } catch (e) {
      setSaveMsg(`切换失败: ${e instanceof Error ? e.message : "未知错误"}`);
      setTimeout(() => setSaveMsg(null), 3000);
    } finally {
      setSwitchingId(null);
    }
  };

  const handleAddModel = async () => {
    if (!customName || !customModel || !customUrl) return;
    setAddingModel(true);
    try {
      const slug = customName
        .toLowerCase()
        .replace(/[^a-z0-9\u4e00-\u9fff]/g, "-")
        .replace(/-+/g, "-")
        .slice(0, 32);
      await addModel({
        id: slug,
        name: customName,
        model: customModel,
        base_url: customUrl,
        api_key: customKey,
        provider: "自定义",
        tier: "mid",
      });
      await fetchModels();
      setCustomName("");
      setCustomModel("");
      setCustomUrl("");
      setCustomKey("");
      setSaveMsg("自定义模型已添加");
      setTimeout(() => setSaveMsg(null), 2000);
    } catch (e) {
      setSaveMsg(`添加失败: ${e instanceof Error ? e.message : "未知错误"}`);
      setTimeout(() => setSaveMsg(null), 3000);
    } finally {
      setAddingModel(false);
    }
  };

  const handleDeleteModel = async (profileId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteModel(profileId);
      await fetchModels();
    } catch {
      // silent
    }
  };

  const handleSavePersona = async () => {
    setSaving(true);
    setSaveMsg(null);
    try {
      await updatePersona({
        name: localName,
        tone: localTone,
        owner_title: localOwnerTitle,
        self_title: localSelfTitle,
      });
      onConfigUpdate({
        ...config!,
        persona_config: {
          name: localName,
          tone: localTone,
          owner_title: localOwnerTitle,
          self_title: localSelfTitle,
        },
      });
      setSaveMsg("人设设置已保存");
    } catch (e) {
      setSaveMsg(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 3000);
    }
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const resp = await checkHealth();
      setTestResult(`连接成功 (v${resp.version || "未知"})`);
    } catch {
      setTestResult("连接失败");
    } finally {
      setTesting(false);
      setTimeout(() => setTestResult(null), 5000);
    }
  };

  const activeModel = models.find((m) => m.is_active);

  const toneOptions: { value: ToneOption; label: string }[] = [
    { value: "professional", label: "专业" },
    { value: "casual", label: "随和" },
    { value: "humorous", label: "幽默" },
    { value: "formal", label: "正式" },
  ];

  return (
    <div className="settings-panel">
      {/* Header */}
      <div className="settings-header">
        <span className="settings-title">{t("settings.title")}</span>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginLeft: "auto" }}>
          <select
            value={locale}
            onChange={(e) => setLocale(e.target.value as Locale)}
            style={{
              padding: "4px 8px",
              borderRadius: "6px",
              border: "1px solid var(--border)",
              background: "var(--bg-card)",
              color: "var(--text)",
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            <option value="zh-CN">中文</option>
            <option value="en">English</option>
          </select>
          <button className="header-btn" onClick={onClose} title={t("settings.closeSettings")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="settings-body">
        {/* Tab navigation */}
        <div className="settings-tabs">
          <button
            className={`tab-btn ${activeTab === "models" ? "active" : ""}`}
            onClick={() => setActiveTab("models")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
            {t("settings.tab.models")}
          </button>
          <button
            className={`tab-btn ${activeTab === "persona" ? "active" : ""}`}
            onClick={() => setActiveTab("persona")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            {t("settings.tab.persona")}
          </button>
          <button
            className={`tab-btn ${activeTab === "plugins" ? "active" : ""}`}
            onClick={() => setActiveTab("plugins")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
            {t("settings.tab.plugins")}
          </button>
          <button
            className={`tab-btn ${activeTab === "privacy" ? "active" : ""}`}
            onClick={() => setActiveTab("privacy")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>
            {t("settings.tab.privacy")}
          </button>
          <button
            className={`tab-btn ${activeTab === "costs" ? "active" : ""}`}
            onClick={() => setActiveTab("costs")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>
            成本
          </button>
        </div>

        {/* Status message */}
        {(saveMsg || testResult) && (
          <div
            style={{
              padding: "8px 12px",
              borderRadius: "8px",
              fontSize: "12px",
              marginBottom: "16px",
              background:
                testResult?.startsWith("连接成功") || saveMsg?.includes("成功")
                  ? "rgba(16,185,129,0.1)"
                  : "rgba(239,68,68,0.1)",
              color:
                testResult?.startsWith("连接成功") || saveMsg?.includes("成功")
                  ? "var(--success)"
                  : "var(--error)",
              border: `1px solid ${
                testResult?.startsWith("连接成功") || saveMsg?.includes("成功")
                  ? "rgba(16,185,129,0.2)"
                  : "rgba(239,68,68,0.2)"
              }`,
            }}
          >
            {saveMsg || testResult}
          </div>
        )}

        {/* ====== Models Tab ====== */}
        {activeTab === "models" && (
          <>
            {/* ====== Model Switcher Section ====== */}
            <div className="settings-section">
              <div className="settings-section-title">模型切换</div>

          {activeModel && (
            <div
              style={{
                padding: "8px 12px",
                borderRadius: "8px",
                marginBottom: "12px",
                background: "rgba(59, 130, 246, 0.06)",
                border: "1px solid rgba(59, 130, 246, 0.15)",
                fontSize: "12px",
                color: "var(--text-secondary)",
              }}
            >
              当前模型:{" "}
              <span style={{ color: "var(--accent-blue-light)", fontWeight: 600 }}>
                {activeModel.name}
              </span>
              <span
                style={{
                  color: "var(--text-muted)",
                  marginLeft: "8px",
                  fontFamily: "monospace",
                  fontSize: "11px",
                }}
              >
                {activeModel.model}
              </span>
            </div>
          )}

          {/* Provider groups */}
          {groupedModels.map((group) => (
            <div key={group.provider}>
              <div className="model-provider-label">{group.provider}</div>
              <div className="model-grid">
                {group.models.map((m) => (
                  <button
                    key={m.id}
                    className={`model-card ${m.is_active ? "model-card-active" : ""} ${
                      switchingId === m.id ? "model-card-flash" : ""
                    }`}
                    onClick={() => !m.is_active && handleSwitchModel(m.id)}
                    disabled={switchingId === m.id}
                    title={m.is_active ? "当前使用的模型" : `点击切换到 ${m.name}`}
                  >
                    {!DEFAULT_BUILTIN_IDS.has(m.id) && (
                      <span
                        className="model-card-delete"
                        onClick={(e) => handleDeleteModel(m.id, e)}
                        title="删除此模型"
                      >
                        x
                      </span>
                    )}
                    <div className="model-card-name">{m.name}</div>
                    <div className="model-card-id">{m.model}</div>
                    {m.is_active && <div className="model-card-active-badge">当前</div>}
                  </button>
                ))}
              </div>
            </div>
          ))}

          {/* Custom model add form */}
          <div className="custom-model-section">
            <div className="settings-subtitle">自定义模型</div>

            <div className="setting-field">
              <label className="setting-label">显示名称</label>
              <input
                className="setting-input"
                type="text"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                placeholder="例如: My Custom Model"
              />
            </div>

            <div className="setting-field">
              <label className="setting-label">模型 ID</label>
              <input
                className="setting-input"
                type="text"
                value={customModel}
                onChange={(e) => setCustomModel(e.target.value)}
                placeholder="例如: my-model-v1"
              />
            </div>

            <div className="setting-field">
              <label className="setting-label">Base URL</label>
              <input
                className="setting-input"
                type="text"
                value={customUrl}
                onChange={(e) => setCustomUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
              />
            </div>

            <div className="setting-field">
              <label className="setting-label">API Key (可选)</label>
              <div style={{ position: "relative" }}>
                <input
                  className="setting-input"
                  type={showKey ? "text" : "password"}
                  value={customKey}
                  onChange={(e) => setCustomKey(e.target.value)}
                  placeholder="留空则使用全局密钥"
                  style={{ paddingRight: "36px" }}
                />
                <button
                  type="button"
                  onClick={() => setShowKey(!showKey)}
                  style={{
                    position: "absolute",
                    right: "8px",
                    top: "50%",
                    transform: "translateY(-50%)",
                    background: "none",
                    border: "none",
                    color: "var(--text-muted)",
                    cursor: "pointer",
                    fontSize: "12px",
                    padding: "2px",
                  }}
                  title={showKey ? "隐藏" : "显示"}
                >
                  {showKey ? "隐藏" : "显示"}
                </button>
              </div>
            </div>

            <button
              className="setting-btn"
              onClick={handleAddModel}
              disabled={addingModel || !customName || !customModel || !customUrl}
            >
              {addingModel ? "添加中..." : "添加模型"}
            </button>
          </div>
        </div>
          </>
        )}

        {/* ====== Persona Tab ====== */}
        {activeTab === "persona" && (
          <>
            {/* ====== Persona Settings ====== */}
            <div className="settings-section">
              <div className="settings-section-title">人设设置</div>

          <div className="setting-field">
            <label className="setting-label">AI 名称</label>
            <input
              className="setting-input"
              type="text"
              value={localName}
              onChange={(e) => setLocalName(e.target.value)}
              placeholder="JARVIS"
            />
          </div>

          <div className="setting-field">
            <label className="setting-label">语气风格</label>
            <select
              className="setting-select"
              value={localTone}
              onChange={(e) => setLocalTone(e.target.value)}
            >
              {toneOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div className="setting-field">
            <label className="setting-label">如何称呼用户</label>
            <input
              className="setting-input"
              type="text"
              value={localOwnerTitle}
              onChange={(e) => setLocalOwnerTitle(e.target.value)}
              placeholder="Boss"
            />
          </div>

          <div className="setting-field">
            <label className="setting-label">AI 自称</label>
            <input
              className="setting-input"
              type="text"
              value={localSelfTitle}
              onChange={(e) => setLocalSelfTitle(e.target.value)}
              placeholder="JARVIS"
            />
          </div>

          <button className="setting-btn" onClick={handleSavePersona} disabled={saving}>
            {saving ? "保存中..." : "保存人设设置"}
          </button>
        </div>

        {/* ====== About ====== */}
        <div className="settings-section">
          <div className="settings-section-title">{t("settings.about.section")}</div>
          <div className="about-info">
            <div className="about-row">
              <span className="label">版本</span>
              <span className="value">0.1.0</span>
            </div>
            <div className="about-row">
              <span className="label">连接状态</span>
              <span
                className="value"
                style={{
                  color:
                    connectionStatus === "online"
                      ? "var(--success)"
                      : connectionStatus === "offline"
                        ? "var(--error)"
                        : "var(--warning)",
                }}
              >
                {connectionStatus === "online"
                  ? "已连接"
                  : connectionStatus === "offline"
                    ? "已断开"
                    : "检测中..."}
              </span>
            </div>
            <div className="about-row">
              <span className="label">当前模型</span>
              <span className="value">{activeModel?.name || config?.model_config.model || "未设置"}</span>
            </div>
          </div>
          <div className="setting-field" style={{ marginTop: "12px" }}>
            <button className="setting-btn" onClick={handleTestConnection} disabled={testing}>
              {testing ? "测试中..." : "测试连接"}
            </button>
          </div>
        </div>
          </>
        )}

        {/* ====== Plugins Tab ====== */}
        {activeTab === "plugins" && (
          <div className="settings-section">
            <PluginManagement connectionStatus={connectionStatus} onConfigUpdate={onConfigUpdate} />
          </div>
        )}

        {/* ====== Costs Tab ====== */}
        {activeTab === "costs" && (
          <div className="settings-section" style={{ padding: 0, background: "transparent", border: "none" }}>
            <CostDashboard />
          </div>
        )}

        {/* ====== Privacy & Data Tab ====== */}
        {activeTab === "privacy" && (
          <div className="settings-section">
            <div className="settings-section-title">隐私与数据</div>

            {/* Privacy status message */}
            {privacyMsg && (
              <div
                style={{
                  padding: "8px 12px",
                  borderRadius: "8px",
                  fontSize: "12px",
                  marginBottom: "16px",
                  background: privacyMsg.includes("成功")
                    ? "rgba(16,185,129,0.1)"
                    : "rgba(239,68,68,0.1)",
                  color: privacyMsg.includes("成功") ? "var(--success)" : "var(--error)",
                  border: `1px solid ${
                    privacyMsg.includes("成功")
                      ? "rgba(16,185,129,0.2)"
                      : "rgba(239,68,68,0.2)"
                  }`,
                }}
              >
                {privacyMsg}
              </div>
            )}

            {/* Data statistics */}
            <div
              style={{
                padding: "16px",
                borderRadius: "8px",
                marginBottom: "16px",
                background: "var(--bg-secondary, rgba(255,255,255,0.03))",
                border: "1px solid var(--border, rgba(255,255,255,0.06))",
              }}
            >
              <div style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "12px" }}>数据统计</div>
              {privacyLoading && !privacyStats ? (
                <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>加载中...</div>
              ) : privacyStats ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                  <div className="about-row">
                    <span className="label">对话数量</span>
                    <span className="value">{privacyStats.conversation_count}</span>
                  </div>
                  <div className="about-row">
                    <span className="label">消息总数</span>
                    <span className="value">{privacyStats.message_count}</span>
                  </div>
                  <div className="about-row">
                    <span className="label">数据库大小</span>
                    <span className="value">{privacyStats.db_size_mb.toFixed(2)} MB</span>
                  </div>
                </div>
              ) : (
                <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>无数据</div>
              )}
            </div>

            {/* Action buttons */}
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <button
                className="setting-btn"
                onClick={handleExportData}
                disabled={privacyLoading}
                style={{ width: "100%" }}
              >
                {privacyLoading ? "处理中..." : "导出数据"}
              </button>

              {!showPurgeConfirm ? (
                <button
                  className="setting-btn"
                  onClick={() => setShowPurgeConfirm(true)}
                  disabled={privacyLoading}
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
                      onClick={handlePurgeData}
                      disabled={privacyLoading}
                      style={{
                        flex: 1,
                        background: "rgba(239,68,68,0.15)",
                        color: "var(--error)",
                        border: "1px solid rgba(239,68,68,0.3)",
                      }}
                    >
                      {privacyLoading ? "清除中..." : "确认清除"}
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
        )}
      </div>
    </div>
  );
}
