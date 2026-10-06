import { useState } from "react";
import type { ToneOption } from "../../lib/types";
import { updatePersona, checkHealth } from "../../lib/api";
import type { PersonaTabProps } from "./types";

const TONE_OPTIONS: { value: ToneOption; label: string }[] = [
  { value: "professional", label: "专业" },
  { value: "casual", label: "随和" },
  { value: "humorous", label: "幽默" },
  { value: "formal", label: "正式" },
];

export default function PersonaTab({
  config,
  connectionStatus,
  activeModelName,
  onConfigUpdate,
  notify,
}: PersonaTabProps) {
  const [localName, setLocalName] = useState(config?.persona_config.name || "JARVIS");
  const [localTone, setLocalTone] = useState<string>(
    config?.persona_config.tone || "professional"
  );
  const [localOwnerTitle, setLocalOwnerTitle] = useState(
    config?.persona_config.owner_title || "Boss"
  );
  const [localSelfTitle, setLocalSelfTitle] = useState(
    config?.persona_config.self_title || "JARVIS"
  );
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  const handleSavePersona = async () => {
    setSaving(true);
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
      notify("人设设置已保存");
    } catch (e) {
      notify(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSaving(false);
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
    }
  };

  return (
    <>
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
            {TONE_OPTIONS.map((opt) => (
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

      {/* ====== 关于 ====== */}
      <div className="settings-section">
        <div className="settings-section-title">关于</div>
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
            <span className="value">
              {activeModelName || config?.model_config.model || "未设置"}
            </span>
          </div>
        </div>
        <div className="setting-field" style={{ marginTop: "12px" }}>
          <button className="setting-btn" onClick={handleTestConnection} disabled={testing}>
            {testing ? "测试中..." : "测试连接"}
          </button>
          {testResult && (
            <div
              className="setting-hint"
              style={{
                marginTop: "8px",
                color: testResult.startsWith("连接成功") ? "var(--success)" : "var(--error)",
              }}
            >
              {testResult}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
