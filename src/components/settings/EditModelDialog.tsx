import { useState } from "react";
import { updateModelProfile, validateModelName } from "../../lib/api";
import type { ModelNameCheck } from "../../lib/api";
import type { ModelItem, Notify } from "./types";
import ModelNameWarning from "./ModelNameWarning";

export interface EditModelDialogProps {
  model: ModelItem;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
  notify: Notify;
}

/** 编辑已有模型档位的弹窗。 */
export default function EditModelDialog({
  model,
  onClose,
  onSaved,
  notify,
}: EditModelDialogProps) {
  const [editName, setEditName] = useState(model.name);
  const [editModel, setEditModel] = useState(model.model);
  const [editUrl, setEditUrl] = useState(model.base_url || "");
  const [editKey, setEditKey] = useState(model.api_key || "");
  const [editShowKey, setEditShowKey] = useState(false);
  const [savingModel, setSavingModel] = useState(false);
  const [check, setCheck] = useState<ModelNameCheck | null>(null);
  const [checking, setChecking] = useState(false);

  const runCheck = async () => {
    if (!editModel || !editUrl) return;
    setChecking(true);
    try {
      setCheck(
        await validateModelName({
          model: editModel,
          base_url: editUrl,
          api_key: editKey,
          profile_id: model.id,
        })
      );
    } catch {
      setCheck(null);
    } finally {
      setChecking(false);
    }
  };

  const handleSave = async () => {
    if (!editName || !editModel || !editUrl) return;
    setSavingModel(true);
    try {
      await updateModelProfile(model.id, {
        name: editName,
        model: editModel,
        base_url: editUrl,
        api_key: editKey,
      });
      await onSaved();
      notify("模型已更新");
      onClose();
    } catch (e) {
      notify(`更新失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSavingModel(false);
    }
  };

  return (
    <div className="model-edit-overlay" onClick={onClose}>
      <div className="model-edit-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="model-edit-header">
          <h3>编辑模型</h3>
          <button className="model-edit-close" onClick={onClose} title="关闭">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        <div className="model-edit-body">
          <div className="setting-field">
            <label className="setting-label">显示名称</label>
            <input
              className="setting-input"
              type="text"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              placeholder="例如: My Custom Model"
            />
          </div>

          <div className="setting-field">
            <label className="setting-label">模型 ID</label>
            <div className="model-id-row">
              <input
                className="setting-input"
                type="text"
                value={editModel}
                onChange={(e) => {
                  setEditModel(e.target.value);
                  setCheck(null);
                }}
                placeholder="例如: my-model-v1"
              />
              <button
                type="button"
                className="setting-btn model-id-check-btn"
                disabled={checking || !editModel || !editUrl}
                onClick={runCheck}
              >
                {checking ? "校验中..." : "校验"}
              </button>
            </div>
            <div className="setting-hint">
              填写网关真实识别的 ID；校验会列出该网关可用的模型名。
            </div>
            {check && check.valid === false && (
              <ModelNameWarning
                check={check}
                onApply={(name) => {
                  setEditModel(name);
                  setCheck(null);
                }}
              />
            )}
          </div>

          <div className="setting-field">
            <label className="setting-label">Base URL</label>
            <input
              className="setting-input"
              type="text"
              value={editUrl}
              onChange={(e) => setEditUrl(e.target.value)}
              placeholder="https://api.example.com/v1"
            />
          </div>

          <div className="setting-field">
            <label className="setting-label">API Key (可选)</label>
            <div style={{ position: "relative" }}>
              <input
                className="setting-input"
                type={editShowKey ? "text" : "password"}
                value={editKey}
                onChange={(e) => setEditKey(e.target.value)}
                placeholder="留空则使用全局密钥"
                style={{ paddingRight: "36px" }}
              />
              <button
                type="button"
                onClick={() => setEditShowKey(!editShowKey)}
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
                title={editShowKey ? "隐藏" : "显示"}
              >
                {editShowKey ? "隐藏" : "显示"}
              </button>
            </div>
          </div>
        </div>

        <div className="model-edit-actions">
          <button className="setting-btn" onClick={onClose}>
            取消
          </button>
          <button
            className="setting-btn primary"
            onClick={handleSave}
            disabled={savingModel || !editName || !editModel || !editUrl}
          >
            {savingModel ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
