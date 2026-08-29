import { useState } from "react";
import type { Message } from "../lib/types";
import HtmlPreviewModal from "./HtmlPreviewModal";

interface MessageBubbleProps {
  message: Message;
  isStreaming?: boolean;
  isLastAssistant?: boolean;
  showPlayBtn?: boolean;
  isPlayingThis?: boolean;
  onPlayMessage?: (content: string) => void;
  onFork?: (messageId: number) => void;
}

/**
 * Simple markdown-like rendering using regex.
 * Converts **bold**, *italic*, `inline code`, and ```code blocks``` to HTML.
 */
function renderMarkdown(text: string): string {
  let html = text;

  // Code blocks: ```...```
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (_match, _lang, code) => {
    const escaped = code
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return `<pre><code>${escaped}</code></pre>`;
  });

  // Inline code: `code`
  html = html.replace(/`([^`\n]+)`/g, (_match, code) => {
    const escaped = code
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return `<code>${escaped}</code>`;
  });

  // Bold: **text**
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Italic: *text*
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

  // Line breaks -> paragraphs
  html = html
    .split("\n\n")
    .map((block) => {
      const lines = block.trim();
      if (!lines) return "";
      // If block contains pre tags, keep as-is
      if (lines.includes("<pre>")) return lines;
      return lines
        .split("\n")
        .map((line) => `<p>${line || "<br/>"}</p>`)
        .join("");
    })
    .join("");

  return html;
}

interface ContentSegment {
  type: "text" | "html-code";
  content: string;
}

function parseHtmlCodeBlocks(text: string): ContentSegment[] {
  const segments: ContentSegment[] = [];
  const regex = /```html\n?([\s\S]*?)```/gi;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: "text", content: text.slice(lastIndex, match.index) });
    }
    segments.push({ type: "html-code", content: match[1].trim() });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    segments.push({ type: "text", content: text.slice(lastIndex) });
  }

  if (segments.length === 0) {
    segments.push({ type: "text", content: text });
  }

  return segments;
}

function CodeBlockWithToolbar({ code }: { code: string }) {
  const [showPreview, setShowPreview] = useState(false);
  const [copyMsg, setCopyMsg] = useState("");

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopyMsg("已复制");
      setTimeout(() => setCopyMsg(""), 2000);
    });
  };

  const handleOpen = () => {
    const blob = new Blob([code], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    URL.revokeObjectURL(url);
  };

  const handlePreview = () => setShowPreview(true);

  return (
    <div className="html-code-block">
      <div className="code-block-toolbar">
        <span className="code-block-lang">HTML</span>
        <div className="code-block-actions">
          <button onClick={handlePreview} className="code-action-btn" title="预览">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
            </svg>
            预览
          </button>
          <button onClick={handleCopy} className="code-action-btn" title="复制">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
            </svg>
            {copyMsg || "复制"}
          </button>
          <button onClick={handleOpen} className="code-action-btn" title="在新标签页打开">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
            </svg>
            打开
          </button>
        </div>
      </div>
      <pre><code>{code}</code></pre>
      {showPreview && <HtmlPreviewModal htmlContent={code} onClose={() => setShowPreview(false)} />}
    </div>
  );
}

export default function MessageBubble({
  message,
  isStreaming,
  isLastAssistant,
  showPlayBtn,
  isPlayingThis,
  onPlayMessage,
  onFork,
}: MessageBubbleProps) {
  const wrapperClass = `message-wrapper ${message.role}`;
  const senderLabel = message.role === "user" ? "用户" : message.role === "assistant" ? "JARVIS" : "系统";
  const previewText = message.content.slice(0, 50) + (message.content.length > 50 ? "..." : "");

  if (message.role === "system") {
    return (
      <div className={wrapperClass} role="article" aria-label={`${senderLabel}: ${previewText}`}>
        <div className="message-bubble">{message.content}</div>
      </div>
    );
  }

  return (
    <div className={wrapperClass} role="article" aria-label={`${senderLabel}: ${previewText}`}>
      <div className="message-bubble">
        {message.role === "assistant" ? (
          <div className="markdown-body">
            {parseHtmlCodeBlocks(message.content).map((seg, i) => {
              if (seg.type === "html-code") {
                return <CodeBlockWithToolbar key={i} code={seg.content} />;
              }
              return (
                <span key={i} dangerouslySetInnerHTML={{ __html: renderMarkdown(seg.content) || "" }} />
              );
            })}
            {isStreaming && isLastAssistant && (
              <span className="streaming-cursor" />
            )}
          </div>
        ) : (
          <div className="markdown-body">
            {parseHtmlCodeBlocks(message.content).map((seg, i) => {
              if (seg.type === "html-code") {
                return <CodeBlockWithToolbar key={i} code={seg.content} />;
              }
              return (
                <span key={i} dangerouslySetInnerHTML={{ __html: renderMarkdown(seg.content) || "" }} />
              );
            })}
          </div>
        )}

        {/* Image attachments */}
        {message.images && message.images.length > 0 && (
          <div className="message-images">
            {message.images.map((img, i) => (
              <img
                key={i}
                src={img.image_url.url}
                alt={img.filename || "图片"}
                className="message-image"
                onClick={() => window.open(img.image_url.url, "_blank")}
              />
            ))}
          </div>
        )}

        {/* File badges */}
        {message.files && message.files.length > 0 && (
          <div className="message-files">
            {message.files.map((file, i) => (
              <div key={i} className="message-file-badge">
                <span className="file-badge-icon">📄</span>
                <span>{file.filename}</span>
              </div>
            ))}
          </div>
        )}

        {/* Play button for assistant messages in manual/auto mode */}
        {message.role === "assistant" &&
          message.content &&
          !isStreaming &&
          showPlayBtn &&
          onPlayMessage && (
            <button
              className={`message-play-btn ${isPlayingThis ? "playing" : ""}`}
              onClick={() => onPlayMessage(message.content)}
              title={isPlayingThis ? "停止朗读" : "朗读此消息"}
            >
              {isPlayingThis ? (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="6" width="12" height="12" rx="1" />
                </svg>
              ) : (
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3" />
                </svg>
              )}
            </button>
          )}

        {/* Fork button */}
        {onFork && message.id && !isStreaming && (
          <button
            className="fork-btn"
            onClick={() => onFork(Number(message.id))}
            title="从此处分叉对话"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="6" y1="3" x2="6" y2="15"/>
              <circle cx="18" cy="6" r="3"/>
              <circle cx="6" cy="18" r="3"/>
              <path d="M18 9a9 9 0 0 1-9 9"/>
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}
