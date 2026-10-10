import { useCallback, useEffect, useState } from "react";
import {
  getMcpConfig,
  putMcpConfig,
  deleteMcpServer,
  listMcpTools,
  reloadMcpServers,
  type McpServerEntry,
  type McpServerStatus,
  type McpTool,
} from "../../lib/api";
import type { TabProps } from "./types";

type Draft = McpServerEntry & { name: string; originalName?: string };

const EMPTY_DRAFT: Draft = {
  name: "",
  transport: "stdio",
  command: "",
  args: [],
  url: "",
  env: undefined,
  enabled: true,
};

/** args / env 用文本域编辑：一行一项，比数组输入框更不容易填错。 */
function argsToText(args?: string[]): string {
  return (args ?? []).join("\n");
}

function textToArgs(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

function envToText(env?: Record<string, string>): string {
  if (!env) return "";
  return Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

function textToEnv(text: string): Record<string, string> | undefined {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const idx = trimmed.indexOf("=");
    if (idx <= 0) continue;
    out[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return Object.keys(out).length ? out : undefined;
}

export default function McpTab({ notify }: TabProps) {
  const [servers, setServers] = useState<Record<string, McpServerEntry>>({});
  const [statuses, setStatuses] = useState<McpServerStatus[]>([]);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [configPath, setConfigPath] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // 编辑表单
  const [draft, setDraft] = useState<Draft | null>(null);
  const [argsText, setArgsText] = useState("");
  const [envText, setEnvText] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [cfg, toolList] = await Promise.all([
        getMcpConfig(),
        listMcpTools(),
      ]);
      setServers(cfg.config.servers ?? {});
      setConfigPath(cfg.path);
      setTools(toolList);
    } catch (e) {
      notify(`读取 MCP 配置失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setLoading(false);
    }
  }, [notify]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const save = async (
    next: Record<string, McpServerEntry>,
    successMsg: string
  ) => {
    setSaving(true);
    try {
      const resp = await putMcpConfig(next);
      setServers(next);
      setStatuses(resp.servers ?? []);
      const fresh = await listMcpTools();
      setTools(fresh);
      notify(successMsg);
    } catch (e) {
      notify(`保存失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async (name: string, enabled: boolean) => {
    await save({ ...servers, [name]: { ...servers[name], enabled } },
      enabled ? `已启用 ${name}` : `已停用 ${name}`);
  };

  const handleDelete = async (name: string) => {
    setSaving(true);
    try {
      const resp = await deleteMcpServer(name);
      const next = { ...servers };
      delete next[name];
      setServers(next);
      setStatuses(resp.servers ?? []);
      setTools(await listMcpTools());
      notify(`已删除 ${name}`);
    } catch (e) {
      notify(`删除失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSaving(false);
    }
  };

  const handleReload = async () => {
    setSaving(true);
    try {
      const resp = await reloadMcpServers();
      setStatuses(resp.servers ?? []);
      setTools(await listMcpTools());
      notify("已重新连接 MCP 服务器");
    } catch (e) {
      notify(`重载失败: ${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setSaving(false);
    }
  };

  const openDraft = (name?: string) => {
    if (name && servers[name]) {
      const e = servers[name];
      setDraft({ ...e, name, originalName: name });
      setArgsText(argsToText(e.args));
      setEnvText(envToText(e.env));
    } else {
      setDraft({ ...EMPTY_DRAFT, name: "" });
      setArgsText("");
      setEnvText("");
    }
  };

  const handleSubmitDraft = async () => {
    if (!draft) return;
    const name = draft.name.trim();
    if (!name) {
      notify("请填写服务器名称");
      return;
    }
    if (draft.transport === "stdio" && !draft.command?.trim()) {
      notify("stdio 模式必须填写启动命令");
      return;
    }
    if (draft.transport === "sse" && !draft.url?.trim()) {
      notify("sse 模式必须填写 URL");
      return;
    }

    const entry: McpServerEntry = {
      transport: draft.transport,
      enabled: draft.enabled ?? true,
    };
    if (draft.transport === "stdio") {
      entry.command = draft.command?.trim();
      entry.args = textToArgs(argsText);
    } else {
      entry.url = draft.url?.trim();
    }
    const env = textToEnv(envText);
    if (env) entry.env = env;
    if (draft.description?.trim()) entry.description = draft.description.trim();

    const next = { ...servers };
    // 改名时删掉旧键，避免留下幽灵配置
    if (draft.originalName && draft.originalName !== name) {
      delete next[draft.originalName];
    }
    next[name] = entry;

    await save(next, `已保存 ${name}`);
    setDraft(null);
  };

  const statusOf = (name: string) => statuses.find((s) => s.name === name);

  return (
    <div className="settings-section">
      <div className="settings-section-title">MCP 服务器</div>

      <div className="setting-hint" style={{ marginBottom: "12px" }}>
        MCP（Model Context Protocol）用于挂载外部工具服务器。启用后，贾维斯
        会自动发现并调用其中的工具。修改会立即重连，无需重启。
      </div>

      {configPath && (
        <div
          style={{
            padding: "6px 10px",
            borderRadius: "6px",
            marginBottom: "12px",
            background: "rgba(128,128,128,0.06)",
            fontSize: "11px",
            color: "var(--text-muted)",
            fontFamily: "monospace",
            wordBreak: "break-all",
          }}
          title="打包版读写的是 exe 同级的文件，不是打进安装包的副本"
        >
          配置文件: {configPath}
        </div>
      )}

      {loading ? (
        <div style={{ fontSize: "12px", color: "var(--text-muted)" }}>
          加载中...
        </div>
      ) : (
        <>
          {Object.entries(servers).map(([name, entry]) => {
            const status = statusOf(name);
            const connected = status?.connected ?? false;
            const toolCount = status?.tool_count ?? 0;
            return (
              <div
                key={name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  padding: "10px 12px",
                  marginBottom: "8px",
                  borderRadius: "8px",
                  border: "1px solid var(--border, rgba(128,128,128,0.2))",
                  background: "rgba(128,128,128,0.03)",
                }}
              >
                <span
                  title={connected ? "已连接" : "未连接"}
                  style={{
                    width: "8px",
                    height: "8px",
                    borderRadius: "50%",
                    flexShrink: 0,
                    background: connected ? "var(--success)" : "var(--text-muted)",
                  }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "13px", fontWeight: 600 }}>
                    {name}
                    {toolCount > 0 && (
                      <span
                        style={{
                          marginLeft: "8px",
                          fontSize: "11px",
                          fontWeight: 400,
                          color: "var(--text-muted)",
                        }}
                      >
                        {toolCount} 个工具
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: "11px",
                      color: "var(--text-muted)",
                      fontFamily: "monospace",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={entry.command || entry.url || ""}
                  >
                    {entry.transport === "sse"
                      ? entry.url
                      : `${entry.command ?? ""} ${(entry.args ?? []).join(" ")}`}
                  </div>
                </div>

                <label
                  style={{
                    fontSize: "11px",
                    color: "var(--text-secondary)",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: "4px",
                  }}
                >
                  <input
                    type="checkbox"
                    checked={entry.enabled ?? true}
                    disabled={saving}
                    onChange={(e) => handleToggle(name, e.target.checked)}
                  />
                  启用
                </label>

                <button
                  className="setting-btn"
                  style={{ padding: "4px 8px", fontSize: "11px" }}
                  onClick={() => openDraft(name)}
                  disabled={saving}
                >
                  编辑
                </button>
                <button
                  className="setting-btn"
                  style={{ padding: "4px 8px", fontSize: "11px" }}
                  onClick={() => handleDelete(name)}
                  disabled={saving}
                >
                  删除
                </button>
              </div>
            );
          })}

          {Object.keys(servers).length === 0 && (
            <div
              style={{
                fontSize: "12px",
                color: "var(--text-muted)",
                padding: "12px",
                textAlign: "center",
              }}
            >
              尚未配置 MCP 服务器
            </div>
          )}
        </>
      )}

      <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
        <button
          className="setting-btn"
          onClick={() => openDraft()}
          disabled={saving}
        >
          添加服务器
        </button>
        <button className="setting-btn" onClick={handleReload} disabled={saving}>
          {saving ? "处理中..." : "重新连接"}
        </button>
      </div>

      {tools.length > 0 && (
        <>
          <div className="settings-subtitle" style={{ marginTop: "20px" }}>
            可用工具 ({tools.length})
          </div>
          <div style={{ maxHeight: "180px", overflowY: "auto" }}>
            {tools.map((t) => (
              <div
                key={`${t.server}/${t.name}`}
                style={{
                  padding: "6px 10px",
                  marginBottom: "4px",
                  borderRadius: "6px",
                  background: "rgba(128,128,128,0.04)",
                  fontSize: "11px",
                }}
              >
                <span
                  style={{
                    color: "var(--accent-blue-light)",
                    fontFamily: "monospace",
                    fontWeight: 600,
                  }}
                >
                  {t.name}
                </span>
                <span style={{ color: "var(--text-muted)", marginLeft: "8px" }}>
                  {t.server}
                </span>
                {t.description && (
                  <div
                    style={{
                      color: "var(--text-secondary)",
                      marginTop: "2px",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                    title={t.description}
                  >
                    {t.description}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {draft && (
        <div className="custom-model-section" style={{ marginTop: "20px" }}>
          <div className="settings-subtitle">
            {draft.originalName ? `编辑 ${draft.originalName}` : "添加 MCP 服务器"}
          </div>

          <div className="setting-field">
            <label className="setting-label">名称</label>
            <input
              className="setting-input"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="例如: time"
            />
          </div>

          <div className="setting-field">
            <label className="setting-label">传输方式</label>
            <select
              className="setting-input"
              value={draft.transport ?? "stdio"}
              onChange={(e) =>
                setDraft({ ...draft, transport: e.target.value })
              }
            >
              <option value="stdio">stdio（本地进程）</option>
              <option value="sse">sse（远程服务）</option>
            </select>
          </div>

          {draft.transport === "sse" ? (
            <div className="setting-field">
              <label className="setting-label">URL</label>
              <input
                className="setting-input"
                value={draft.url ?? ""}
                onChange={(e) => setDraft({ ...draft, url: e.target.value })}
                placeholder="http://127.0.0.1:8000/sse"
              />
            </div>
          ) : (
            <>
              <div className="setting-field">
                <label className="setting-label">启动命令</label>
                <input
                  className="setting-input"
                  value={draft.command ?? ""}
                  onChange={(e) =>
                    setDraft({ ...draft, command: e.target.value })
                  }
                  placeholder="python 或 npx 等可执行文件的完整路径"
                />
                <div className="setting-hint">
                  打包版请写绝对路径：安装目录里的解释器路径与开发机不同。
                </div>
              </div>

              <div className="setting-field">
                <label className="setting-label">参数（每行一个）</label>
                <textarea
                  className="setting-input"
                  rows={3}
                  value={argsText}
                  onChange={(e) => setArgsText(e.target.value)}
                  placeholder={"server.py\n--flag"}
                  style={{ resize: "vertical", fontFamily: "monospace" }}
                />
              </div>
            </>
          )}

          <div className="setting-field">
            <label className="setting-label">环境变量（可选，每行 KEY=VALUE）</label>
            <textarea
              className="setting-input"
              rows={2}
              value={envText}
              onChange={(e) => setEnvText(e.target.value)}
              placeholder={"API_KEY=xxx"}
              style={{ resize: "vertical", fontFamily: "monospace" }}
            />
          </div>

          <label
            style={{
              fontSize: "12px",
              color: "var(--text-secondary)",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              marginBottom: "10px",
            }}
          >
            <input
              type="checkbox"
              checked={draft.enabled ?? true}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
            />
            启用
          </label>

          <div style={{ display: "flex", gap: "8px" }}>
            <button
              className="setting-btn"
              onClick={handleSubmitDraft}
              disabled={saving}
            >
              {saving ? "保存中..." : "保存"}
            </button>
            <button
              className="setting-btn"
              onClick={() => setDraft(null)}
              disabled={saving}
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
