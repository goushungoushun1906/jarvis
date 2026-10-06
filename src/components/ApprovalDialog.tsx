import type { ToolCallRecord } from "../lib/types";
import "./ApprovalDialog.css";

interface ApprovalDialogProps {
  toolCall: ToolCallRecord;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}

function summarizeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("\n");
}

export default function ApprovalDialog({ toolCall, onApprove, onReject }: ApprovalDialogProps) {
  const toolName = toolCall.tool;
  const isHighRisk = ["click", "type_text", "press_key", "run_shell"].includes(toolName);

  return (
    <div className="approval-overlay">
      <div className="approval-dialog">
        <div className="approval-header">
          <div className={`approval-icon ${isHighRisk ? "risk" : "info"}`}>
            {isHighRisk ? "⚠️" : "🔒"}
          </div>
          <h3 className="approval-title">需要您的确认</h3>
          <p className="approval-subtitle">
            JARVIS 请求执行 <strong>{toolName}</strong> 工具
          </p>
        </div>

        <div className="approval-body">
          <div className="approval-section">
            <span className="approval-label">工具名称</span>
            <span className="approval-value approval-tool-name">{toolName}</span>
          </div>
          <div className="approval-section">
            <span className="approval-label">调用参数</span>
            <pre className="approval-args">{summarizeArgs(toolCall.args)}</pre>
          </div>
          {isHighRisk && (
            <div className="approval-warning">
              这是一个可能影响系统状态或桌面环境的高风险操作，请确认是否允许执行。
            </div>
          )}
        </div>

        <div className="approval-actions">
          <button className="approval-btn reject" onClick={() => onReject(toolCall.id)}>
            拒绝
          </button>
          <button className="approval-btn approve" onClick={() => onApprove(toolCall.id)}>
            允许执行
          </button>
        </div>
      </div>
    </div>
  );
}
