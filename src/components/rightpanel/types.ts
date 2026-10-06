import type { RightPanelTab, RightPanelTabType } from "../../lib/types";

export type { RightPanelTab, RightPanelTabType };

/** 每个内容视图的公共 props。 */
export interface ViewProps {
  tab: RightPanelTab;
}

/** WebView 需要额外的"在系统浏览器打开"入口。 */
export interface WebViewProps extends ViewProps {
  onOpenExternal?: (tab: RightPanelTab) => void;
}

/** 页签图标（emoji / 字符，避免为此引入图标库）。 */
export const TAB_ICONS: Record<RightPanelTabType, string> = {
  canvas: "🎨",
  markdown: "📝",
  code: "</>",
  webview: "🌐",
  runner: "▶️",
  pdf: "📄",
  docx: "📘",
  xlsx: "📊",
  pptx: "📽️",
};

/**
 * 把 data URL 解成字节。
 *
 * 三种前缀都要剥：Office 的正式 MIME、`application/octet-stream` 兜底，
 * 以及 Excel 的老式 `vnd.ms-excel`。
 */
export function decodeDataUrl(
  dataUrl: string | undefined,
  mimePrefixes: string[]
): Uint8Array {
  if (!dataUrl) throw new Error("无文件数据");
  if (!dataUrl.startsWith("data:")) {
    throw new Error("仅支持 base64 格式的文件");
  }
  let base64 = dataUrl;
  for (const prefix of mimePrefixes) {
    base64 = base64.replace(new RegExp(`^data:${prefix};base64,`), "");
  }
  base64 = base64.replace(/^data:[^;]+;base64,/, "");
  const binaryStr = atob(base64);
  const bytes = new Uint8Array(binaryStr.length);
  for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
  return bytes;
}
