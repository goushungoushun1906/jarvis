import type { RightPanelTab } from "./types";
import SimpleMarkdown from "./SimpleMarkdown";
import CanvasView from "./views/CanvasView";
import WebView from "./views/WebView";
import DocxView from "./views/DocxView";
import XlsxView from "./views/XlsxView";
import {
  PdfView,
  PptxView,
  CodeView,
  RunnerView,
} from "./views/DocumentViews";

/** 按页签类型路由到对应视图。 */
export default function TabContent({
  tab,
  onOpenExternal,
}: {
  tab: RightPanelTab;
  onOpenExternal?: (tab: RightPanelTab) => void;
}) {
  switch (tab.type) {
    case "canvas":
      return <CanvasView tab={tab} />;
    case "markdown":
      return <SimpleMarkdown source={tab.content || ""} />;
    case "code":
      return <CodeView tab={tab} />;
    case "webview":
      return <WebView tab={tab} onOpenExternal={onOpenExternal} />;
    case "runner":
      return <RunnerView tab={tab} />;
    case "pdf":
      return <PdfView tab={tab} />;
    case "docx":
      return <DocxView tab={tab} />;
    case "xlsx":
      return <XlsxView tab={tab} />;
    case "pptx":
      return <PptxView tab={tab} />;
    default:
      return <div className="right-panel-unsupported">不支持的内容类型</div>;
  }
}
