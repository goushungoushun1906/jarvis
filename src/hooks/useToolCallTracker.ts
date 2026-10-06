import { useCallback, useRef, useState } from "react";
import type { ToolCallRecord } from "../lib/types";

/**
 * 高风险工具名单 —— 仅作**兜底**用。
 *
 * 后端 `BaseTool.requires_approval` 才是唯一事实来源（Phase 17.3）。这里只在
 * 后端没下发 `requires_approval` 时（老后端、或注册外的工具）才被咨询。
 *
 * NOTE: 这些必须是**真实**的工具名；早期版本写的是 "click" / "run_shell" /
 * "press_key"，从来没匹配上过任何东西。
 */
const HIGH_RISK_TOOLS = new Set([
  "workspace_run_command",
  "workspace_write_file",
  "system_control",
  "code_execution",
  "vision_agent",
]);

export function requiresApproval(toolName: string): boolean {
  return HIGH_RISK_TOOLS.has(toolName);
}

export interface ToolCallTracker {
  /** 当前轮次累积的工具调用（按 id 去重，保持插入顺序）。 */
  upsert(toolCall: ToolCallRecord): ToolCallRecord[];
  /** 清空本轮记录，开始新一轮。 */
  reset(): void;
  /** 读取某条记录（用于审批时补全字段）。 */
  get(id: string): ToolCallRecord | undefined;
  /** 当前全部记录（渲染用快照）。 */
  list(): ToolCallRecord[];
  /** 把工具调用补全成带审批语义的记录。 */
  enrich(toolCall: ToolCallRecord): ToolCallRecord;
  /** 把已结束的结果合并进已有记录。 */
  mergeResult(toolCall: ToolCallRecord): ToolCallRecord[];
  /** 中止/致命错误时，把还在跑的卡片标记为已取消。 */
  markRunningAsCancelled(): void;
  /** 等待用户审批的调用队列。 */
  pendingApprovals: ToolCallRecord[];
  approve(id: string): void;
  reject(id: string): void;
}

/**
 * 管理工具调用的记录、审批队列与取消语义。
 *
 * 用 ref 存 Map 而不是 state：流式回调每秒触发多次，走 state 会丢更新且
 * 触发大量重渲染；只有"要渲染的那一瞬间"才转成数组。
 */
export function useToolCallTracker(): ToolCallTracker {
  const toolCallsRef = useRef<Map<string, ToolCallRecord>>(new Map());
  const [pendingApprovals, setPendingApprovals] = useState<ToolCallRecord[]>([]);

  const upsert = useCallback((toolCall: ToolCallRecord): ToolCallRecord[] => {
    toolCallsRef.current.set(toolCall.id, toolCall);
    return Array.from(toolCallsRef.current.values());
  }, []);

  const reset = useCallback(() => {
    toolCallsRef.current = new Map();
    setPendingApprovals([]);
  }, []);

  const get = useCallback((id: string) => toolCallsRef.current.get(id), []);

  const list = useCallback(() => Array.from(toolCallsRef.current.values()), []);

  const enrich = useCallback((toolCall: ToolCallRecord): ToolCallRecord => {
    const needs =
      toolCall.requires_approval ?? requiresApproval(toolCall.tool);
    const enriched: ToolCallRecord = {
      ...toolCall,
      requires_approval: needs,
      approved: needs ? null : true,
      status: toolCall.status || "running",
    };
    upsert(enriched);
    if (needs && enriched.approved === null) {
      setPendingApprovals((prev) =>
        prev.some((p) => p.id === enriched.id) ? prev : [...prev, enriched]
      );
    }
    return enriched;
  }, [upsert]);

  const mergeResult = useCallback(
    (toolResult: ToolCallRecord): ToolCallRecord[] => {
      const existing = toolCallsRef.current.get(toolResult.id);
      return upsert({
        ...(existing || toolResult),
        ...toolResult,
        status: toolResult.success ? "success" : "error",
        finished_at: toolResult.finished_at || new Date().toISOString(),
      });
    },
    [upsert]
  );

  const markRunningAsCancelled = useCallback(() => {
    toolCallsRef.current.forEach((tc, id) => {
      if (tc.status === "running" || tc.status === "pending") {
        toolCallsRef.current.set(id, {
          ...tc,
          status: "error",
          success: false,
          result: tc.result || "已取消",
          finished_at: new Date().toISOString(),
        });
      }
    });
  }, []);

  const approve = useCallback((id: string) => {
    setPendingApprovals((prev) => prev.filter((tc) => tc.id !== id));
    const tc = toolCallsRef.current.get(id);
    if (!tc) return;
    toolCallsRef.current.set(id, {
      ...tc,
      approved: true,
      status: "running",
    });
    notifyBackend(id, true);
  }, []);

  const reject = useCallback((id: string) => {
    setPendingApprovals((prev) => prev.filter((tc) => tc.id !== id));
    const tc = toolCallsRef.current.get(id);
    if (!tc) return;
    toolCallsRef.current.set(id, {
      ...tc,
      approved: false,
      status: "error",
    });
    notifyBackend(id, false);
  }, []);

  return {
    upsert,
    reset,
    get,
    list,
    enrich,
    mergeResult,
    markRunningAsCancelled,
    pendingApprovals,
    approve,
    reject,
  };
}

/** 通知后端审批结果——后端可能还没实现该接口，失败保持静默。 */
function notifyBackend(toolCallId: string, approved: boolean): void {
  fetch("http://127.0.0.1:18200/api/tools/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool_call_id: toolCallId, approved }),
  }).catch(() => {
    /* silent — backend may not implement this endpoint yet */
  });
}
