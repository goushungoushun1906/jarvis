import type { ViewProps } from "../types";
import SimpleMarkdown from "../SimpleMarkdown";

/** PDF：优先用内联的 base64 data URL，否则退回 url。 */
export function PdfView({ tab }: ViewProps) {
  const src = tab.content?.startsWith("data:application/pdf") ? tab.content : tab.url;
  return (
    <div className="pdf-view">
      {src ? (
        <iframe className="pdf-frame" src={src} title={tab.title} />
      ) : (
        <div className="pdf-empty">未找到 PDF 内容</div>
      )}
    </div>
  );
}

/** PPTX：暂不做可视化渲染，展示抽取出的文本。 */
export function PptxView({ tab }: ViewProps) {
  return (
    <div className="pptx-view">
      <div className="pptx-view-header">
        <span className="pptx-view-filename">{tab.title}</span>
      </div>
      <div className="pptx-view-body">
        {tab.content ? (
          <SimpleMarkdown source={tab.content} />
        ) : (
          <div className="pptx-empty">PPT 文件暂不支持可视化渲染，请联系开发以启用。</div>
        )}
      </div>
    </div>
  );
}

/** 代码块：只做展示，语言标签来自 tab.language。 */
export function CodeView({ tab }: ViewProps) {
  return (
    <div className="code-view">
      <div className="code-view-header">
        <span className="code-view-lang">{tab.language || "text"}</span>
        <span className="code-view-filename">{tab.title}</span>
      </div>
      <pre className="code-view-body">
        <code>{tab.content || ""}</code>
      </pre>
    </div>
  );
}

/** 运行结果占位视图。 */
export function RunnerView({ tab }: ViewProps) {
  return (
    <div className="runner-view">
      <div className="runner-toolbar">
        <button className="runner-run-btn">运行</button>
        <button className="runner-stop-btn">停止</button>
      </div>
      <div className="runner-output">
        {typeof tab.content === "string" ? tab.content : "等待运行…"}
      </div>
    </div>
  );
}
