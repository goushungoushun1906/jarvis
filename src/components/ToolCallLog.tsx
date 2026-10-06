import { useState } from "react";
import type { ToolCallRecord, ToolCallStatus } from "../lib/types";
import "./ToolCallLog.css";

interface ToolCallLogProps {
  toolCalls: ToolCallRecord[];
  isStreaming?: boolean;
  /** Open a URL a tool resolved (e.g. browser_extract / web_fetch) in the display panel. */
  onOpenUrl?: (url: string, title?: string, embeddable?: boolean, reason?: string) => void;
}

const STATUS_ICONS: Record<ToolCallStatus, string> = {
  pending: "⏳",
  running: "⚙️",
  success: "✅",
  error: "❌",
};

const STATUS_LABELS: Record<ToolCallStatus, string> = {
  pending: "等待中",
  running: "执行中",
  success: "已完成",
  error: "失败",
};

/** Shorten long values for display */
function summarizeValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const str = typeof value === "string" ? value : JSON.stringify(value);
  if (str.length > 80) return str.slice(0, 80) + "…";
  return str;
}

/**
 * Chinese display names for the tools that actually exist in the backend
 * registry (Phase 17.4). Unknown tools fall back to their raw name.
 */
const TOOL_LABELS: Record<string, string> = {
  web_search: "网页搜索",
  web_fetch: "抓取网页",
  calculator: "计算器",
  system_info: "系统信息",
  memory: "记忆",
  code_execution: "执行代码",
  screenshot: "屏幕截图",
  clipboard: "剪贴板",
  system_monitor: "系统监控",
  system_control: "系统控制",
  browser_open: "打开网页",
  browser_extract: "提取网页内容",
  browser_screenshot: "网页截图",
  browser_click: "点击页面元素",
  browser_close: "关闭浏览器",
  vision_describe: "图像识别",
  vision_agent: "视觉操作",
  workspace_open: "打开工作区",
  workspace_list_files: "列出文件",
  workspace_search: "搜索文件",
  workspace_read_file: "读取文件",
  workspace_write_file: "写入文件",
  workspace_run_command: "执行命令",
};

/** Generate a human-readable one-line summary for a tool call */
function summarizeToolCall(tc: ToolCallRecord): string {
  if (tc.summary) return tc.summary;
  const name = tc.tool;
  const args = tc.args || {};
  switch (name) {
    case "web_search":
      return `搜索: ${summarizeValue(args.query)}`;
    case "web_fetch":
      return `抓取网页: ${summarizeValue(args.url)}`;
    case "calculator":
      return `计算: ${summarizeValue(args.expression)}`;
    case "system_info":
      return `查看系统信息: ${summarizeValue(args.info_type)}`;
    case "memory":
      return args.action === "save" || args.action === "remember"
        ? `记住: ${summarizeValue(args.content ?? args.text)}`
        : `回忆: ${summarizeValue(args.query)}`;
    case "code_execution":
      return `执行 ${summarizeValue(args.language) || "代码"}`;
    case "screenshot":
      return "截取屏幕画面";
    case "clipboard":
      return args.action === "write" || args.action === "copy" || args.action === "set"
        ? `写入剪贴板: ${summarizeValue(args.text ?? args.content)}`
        : "读取剪贴板";
    case "system_monitor":
      return "查看系统资源占用";
    case "system_control":
      return `系统控制: ${summarizeValue(args.action)}`;
    case "browser_open":
      return `打开网页: ${summarizeValue(args.url)}`;
    case "browser_extract":
      return "提取网页正文";
    case "browser_screenshot":
      return "截取网页画面";
    case "browser_click":
      return `点击页面元素: ${summarizeValue(args.selector ?? args.text)}`;
    case "browser_close":
      return "关闭浏览器会话";
    case "vision_describe":
      return "识别图像内容";
    case "vision_agent":
      return `视觉操作: ${summarizeValue(args.task ?? args.instruction)}`;
    case "workspace_open":
      return `打开工作区: ${summarizeValue(args.path) || "默认目录"}`;
    case "workspace_list_files":
      return `列出文件: ${summarizeValue(args.path) || "根目录"}`;
    case "workspace_search":
      return `搜索文件: ${summarizeValue(args.query ?? args.pattern)}`;
    case "workspace_read_file":
      return `读取文件: ${summarizeValue(args.filename ?? args.path)}`;
    case "workspace_write_file":
      return `写入文件: ${summarizeValue(args.filename ?? args.path)}`;
    case "workspace_run_command":
      return `执行命令: ${summarizeValue(args.command)}`;
    default:
      return `${name}(${Object.keys(args).join(", ")})`;
  }
}

export default function ToolCallLog({ toolCalls, isStreaming, onOpenUrl }: ToolCallLogProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  if (!toolCalls || toolCalls.length === 0) return null;

  const toggle = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const runningCount = toolCalls.filter((tc) => tc.status === "running").length;

  return (
    <div className="tool-call-log">
      <div className="tool-call-header">
        <span className="tool-call-title">
          {isStreaming && runningCount > 0 ? (
            <>
              <span className="tool-call-pulse" /> 正在使用工具
            </>
          ) : (
            <>使用了 {toolCalls.length} 个工具</>
          )}
        </span>
        <span className="tool-call-count">
          {runningCount > 0 ? `${runningCount} 个执行中` : `${toolCalls.filter((tc) => tc.status === "success").length} 个成功`}
        </span>
      </div>
      <div className="tool-call-list">
        {toolCalls.map((tc) => {
          const expanded = expandedIds.has(tc.id);
          const needsApproval = tc.requires_approval && tc.approved === null;
          const browsedUrl = tc.metadata?.url;
          return (
            <div key={tc.id} className={`tool-call-item ${tc.status} ${needsApproval ? "needs-approval" : ""}`}>
              <div className="tool-call-summary-row">
                <button className="tool-call-summary" onClick={() => toggle(tc.id)}>
                  <span className="tool-call-status-icon">{STATUS_ICONS[tc.status]}</span>
                  <span className="tool-call-name" title={tc.tool}>
                    {TOOL_LABELS[tc.tool] ?? tc.tool}
                  </span>
                  <span className="tool-call-desc" title={summarizeToolCall(tc)}>
                    {summarizeToolCall(tc)}
                  </span>
                  {needsApproval && <span className="tool-call-approval-badge">待审批</span>}
                  <span className="tool-call-chevron">{expanded ? "▼" : "▶"}</span>
                </button>
                {browsedUrl && onOpenUrl && (
                  <button
                    className="tool-call-open"
                    title="在右侧展示面板打开此网页"
                    onClick={() =>
                      onOpenUrl(browsedUrl, tc.metadata?.title, tc.metadata?.embeddable, tc.metadata?.embed_block_reason)
                    }
                  >
                    🌐
                  </button>
                )}
              </div>
              {expanded && (
                <div className="tool-call-detail">
                  <div className="tool-call-detail-row">
                    <span className="tool-call-detail-label">状态</span>
                    <span className={`tool-call-detail-value status-${tc.status}`}>{STATUS_LABELS[tc.status]}</span>
                  </div>
                  {tc.started_at && (
                    <div className="tool-call-detail-row">
                      <span className="tool-call-detail-label">开始时间</span>
                      <span className="tool-call-detail-value">{new Date(tc.started_at).toLocaleTimeString()}</span>
                    </div>
                  )}
                  {tc.duration_ms !== undefined && (
                    <div className="tool-call-detail-row">
                      <span className="tool-call-detail-label">耗时</span>
                      <span className="tool-call-detail-value">
                        {tc.duration_ms < 1000 ? `${tc.duration_ms} ms` : `${(tc.duration_ms / 1000).toFixed(2)} s`}
                      </span>
                    </div>
                  )}
                  {tc.requires_approval === true && (
                    <div className="tool-call-detail-row">
                      <span className="tool-call-detail-label">需要审批</span>
                      <span className="tool-call-detail-value">
                        {tc.approved === true ? "已通过" : tc.approved === false ? "已拒绝" : "待确认"}
                      </span>
                    </div>
                  )}
                  <div className="tool-call-detail-row">
                    <span className="tool-call-detail-label">参数</span>
                    <pre className="tool-call-args">{JSON.stringify(tc.args, null, 2)}</pre>
                  </div>
                  {tc.result !== undefined && (
                    <div className="tool-call-detail-row">
                      <span className="tool-call-detail-label">结果</span>
                      <pre className="tool-call-result">{tc.result}</pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
