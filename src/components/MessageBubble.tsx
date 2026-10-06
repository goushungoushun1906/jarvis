import { useState } from "react";
import type { Message, RightPanelTab } from "../lib/types";
import HtmlPreviewModal from "./HtmlPreviewModal";
import ToolCallLog from "./ToolCallLog";
import "./MessageBubble.css";

interface MessageBubbleProps {
  message: Message;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  showPlayBtn?: boolean;
  isPlayingThis?: boolean;
  onPlayMessage?: (content: string) => void;
  onFork?: (messageId: number) => void;
  /** Called when a link or file should be opened in the right panel */
  onOpenInRightPanel?: (tab: RightPanelTab) => void;
}

const URL_REGEX = /(?:https?:\/\/|www\.)[^\s<>"')\]]+/g;
const FILE_EXTS: Record<string, RightPanelTab["type"]> = {
  pdf: "pdf",
  docx: "docx",
  doc: "docx",
  xlsx: "xlsx",
  xls: "xlsx",
  pptx: "pptx",
  ppt: "pptx",
  md: "markdown",
  txt: "markdown",
};

function getMimeFromExt(ext: string): string {
  const map: Record<string, string> = {
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    doc: "application/msword",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    xls: "application/vnd.ms-excel",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ppt: "application/vnd.ms-powerpoint",
    md: "text/markdown",
    txt: "text/plain",
  };
  return map[ext] || "application/octet-stream";
}

function getFileIcon(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() || "";
  const icons: Record<string, string> = { pdf: "📄", docx: "📘", doc: "📘", xlsx: "📊", xls: "📊", pptx: "📽️", ppt: "📽️", md: "📝", txt: "📄" };
  return icons[ext] || "📎";
}

function renderMarkdown(text: string): string {
  let html = text;
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_m, _l, code) => {
    const escaped = code.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    return `<pre><code>${escaped}</code></pre>`;
  });
  html = html.replace(/`([^`\n]+)`/g, (_m, code) => {
    const escaped = code.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    return `<code>${escaped}</code>`;
  });
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Convert URLs to <a> tags
  html = html.replace(URL_REGEX, (url) => {
    const display = url.length > 60 ? url.slice(0, 60) + "…" : url;
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${display}</a>`;
  });
  html = html.split("\n\n").map((block) => {
    const lines = block.trim();
    if (!lines) return "";
    if (lines.includes("<pre>")) return lines;
    return lines.split("\n").map((line) => `<p>${line || "<br/>"}</p>`).join("");
  }).join("");
  return html;
}

interface ContentSegment { type: "text" | "html-code"; content: string; }

function parseHtmlCodeBlocks(text: string): ContentSegment[] {
  const segments: ContentSegment[] = [];
  const regex = /```html\n?([\s\S]*?)```/gi;
  let lastIndex = 0;
  let match;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) segments.push({ type: "text", content: text.slice(lastIndex, match.index) });
    segments.push({ type: "html-code", content: match[1].trim() });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) segments.push({ type: "text", content: text.slice(lastIndex) });
  if (segments.length === 0) segments.push({ type: "text", content: text });
  return segments;
}

function CodeBlockWithToolbar({ code }: { code: string }) {
  const [showPreview, setShowPreview] = useState(false);
  const [copyMsg, setCopyMsg] = useState("");
  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => { setCopyMsg("已复制"); setTimeout(() => setCopyMsg(""), 2000); });
  };
  const handleOpen = () => {
    const blob = new Blob([code], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    URL.revokeObjectURL(url);
  };
  return (
    <div className="html-code-block">
      <div className="code-block-toolbar">
        <span className="code-block-lang">HTML</span>
        <div className="code-block-actions">
          <button onClick={() => setShowPreview(true)} className="code-action-btn" title="预览">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>预览
          </button>
          <button onClick={handleCopy} className="code-action-btn" title="复制">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>{copyMsg || "复制"}
          </button>
          <button onClick={handleOpen} className="code-action-btn" title="新标签页打开">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>打开
          </button>
        </div>
      </div>
      <pre><code>{code}</code></pre>
      {showPreview && <HtmlPreviewModal htmlContent={code} onClose={() => setShowPreview(false)} />}
    </div>
  );
}

export default function MessageBubble({ message, isStreaming, isLastAssistant, showPlayBtn, isPlayingThis, onPlayMessage, onFork, onOpenInRightPanel }: MessageBubbleProps) {
  const [copied, setCopied] = useState(false);
  const [liked, setLiked] = useState(false);
  const [disliked, setDisliked] = useState(false);
  const isAssistant = message.role === "assistant";

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleFileOpen = (file: NonNullable<Message["files"]>[number]) => {
    if (!onOpenInRightPanel) return;
    const ext = file.filename.split(".").pop()?.toLowerCase() || "";
    const type = FILE_EXTS[ext] || "markdown";
    onOpenInRightPanel({
      id: `file-${file.filename}-${Date.now()}`,
      title: file.filename,
      type,
      content: file.extracted_text || "",
      url: file.data_url,
      language: type === "code" ? ext : undefined,
      closable: true,
    });
  };

  if (message.role === "system") {
    return (
      <div className="msg-row system-row">
        <div className="msg-bubble msg-bubble-system">{message.content}</div>
      </div>
    );
  }

  return (
    <div className={`msg-row ${message.role === "user" ? "user-row" : "assistant-row"}`}>
      {!isAssistant && (
        <div className="msg-avatar user-avatar">用</div>
      )}
      <div className="msg-body">
        <div className={`msg-bubble ${message.role}`}>
          {isAssistant ? (
            <div className="markdown-body">
              {parseHtmlCodeBlocks(message.content).map((seg, i) =>
                seg.type === "html-code"
                  ? <CodeBlockWithToolbar key={i} code={seg.content} />
                  : <span key={i} dangerouslySetInnerHTML={{ __html: renderMarkdown(seg.content) || "" }} />
              )}
              {isStreaming && isLastAssistant && <span className="streaming-cursor" />}
            </div>
          ) : (
            <div className="markdown-body">
              {parseHtmlCodeBlocks(message.content).map((seg, i) =>
                seg.type === "html-code"
                  ? <CodeBlockWithToolbar key={i} code={seg.content} />
                  : <span key={i} dangerouslySetInnerHTML={{ __html: renderMarkdown(seg.content) || "" }} />
              )}
            </div>
          )}

          {message.images && message.images.length > 0 && (
            <div className="msg-images">
              {message.images.map((img, i) => (
                <img key={i} src={img.image_url.url} alt={img.filename || "图片"} className="msg-img" onClick={() => window.open(img.image_url.url, "_blank")} />
              ))}
            </div>
          )}
          {message.files && message.files.length > 0 && (
            <div className="msg-files">
              {message.files.map((f, i) => (
                <div key={i} className="file-badge-wrap">
                  <span className="file-badge">
                    {getFileIcon(f.filename)} {f.filename}
                  </span>
                  {onOpenInRightPanel && (
                    <button className="file-open-btn" onClick={() => handleFileOpen(f)} title="在右侧面板打开">
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="15 3 21 3 21 9" />
                        <polyline points="9 21 3 21 3 15" />
                        <line x1="21" y1="3" x2="14" y2="10" />
                        <line x1="3" y1="21" x2="10" y2="14" />
                      </svg>
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Tool call log — show what the agent is doing */}
          {message.tool_calls && message.tool_calls.length > 0 && (
            <ToolCallLog
              toolCalls={message.tool_calls}
              isStreaming={isStreaming && isLastAssistant}
              onOpenUrl={
                onOpenInRightPanel
                  ? (url, title, embeddable, reason) =>
                      onOpenInRightPanel({
                        id: "webview-agent",
                        title: title || url,
                        type: "webview",
                        url,
                        embeddable,
                        embed_block_reason: reason,
                        closable: true,
                      })
                  : undefined
              }
            />
          )}
        </div>

        {/* Action bar — only for assistant messages */}
        {isAssistant && !isStreaming && message.content && (
          <div className="msg-actions">
            <button className="msg-action-btn" onClick={handleCopy} title={copied ? "已复制" : "复制内容"}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
              </svg>
              {copied ? "已复制" : "复制"}
            </button>
            {showPlayBtn && onPlayMessage && (
              <button className={`msg-action-btn ${isPlayingThis ? "playing" : ""}`} onClick={() => onPlayMessage(message.content)} title={isPlayingThis ? "停止朗读" : "朗读"}>
                {isPlayingThis
                  ? <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
                  : <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                }
                朗读
              </button>
            )}
            <button className={`msg-action-btn ${liked ? "active" : ""}`} onClick={() => { setLiked(v => !v); setDisliked(false); }} title="好评">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
              赞
            </button>
            <button className={`msg-action-btn ${disliked ? "active" : ""}`} onClick={() => { setDisliked(v => !v); setLiked(false); }} title="差评">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>
              踩
            </button>
            {onFork && message.id && (
              <button className="msg-action-btn" onClick={() => onFork(Number(message.id))} title="从此处分叉对话">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>
                </svg>
                分叉
              </button>
            )}
          </div>
        )}
      </div>
      {isAssistant && (
        <div className="msg-avatar assistant-avatar">J</div>
      )}
    </div>
  );
}
