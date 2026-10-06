import type { ModelNameCheck } from "../../lib/api";

/**
 * 模型 ID 不被网关识别时给出的内联提示条，带一键修正。
 *
 * 这个提示是必要的：`DeepSeek-V4.1-Flash` 这类营销名填进来后网关根本不认，
 * 但设置页不会报错，用户只会看到"这个档位一直空转"。
 */
export default function ModelNameWarning({
  check,
  onApply,
}: {
  check: ModelNameCheck;
  onApply: (name: string) => void;
}) {
  if (!check || check.valid !== false) return null;
  return (
    <div className="model-name-warning">
      <span className="model-name-warning-text">{check.message}</span>
      {check.suggestion && (
        <button
          type="button"
          className="model-name-fix-btn"
          onClick={() => onApply(check.suggestion as string)}
        >
          改用 {check.suggestion}
        </button>
      )}
    </div>
  );
}
