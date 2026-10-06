import { useState } from "react";
import type { WebViewProps } from "../types";

/**
 * 网页镜像视图。
 *
 * 站点若设置了禁止嵌入的响应头（X-Frame-Options / CSP frame-ancestors），
 * iframe 会一直转圈，所以这里用 `embeddable === false` 提前拦下并给出说明
 * ——页面内容其实已被抓取成功，贾维斯照样能读。
 */
export default function WebView({ tab, onOpenExternal }: WebViewProps) {
  // 通过换 key 强制重挂载 iframe 来刷新——被拦截的站点不该留着转圈。
  const [frameKey, setFrameKey] = useState(0);
  const blocked = tab.embeddable === false;

  return (
    <div className="webview-view">
      <div className="webview-addressbar">
        <span className="webview-url" title={tab.url || ""}>
          {tab.url || ""}
        </span>
        <button
          className="webview-reload"
          onClick={() => setFrameKey((k) => k + 1)}
          title="重新加载"
        >
          ⟳
        </button>
      </div>

      {blocked && (
        <div className="webview-blocked">
          <div className="webview-blocked-icon">🚫</div>
          <div className="webview-blocked-title">该网页不允许被嵌入显示</div>
          <div className="webview-blocked-desc">
            {tab.embed_block_reason || "站点设置了禁止嵌入的响应头"}，因此无法在面板内渲染。
            页面内容本身已被成功抓取，贾维斯仍可阅读和分析它。
          </div>
          {onOpenExternal && (
            <button className="webview-blocked-btn" onClick={() => onOpenExternal(tab)}>
              在系统浏览器中打开
            </button>
          )}
        </div>
      )}

      {tab.url ? (
        <iframe
          key={frameKey}
          className="webview-frame"
          style={blocked ? { display: "none" } : undefined}
          src={tab.url}
          title={tab.title}
          sandbox="allow-scripts allow-same-origin allow-popups allow-forms"
        />
      ) : (
        <div className="webview-empty">未设置 URL</div>
      )}
    </div>
  );
}
