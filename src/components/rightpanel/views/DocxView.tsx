import { useEffect, useState } from "react";
import type { ViewProps } from "../types";
import { decodeDataUrl } from "../types";

const DOCX_MIMES = [
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/octet-stream",
];

/** DOCX 阅读器：用 mammoth 抽取纯文本（懒加载，避免打进主包）。 */
export default function DocxView({ tab }: ViewProps) {
  const [text, setText] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const bytes = decodeDataUrl(tab.content || tab.url, DOCX_MIMES);
        const mammoth = await import("mammoth");
        const result = await mammoth.extractRawText({ buffer: bytes });
        if (!cancelled) setText(result.value || "（文档内容为空）");
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "解析失败");
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [tab.content, tab.url]);

  return (
    <div className="docx-view">
      <div className="docx-view-header">
        <span className="docx-view-filename">{tab.title}</span>
      </div>
      <div className="docx-view-body">
        {error ? (
          <div className="docx-error">{error}</div>
        ) : text ? (
          <pre className="docx-rendered">{text}</pre>
        ) : (
          <div className="docx-loading">正在解析文档…</div>
        )}
      </div>
    </div>
  );
}
