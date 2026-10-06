import { useEffect, useState } from "react";
import type { ViewProps } from "../types";
import { decodeDataUrl } from "../types";

const XLSX_MIMES = [
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel",
  "application/octet-stream",
];

/** 单表最多渲染的行数——再多行首屏也读不完，还会拖垮滚动。 */
const MAX_ROWS = 200;

interface Sheet {
  name: string;
  rows: string[][];
}

/** XLSX 阅读器：SheetJS 解析（懒加载）。 */
export default function XlsxView({ tab }: ViewProps) {
  const [sheets, setSheets] = useState<Sheet[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const bytes = decodeDataUrl(tab.content || tab.url, XLSX_MIMES);
        const XLSX = (await import("xlsx")).default;
        const wb = XLSX.read(bytes, { type: "array" });
        const parsed = wb.SheetNames.map((name) => {
          const ws = wb.Sheets[name];
          return {
            name,
            rows: XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" }) as string[][],
          };
        });
        if (!cancelled) setSheets(parsed);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "解析失败");
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [tab.content, tab.url]);

  const currentSheet = sheets[activeSheet];

  return (
    <div className="xlsx-view">
      <div className="xlsx-view-header">
        <span className="xlsx-view-filename">{tab.title}</span>
      </div>
      {sheets.length > 1 && (
        <div className="xlsx-sheet-tabs">
          {sheets.map((s, i) => (
            <button
              key={i}
              className={`xlsx-sheet-tab ${i === activeSheet ? "active" : ""}`}
              onClick={() => setActiveSheet(i)}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="xlsx-view-body">
        {error ? (
          <div className="xlsx-error">{error}</div>
        ) : currentSheet ? (
          <div className="xlsx-table-wrap">
            <table className="xlsx-table">
              <tbody>
                {currentSheet.rows.slice(0, MAX_ROWS).map((row, ri) => (
                  <tr key={ri}>
                    {row.map((cell, ci) => (
                      <td key={ci} className={ri === 0 ? "xlsx-header" : ""}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {currentSheet.rows.length > MAX_ROWS && (
              <div className="xlsx-truncate-hint">
                已显示前 {MAX_ROWS} 行，共 {currentSheet.rows.length} 行
              </div>
            )}
          </div>
        ) : (
          <div className="xlsx-loading">正在解析表格…</div>
        )}
      </div>
    </div>
  );
}
