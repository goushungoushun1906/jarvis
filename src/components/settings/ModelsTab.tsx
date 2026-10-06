import { useState } from "react";
import {
  listModels,
  switchModel,
  addModel,
  updateModelProfile,
  deleteModel,
  validateModelName,
} from "../../lib/api";
import type { ModelNameCheck } from "../../lib/api";
import type { ModelItem, TabProps } from "./types";
import ModelNameWarning from "./ModelNameWarning";
import EditModelDialog from "./EditModelDialog";

/** 内置但冗余/不可用的档位，不显示在设置里。 */
export const HIDDEN_BUILTIN_IDS = new Set([
  "agnes-flash",
  "gpt4o-mini",
  "gpt4o",
  "deepseek",
  "doubao",
  "claude",
]);

/** 不可删除的默认档位（Agnes 2.0 + 本地 Ollama）。 */
export const DEFAULT_BUILTIN_IDS = new Set(["agnes-15", "deepseek-r1-ollama"]);

export interface ModelsTabProps extends TabProps {
  models: ModelItem[];
  onModelsChange: (models: ModelItem[]) => void;
}

export default function ModelsTab({
  models,
  onModelsChange,
  onConfigUpdate,
  notify,
}: ModelsTabProps) {
  const [switchingId, setSwitchingId] = useState<string | null>(null);

  // 新增模型表单
  const [customName, setCustomName] = useState("");
  const [customModel, setCustomModel] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customKey, setCustomKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [addingModel, setAddingModel] = useState(false);

  // 编辑模型弹窗
  const [editingModel, setEditingModel] = useState<ModelItem | null>(null);

  // 模型名校验（针对真实网关）
  const [customCheck, setCustomCheck] = useState<ModelNameCheck | null>(null);
  const [customChecking, setCustomChecking] = useState(false);

  const refreshModels = async () => {
    try {
      onModelsChange(await listModels());
    } catch {
      // 拉取失败保持静默
    }
  };

  const visibleModels = models.filter((m) => !HIDDEN_BUILTIN_IDS.has(m.id));
  const activeModel = models.find((m) => m.is_active);

  // 按 provider 分组展示
  const groupedModels = (() => {
    const groups: Record<string, ModelItem[]> = {};
    for (const m of visibleModels) {
      const provider = m.provider || "自定义";
      if (!groups[provider]) groups[provider] = [];
      groups[provider].push(m);
    }
    return Object.entries(groups).map(([provider, items]) => ({ provider, models: items }));
  })();

  const handleSwitchModel = async (profileId: string) => {
    setSwitchingId(profileId);
    try {
      await switchModel(profileId);
      await refreshModels();
      // 顺带刷新完整配置，让顶部状态同步
      const resp = await fetch("http://127.0.0.1:18200/api/config");
      if (resp.ok) {
        onConfigUpdate(await resp.json());
      }
      notify("模型已切换");
    } catch (e) {
      notify(`切换失败: ${e instanceof Error ? e.message : "未知错误"}`);
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
      await refreshModels();
      setCustomName("");
      setCustomModel("");
      setCustomUrl("");
      setCustomKey("");
      setCustomCheck(null);
      notify("自定义模型已添加");
    } catch (e) {
      notify(`添加失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setAddingModel(false);
    }
  };

  const handleDeleteModel = async (profileId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteModel(profileId);
      await refreshModels();
    } catch {
      // silent
    }
  };

  const runModelCheck = async () => {
    if (!customModel || !customUrl) return;
    setCustomChecking(true);
    try {
      setCustomCheck(
        await validateModelName({
          model: customModel,
          base_url: customUrl,
          api_key: customKey,
        })
      );
    } catch {
      setCustomCheck(null);
    } finally {
      setCustomChecking(false);
    }
  };

  return (
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
                  <div className="model-card-actions">
                    <span
                      className="model-card-action edit"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingModel(m);
                      }}
                      title="编辑此模型"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                      </svg>
                    </span>
                    <span
                      className="model-card-action delete"
                      onClick={(e) => handleDeleteModel(m.id, e)}
                      title="删除此模型"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="3 6 5 6 21 6" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </span>
                  </div>
                )}
                <div className="model-card-name">{m.name}</div>
                <div className="model-card-id">{m.model}</div>
                {m.is_active && <div className="model-card-active-badge">当前</div>}
              </button>
            ))}
          </div>
        </div>
      ))}

      {/* 自定义模型 */}
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
          <div className="model-id-row">
            <input
              className="setting-input"
              type="text"
              value={customModel}
              onChange={(e) => {
                setCustomModel(e.target.value);
                setCustomCheck(null);
              }}
              placeholder="例如: my-model-v1"
            />
            <button
              type="button"
              className="setting-btn model-id-check-btn"
              disabled={customChecking || !customModel || !customUrl}
              onClick={runModelCheck}
            >
              {customChecking ? "校验中..." : "校验"}
            </button>
          </div>
          <div className="setting-hint">
            填写网关真实识别的 ID；校验会列出该网关可用的模型名。
          </div>
          {customCheck && customCheck.valid === false && (
            <ModelNameWarning
              check={customCheck}
              onApply={(name) => {
                setCustomModel(name);
                setCustomCheck(null);
              }}
            />
          )}
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

      {editingModel && (
        <EditModelDialog
          model={editingModel}
          onClose={() => setEditingModel(null)}
          onSaved={refreshModels}
          notify={notify}
        />
      )}
    </div>
  );
}
