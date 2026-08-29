import React, { useRef, useEffect, useCallback, useState, useMemo, Suspense } from "react";
import type { Message, JavisStatus, ConnectionStatus, VoiceMode, ImageAttachment, FileAttachment } from "../lib/types";
import BreathingIndicator from "./BreathingIndicator";
import MessageBubble from "./MessageBubble";
import RegionSelector, { type Region } from "./RegionSelector";
import jarvisAvatar from "../assets/jarvis-avatar.png";
import './JarvisAvatar3D.css';
import { classifyEmotion } from "../lib/emotionClassifier";
import { captureRegionScreenshot } from "../lib/api";
import "./ChatArea.css";

// Lazy-loaded VoiceControl component
const VoiceControl = React.lazy(() => import("./VoiceControl").then(m => ({ default: m.default })));

interface ChatAreaProps {
  messages: Message[];
  isStreaming: boolean;
  javisStatus: JavisStatus;
  connectionStatus: ConnectionStatus;
  error: string | null;
  input: string;
  currentModel: string;
  conversationTitle: string;
  settingsOpen: boolean;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onClearError: () => void;
  onToggleSettings: () => void;
  // Voice props
  voiceMode: VoiceMode;
  selectedVoice: string;
  isVoicePlaying: boolean;
  isListening: boolean;
  onVoiceModeChange: (mode: VoiceMode) => void;
  onVoiceSelect: (voiceId: string) => void;
  onVoiceTranscript: (text: string) => void;
  onListeningChange?: (listening: boolean) => void;
  onPlayMessage: (content: string) => void;
  onStopVoice: () => void;
  // Attachment props
  pendingImages: ImageAttachment[];
  pendingFiles: FileAttachment[];
  onImageSelect: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onFileSelect: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onRemovePendingImage: (index: number) => void;
  onRemovePendingFile: (index: number) => void;
  imageInputRef: React.RefObject<HTMLInputElement | null>;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  onForkFromMessage?: (messageId: number) => void;
  onSwitchModel?: (modelId: string) => Promise<void>;
}

export default function ChatArea({
  messages,
  isStreaming,
  javisStatus,
  connectionStatus,
  error,
  input,
  currentModel,
  conversationTitle,
  settingsOpen,
  onInputChange,
  onSend,
  onStop,
  onClearError,
  onToggleSettings,
  voiceMode,
  selectedVoice,
  isVoicePlaying,
  isListening,
  onVoiceModeChange,
  onVoiceSelect,
  onVoiceTranscript,
  onListeningChange,
  onPlayMessage,
  onStopVoice,
  pendingImages,
  pendingFiles,
  onImageSelect,
  onFileSelect,
  onRemovePendingImage,
  onRemovePendingFile,
  imageInputRef,
  fileInputRef,
  onForkFromMessage,
  onSwitchModel,
}: ChatAreaProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-expand textarea
  const adjustTextareaHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const newHeight = Math.min(el.scrollHeight, 132); // max 6 rows
    el.style.height = `${newHeight}px`;
  }, []);

  useEffect(() => {
    adjustTextareaHeight();
  }, [input, adjustTextareaHeight]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const lastAssistantIdx = messages.reduce<number>((acc, msg, i) => {
    return msg.role === "assistant" ? i : acc;
  }, -1);

  const [showMessages, setShowMessages] = useState(true);
  const [availableModels, setAvailableModels] = useState<Array<{id:string;name:string;is_active:boolean}>>([]);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);

  const HIDDEN_BUILTIN_MODEL_IDS = useMemo(() => new Set([
    "agnes-flash", "gpt4o-mini", "gpt4o", "deepseek", "doubao", "claude"
  ]), []);

  // ---- Wake word state ----
  const [wakeWordStatus, setWakeWordStatus] = useState<'idle'|'listening'|'detected'|'error'>('idle');
  const [wakeWordConfidence, setWakeWordConfidence] = useState(0);
  const [wakeWordFlash, setWakeWordFlash] = useState(false);
  const wakeWordPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- Region screenshot state ----
  const [showRegionSelector, setShowRegionSelector] = useState(false);
  const [isCapturingRegion, setIsCapturingRegion] = useState(false);
  const [regionToast, setRegionToast] = useState<string | null>(null);

  // Poll wake word status every 500ms
  useEffect(() => {
    const poll = async () => {
      try {
        const resp = await fetch('http://127.0.0.1:18200/api/wakeword/poll');
        if (resp.ok) {
          const data = await resp.json();
          if (data.info) {
            setWakeWordStatus(data.info.status);
            setWakeWordConfidence(data.info.peak_confidence || 0);
          }
          if (data.detections && data.detections.length > 0) {
            setWakeWordFlash(true);
            setTimeout(() => setWakeWordFlash(false), 2000);
          }
        }
      } catch { /* backend may be offline */ }
    };
    poll();
    wakeWordPollRef.current = setInterval(poll, 500);
    return () => { if (wakeWordPollRef.current) clearInterval(wakeWordPollRef.current); };
  }, []);

  const toggleWakeWord = async () => {
    try {
      if (wakeWordStatus === 'listening') {
        await fetch('http://127.0.0.1:18200/api/wakeword/stop', { method: 'POST' });
      } else {
        await fetch('http://127.0.0.1:18200/api/wakeword/start', { method: 'POST' });
      }
    } catch (e) {
      console.error('Wake word toggle failed:', e);
    }
  };

  // Fetch model list on mount
  useEffect(() => {
    const fetchModels = async () => {
      try {
        const resp = await fetch('http://127.0.0.1:18200/api/config/models');
        if (resp.ok) {
          const data = await resp.json();
          // Show default models (Agnes 2.0, local Ollama) and user-added custom models
          const visible = data.filter((m: {id: string}) => !HIDDEN_BUILTIN_MODEL_IDS.has(m.id));
          setAvailableModels(visible);
        }
      } catch { /* silent */ }
    };
    fetchModels();
    const interval = setInterval(fetchModels, 30000);
    return () => clearInterval(interval);
  }, []);

  // Click outside handler to close dropdown
  useEffect(() => {
    if (!modelDropdownOpen) return;
    const handler = () => setModelDropdownOpen(false);
    setTimeout(() => document.addEventListener('click', handler), 0);
    return () => document.removeEventListener('click', handler);
  }, [modelDropdownOpen]);

  // ---- Region screenshot handlers ----
  const handleStartRegionScreenshot = async () => {
    if (isStreaming || connectionStatus === "offline") return;

    // 如果运行在 Electron 中，优先使用主进程提供的全屏选择器
    if (window.electronAPI?.captureRegion) {
      setIsCapturingRegion(true);
      try {
        const region = await window.electronAPI.captureRegion();
        if (region) {
          await handleRegionCaptured(region);
        }
      } catch (err: any) {
        setRegionToast(`区域截图失败: ${err?.message || "未知错误"}`);
        setTimeout(() => setRegionToast(null), 4000);
      } finally {
        setIsCapturingRegion(false);
      }
      return;
    }

    // 否则使用应用内遮罩选择器
    setShowRegionSelector(true);
  };

  const handleRegionCaptured = async (region: Region) => {
    setShowRegionSelector(false);
    setIsCapturingRegion(true);
    setRegionToast("正在识别选中区域...");
    try {
      const result = await captureRegionScreenshot(region);
      const text = result.text || "";
      if (text.trim()) {
        const prefix = input.trim() ? input.trim() + "\n\n" : "";
        onInputChange(prefix + text.trim());
        setRegionToast(`已识别 ${text.trim().length} 字符`);
      } else {
        setRegionToast("选中区域未识别到文字");
      }
    } catch (err: any) {
      setRegionToast(`识别失败: ${err?.message || "未知错误"}`);
    } finally {
      setIsCapturingRegion(false);
      setTimeout(() => setRegionToast(null), 4000);
    }
  };

  const handleRegionCancel = () => {
    setShowRegionSelector(false);
  };

  return (
    <div className="main-area">
      {showRegionSelector && (
        <RegionSelector onCapture={handleRegionCaptured} onCancel={handleRegionCancel} />
      )}

      {/* Header */}
      <div className="chat-header">
        <div className="chat-header-left">
          <BreathingIndicator status={javisStatus} />
          <span className="chat-title">{conversationTitle || "JARVIS"}</span>
        </div>
        <div className="chat-header-right">
          <button
            className={`header-btn ${showMessages ? "active" : ""}`}
            onClick={() => setShowMessages(!showMessages)}
            title={showMessages ? "收起对话" : "展开对话"}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </button>
          <button
            className={`header-btn header-btn-settings ${settingsOpen ? "active" : ""}`}
            onClick={onToggleSettings}
            title="设置"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            <span className="header-btn-label">设置</span>
          </button>
        </div>
      </div>

      {/* Main content: Avatar + Collapsible Messages */}
      <div className="conversation-layout">
        {/* Avatar zone - always visible, centered */}
        <div className="avatar-zone">
          {isListening && (
            <div className="listening-banner floating">
              <div className="listening-dot" />
              <span>正在聆听，请说话...</span>
            </div>
          )}
          {messages.length === 0 && (
            <div className="empty-suggestions">
              <div className="empty-tagline">有什么可以帮你的？</div>
              <div className="suggestion-chips">
                <button className="suggestion-chip" onClick={() => onInputChange("帮我写一段代码")}>帮我写一段代码</button>
                <button className="suggestion-chip" onClick={() => onInputChange("今天天气怎么样")}>今天天气怎么样</button>
                <button className="suggestion-chip" onClick={() => onInputChange("解释一下量子计算")}>解释一下量子计算</button>
              </div>
            </div>
          )}
          <div className={`jarvis-avatar-img ${javisStatus}`} style={{ width: 280, height: 280 }}>
            <img src={jarvisAvatar} alt="JARVIS AI" draggable={false} />
            <div className="avatar-status-ring" />
            <div className="avatar-status-glow" />
          </div>
        </div>

        {/* Collapsible messages panel */}
        <div className={`messages-panel ${showMessages ? 'open' : 'closed'}`}>
          <div className="messages-panel-header">
            <span>对话记录</span>
            <button className="messages-panel-close" onClick={() => setShowMessages(false)} title="收起">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polyline points="6 9 12 15 18 9"/>
              </svg>
            </button>
          </div>
          <div className="messages-scroll" role="log" aria-live="polite" aria-label="对话消息">
            {messages.map((msg, i) => (
              <MessageBubble
                key={i}
                message={msg}
                isStreaming={isStreaming}
                isLastAssistant={i === lastAssistantIdx}
                showPlayBtn={voiceMode !== "off"}
                isPlayingThis={isVoicePlaying && i === lastAssistantIdx}
                onPlayMessage={onPlayMessage}
                onFork={onForkFromMessage}
              />
            ))}
            <div ref={messagesEndRef} />
          </div>
        </div>
      </div>

      {/* Error bar */}
      {error && (
        <div className="error-bar">
          <span>{error}</span>
          <button className="error-dismiss" onClick={onClearError}>
            x
          </button>
        </div>
      )}

      {/* Input area - same as before */}
      <div className="input-area" aria-label="消息输入区域">
        <div className="input-container">
          <textarea
            ref={textareaRef}
            className="chat-textarea"
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入消息..."
            disabled={isStreaming || connectionStatus === "offline"}
            rows={1}
          />
          {(pendingImages.length > 0 || pendingFiles.length > 0) && (
            <div className="attachments-preview">
              {pendingImages.map((img, i) => (
                <div key={i} className="attachment-item image-attachment">
                  <img src={img.image_url.url} alt={img.filename || "图片"} />
                  <button className="attachment-remove" onClick={() => onRemovePendingImage(i)}>x</button>
                </div>
              ))}
              {pendingFiles.map((file, i) => (
                <div key={i} className="attachment-item file-attachment">
                  <span className="file-icon">📄</span>
                  <span className="file-name">{file.filename}</span>
                  <button className="attachment-remove" onClick={() => onRemovePendingFile(i)}>x</button>
                </div>
              ))}
            </div>
          )}
          <div className="input-actions">
            <button
              className="input-action-btn"
              onClick={() => imageInputRef.current?.click()}
              title="上传图片"
              disabled={isStreaming || connectionStatus === "offline"}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/>
                <polyline points="21 15 16 10 5 21"/>
              </svg>
            </button>
            <button
              className="input-action-btn"
              onClick={() => fileInputRef.current?.click()}
              title="上传文件"
              disabled={isStreaming || connectionStatus === "offline"}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
            </button>
            <button
              className={`input-action-btn ${isCapturingRegion ? "active" : ""}`}
              onClick={handleStartRegionScreenshot}
              title="区域截图识别"
              disabled={isStreaming || connectionStatus === "offline" || isCapturingRegion}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <path d="M9 3v18"/>
                <path d="M15 3v18"/>
                <path d="M3 9h18"/>
                <path d="M3 15h18"/>
              </svg>
            </button>
            <input ref={imageInputRef} type="file" accept="image/*" hidden onChange={onImageSelect} />
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.pdf,.md,.csv,.json,.doc,.docx,.xls,.xlsx,application/pdf,text/plain,text/markdown,text/csv,application/json,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              hidden
              onChange={onFileSelect}
            />
            <Suspense fallback={<div style={{ width: 24, height: 24 }} />}>
              <VoiceControl
                voiceMode={voiceMode}
                selectedVoice={selectedVoice}
                onVoiceModeChange={onVoiceModeChange}
                onVoiceSelect={onVoiceSelect}
                onTranscript={onVoiceTranscript}
                onListeningChange={onListeningChange}
                disabled={isStreaming || connectionStatus === "offline"}
              />
            </Suspense>
            {(isStreaming || isVoicePlaying) ? (
              <button className="stop-btn" onClick={onStop} title={isStreaming ? "停止生成" : "停止朗读"}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              </button>
            ) : (
              <button
                className="send-btn"
                onClick={onSend}
                disabled={!input.trim() && pendingImages.length === 0 && pendingFiles.length === 0 || isStreaming || connectionStatus === "offline"}
                title="发送消息"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="22" y1="2" x2="11" y2="13" />
                  <polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            )}
          </div>
        </div>
        <div className="input-hint">
          <span>Enter 发送，Shift+Enter 换行</span>
          <span>{input.length > 0 ? `${input.length} 字` : ""}</span>
        </div>
        {regionToast && (
          <div className="region-toast">
            <span>{regionToast}</span>
          </div>
        )}
      </div>

      {/* Status bar */}
      <div className="status-bar">
        <div className="status-bar-left">
          <div className={`status-bar-dot ${connectionStatus}`} />
          <span>
            {connectionStatus === "online"
              ? "已连接"
              : connectionStatus === "offline"
              ? "已断开"
              : "检测中..."}
          </span>
        </div>
        <div className="status-bar-right">
          {/* Wake word toggle */}
          <button
            className={`wakeword-btn ${wakeWordStatus} ${wakeWordFlash ? 'flash' : ''}`}
            onClick={toggleWakeWord}
            title={wakeWordStatus === 'listening' ? '点击关闭语音唤醒 (Hey Jarvis)' : '点击开启语音唤醒'}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
              <line x1="12" y1="19" x2="12" y2="23"/>
              <line x1="8" y1="23" x2="16" y2="23"/>
            </svg>
            <span className="wakeword-label">
              {wakeWordStatus === 'listening' ? 'Hey JARVIS' : wakeWordStatus === 'detected' ? '唤醒!' : '语音唤醒'}
            </span>
            {wakeWordStatus === 'listening' && (
              <span className="wakeword-pulse" />
            )}
          </button>
          <div className="model-switcher" onClick={() => setModelDropdownOpen(!modelDropdownOpen)}>
            <span className="model-switcher-label">{currentModel || "未设置模型"}</span>
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
            {modelDropdownOpen && (
              <div className="model-switcher-dropdown" onClick={e => e.stopPropagation()}>
                {availableModels.map(m => (
                  <button
                    key={m.id}
                    className={`model-switcher-item ${m.is_active ? 'active' : ''}`}
                    onClick={async () => {
                      setModelDropdownOpen(false);
                      if (!m.is_active && onSwitchModel) {
                        await onSwitchModel(m.id);
                      }
                    }}
                  >
                    {m.name}
                    {m.is_active && <span className="model-switcher-check">&#10003;</span>}
                  </button>
                ))}

              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
