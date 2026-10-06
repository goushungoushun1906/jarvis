import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { AppConfig, ConnectionStatus } from "../lib/types";
import { listModels } from "../lib/api";
import PluginManagement from "./PluginManagement";
import CostDashboard from "./CostDashboard";
import ModelsTab from "./settings/ModelsTab";
import PersonaTab from "./settings/PersonaTab";
import PrivacyTab from "./settings/PrivacyTab";
import type { ModelItem } from "./settings/types";
import "./SettingsPanel.css";

type TabId = "models" | "persona" | "plugins" | "privacy" | "costs";

interface SettingsPanelProps {
  config: AppConfig | null;
  connectionStatus: ConnectionStatus;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onConfigUpdate: (config: AppConfig) => void;
  onClose: () => void;
}

/** 顶部状态条：成功绿 / 失败红。判定沿用"文案里带成功/已"的朴素规则。 */
function isGoodNews(msg: string): boolean {
  return msg.includes("成功") || msg.includes("已");
}

export default function SettingsPanel({
  config,
  connectionStatus,
  theme,
  onToggleTheme,
  onConfigUpdate,
  onClose,
}: SettingsPanelProps) {
  const [activeTab, setActiveTab] = useState<TabId>("models");
  const [models, setModels] = useState<ModelItem[]>([]);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  useEffect(() => {
    listModels()
      .then(setModels)
      .catch(() => {
        // 拉取失败保持静默
      });
  }, []);

  const notify = useCallback((msg: string) => {
    setStatusMsg(msg);
    setTimeout(() => setStatusMsg(null), 3000);
  }, []);

  const activeModel = models.find((m) => m.is_active);

  const tabs: { id: TabId; label: string; icon: ReactNode }[] = [
    {
      id: "models",
      label: "模型",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
      ),
    },
    {
      id: "persona",
      label: "人设",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      ),
    },
    {
      id: "plugins",
      label: "插件",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>
      ),
    },
    {
      id: "privacy",
      label: "隐私",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>
      ),
    },
    {
      id: "costs",
      label: "成本",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>
      ),
    },
  ];

  return (
    <div className="settings-panel">
      {/* Header */}
      <div className="settings-header">
        <span className="settings-title">设置</span>
        <div className="settings-header-actions">
          <button
            className="theme-toggle-btn"
            onClick={onToggleTheme}
            title={theme === "light" ? "切换至深色主题" : "切换至浅色主题"}
            aria-label={theme === "light" ? "切换至深色主题" : "切换至浅色主题"}
          >
            {theme === "light" ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="5" />
                <line x1="12" y1="1" x2="12" y2="3" />
                <line x1="12" y1="21" x2="12" y2="23" />
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                <line x1="1" y1="12" x2="3" y2="12" />
                <line x1="21" y1="12" x2="23" y2="12" />
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
              </svg>
            )}
          </button>
          <button className="header-btn" onClick={onClose} title="关闭设置">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="settings-body">
        <div className="settings-tabs">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`tab-btn ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>

        {statusMsg && (
          <div
            style={{
              padding: "8px 12px",
              borderRadius: "8px",
              fontSize: "12px",
              marginBottom: "16px",
              background: isGoodNews(statusMsg)
                ? "rgba(16,185,129,0.1)"
                : "rgba(239,68,68,0.1)",
              color: isGoodNews(statusMsg) ? "var(--success)" : "var(--error)",
              border: `1px solid ${
                isGoodNews(statusMsg)
                  ? "rgba(16,185,129,0.2)"
                  : "rgba(239,68,68,0.2)"
              }`,
            }}
          >
            {statusMsg}
          </div>
        )}

        {activeTab === "models" && (
          <ModelsTab
            models={models}
            onModelsChange={setModels}
            onConfigUpdate={onConfigUpdate}
            notify={notify}
          />
        )}

        {activeTab === "persona" && (
          <PersonaTab
            config={config}
            connectionStatus={connectionStatus}
            activeModelName={activeModel?.name ?? null}
            onConfigUpdate={onConfigUpdate}
            notify={notify}
          />
        )}

        {activeTab === "plugins" && (
          <div className="settings-section">
            <PluginManagement
              connectionStatus={connectionStatus}
              onConfigUpdate={onConfigUpdate}
            />
          </div>
        )}

        {activeTab === "costs" && (
          <div
            className="settings-section"
            style={{ padding: 0, background: "transparent", border: "none" }}
          >
            <CostDashboard />
          </div>
        )}

        {/* PrivacyTab 常驻挂载会重复拉统计，所以只在激活时渲染 */}
        {activeTab === "privacy" && <PrivacyTab active />}
      </div>
    </div>
  );
}
