import { useRef, useEffect } from "react";

interface HtmlPreviewModalProps {
  htmlContent: string;
  onClose: () => void;
  fullScreen?: boolean;
}

export default function HtmlPreviewModal({ htmlContent, onClose, fullScreen = false }: HtmlPreviewModalProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // Write HTML into sandboxed iframe
  useEffect(() => {
    if (iframeRef.current) {
      const doc = iframeRef.current.contentDocument;
      if (doc) {
        doc.open();
        doc.write(htmlContent);
        doc.close();
      }
    }
  }, [htmlContent]);

  // ESC to close
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className={`html-preview-overlay ${fullScreen ? "fullscreen" : ""}`} onClick={onClose}>
      <div className="html-preview-container" onClick={(e) => e.stopPropagation()}>
        <div className="html-preview-toolbar">
          <span className="html-preview-title">HTML 预览</span>
          <button onClick={onClose} className="html-preview-close">✕</button>
        </div>
        <iframe
          ref={iframeRef}
          className="html-preview-iframe"
          sandbox="allow-scripts allow-same-origin"
          title="HTML Preview"
        />
      </div>
    </div>
  );
}
