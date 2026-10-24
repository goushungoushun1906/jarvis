import React, { useRef, useEffect, useCallback, useState, useMemo, Suspense } from "react";
import type { Message, JavisStatus, ConnectionStatus, VoiceMode, ImageAttachment, FileAttachment, RightPanelTab } from "../lib/types";
import MessageBubble from "./MessageBubble";
import RegionSelector, { type Region } from "./RegionSelector";
import ParticleAvatar from "./ParticleAvatar";
import { captureRegionScreenshot } from "../lib/api";
import "./ChatArea.css";

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
  /** TTS 播放中的实时音量（0..1），供形象动画每帧读取。 */
  levelRef?: React.MutableRefObject<number>;
  // ====== 语音唤醒 ======
  wakeEnabled: boolean;
  wakePhase: "off" | "listening" | "armed" | "conversation";
  wakeHeard: string;
  wakeError?: string | null;
  onWakeToggle: () => void;
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
  onOpenSkillCenter?: () => void;
  onOpenScheduler?: () => void;
  onOpenInRightPanel?: (tab: RightPanelTab) => void;
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
  levelRef,
  wakeEnabled,
  wakePhase,
  wakeHeard,
  wakeError,
  onWakeToggle,
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
  onOpenSkillCenter,
  onOpenScheduler,
  onOpenInRightPanel,
}: ChatAreaProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const adjustTextareaHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 132)}px`;
  }, []);

  useEffect(() => { adjustTextareaHeight(); }, [input, adjustTextareaHeight]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const lastAssistantIdx = messages.reduce<number>((acc, msg, i) =>
    msg.role === "assistant" ? i : acc, -1);

  const [availableModels, setAvailableModels] = useState<Array<{id:string;name:string;is_active:boolean}>>([]);
  const [modelDropdownOpen, setModelDropdownOpen] = useState(false);

  const HIDDEN_BUILTIN_MODEL_IDS = useMemo(() => new Set([
    "agnes-flash", "gpt4o-mini", "gpt4o", "deepseek", "doubao", "claude"
  ]), []);

  // ---- Region screenshot ----
  const [showRegionSelector, setShowRegionSelector] = useState(false);
  const [isCapturingRegion, setIsCapturingRegion] = useState(false);
  const [regionToast, setRegionToast] = useState<string | null>(null);

  const handleStartRegionScreenshot = async () => {
    if (isStreaming || connectionStatus === "offline") return;
    if (window.electronAPI?.captureRegion) {
      setIsCapturingRegion(true);
      try {
        const region = await window.electronAPI.captureRegion();
        if (region) await handleRegionCaptured(region);
      } catch (err: any) {
        setRegionToast(`区域截图失败: ${err?.message || "未知错误"}`);
        setTimeout(() => setRegionToast(null), 4000);
      } finally { setIsCapturingRegion(false); }
      return;
    }
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

  // Fetch model list
  useEffect(() => {
    const fetchModels = async () => {
      try {
        const resp = await fetch('http://127.0.0.1:18200/api/config/models');
        if (resp.ok) {
          const data = await resp.json();
          setAvailableModels(data.filter((m: {id: string}) => !HIDDEN_BUILTIN_MODEL_IDS.has(m.id)));
        }
      } catch { /* silent */ }
    };
    fetchModels();
    const interval = setInterval(fetchModels, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!modelDropdownOpen) return;
    const handler = () => setModelDropdownOpen(false);
    setTimeout(() => document.addEventListener('click', handler), 0);
    return () => document.removeEventListener('click', handler);
  }, [modelDropdownOpen]);

  const hasMessages = messages.length > 0;

  // 唤醒监听开着就是在听；但说话/思考优先级更高，不该被覆盖
  const avatarState =
    javisStatus === "speaking" || javisStatus === "thinking"
      ? javisStatus
      : wakeEnabled && wakePhase !== "off"
        ? "listening"
        : javisStatus;

  return (
    <div className="main-area">
      {showRegionSelector && (
        <RegionSelector onCapture={handleRegionCaptured} onCancel={() => setShowRegionSelector(false)} />
      )}

      {/* Header — simplified, no breathing indicator, no skill toggle */}
      <div className="chat-header">
        <span className="chat-title">{conversationTitle || "JARVIS"}</span>
        <div className="chat-header-right">
          <button className="header-icon-btn" onClick={onOpenSkillCenter} title="技能中心">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="12 2 2 7 12 12 22 7 12 2"/>
              <polyline points="2 17 12 22 22 17"/>
              <polyline points="2 12 12 17 22 12"/>
            </svg>
          </button>
          <button className="header-icon-btn" onClick={onOpenScheduler} title="定时任务">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/>
              <polyline points="12 6 12 12 16 14"/>
            </svg>
          </button>
          <button className={`header-icon-btn ${settingsOpen ? "active" : ""}`} onClick={onToggleSettings} title="设置">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
          <button className="header-icon-btn" title="更多" onClick={onToggleSettings}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
              <circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/>
            </svg>
          </button>
        </div>
      </div>

      {/* Message feed */}
      <div className={`msg-feed ${hasMessages ? 'has-msgs' : ''}`}>
        {messages.length === 0 ? (
          <div className="empty-state">
            <ParticleAvatar state={avatarState} levelRef={levelRef} />
            <div className="empty-tagline">你好，我是 JARVIS</div>
            <div className="empty-sub">有什么可以帮你的？</div>
            <div className="suggestion-chips">
              <button className="suggestion-chip" onClick={() => onInputChange("帮我写一段代码")}>帮我写一段代码</button>
              <button className="suggestion-chip" onClick={() => onInputChange("今天天气怎么样")}>今天天气怎么样</button>
              <button className="suggestion-chip" onClick={() => onInputChange("解释一下量子计算")}>解释一下量子计算</button>
              <button className="suggestion-chip" onClick={() => onInputChange("帮我规划一个项目")}>帮我规划一个项目</button>
            </div>
          </div>
        ) : (
          <div className="msg-list" role="log" aria-live="polite" aria-label="对话消息">
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
                onOpenInRightPanel={onOpenInRightPanel}
              />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Error bar */}
      {error && (
        <div className="error-bar">
          <span>{error}</span>
          <button className="error-dismiss" onClick={onClearError}>×</button>
        </div>
      )}

      {/* Input area */}
      <div className="input-area">
        {wakeEnabled && (
          <div className={`wake-bar ${wakePhase} ${wakeError ? "has-error" : ""}`}>
            <span className="wake-dot" />
            <span className="wake-text">
              {wakeError
                ? wakeError
                : wakePhase === "armed"
                  ? "已唤醒 — 请说指令"
                  : wakePhase === "conversation"
                    ? "对话中 — 继续说即可，停一会儿自动结束"
                    : "语音唤醒已开启 — 说「你好贾维斯」"}
            </span>
            {!wakeError && wakeHeard && <span className="wake-heard">听到：{wakeHeard}</span>}
          </div>
        )}
        <div className="input-card">
          <textarea
            ref={textareaRef}
            className="chat-textarea"
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="发消息或创建任务… / @ 选择技能"
            disabled={isStreaming || connectionStatus === "offline"}
            rows={1}
          />
          {(pendingImages.length > 0 || pendingFiles.length > 0) && (
            <div className="attachments-preview">
              {pendingImages.map((img, i) => (
                <div key={i} className="attachment-item image-attachment">
                  <img src={img.image_url.url} alt={img.filename || "图片"} />
                  <button className="attachment-remove" onClick={() => onRemovePendingImage(i)}>×</button>
                </div>
              ))}
              {pendingFiles.map((file, i) => (
                <div key={i} className="attachment-item file-attachment">
                  <span className="file-icon">📄</span>
                  <span className="file-name">{file.filename}</span>
                  <button className="attachment-remove" onClick={() => onRemovePendingFile(i)}>×</button>
                </div>
              ))}
            </div>
          )}
          <div className="input-row">
            <div className="input-actions-left">
              <button className="input-icon-btn" onClick={() => imageInputRef.current?.click()} title="图片" disabled={isStreaming || connectionStatus === "offline"}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>
                </svg>
              </button>
              <button className="input-icon-btn" onClick={() => fileInputRef.current?.click()} title="文件" disabled={isStreaming || connectionStatus === "offline"}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>
                </svg>
              </button>
              <button
                className={`input-icon-btn ${isCapturingRegion ? "active" : ""}`}
                onClick={handleStartRegionScreenshot}
                title="截图识文"
                disabled={isStreaming || connectionStatus === "offline" || isCapturingRegion}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>
                </svg>
              </button>
              <input ref={imageInputRef} type="file" accept="image/*" hidden onChange={onImageSelect} />
              <input ref={fileInputRef} type="file" accept=".txt,.pdf,.md,.csv,.json,.doc,.docx,.xls,.xlsx,.application/pdf" hidden onChange={onFileSelect} />
              {/* 语音唤醒：开启后持续监听，说「你好贾维斯」即可下指令 */}
              <button
                className={`input-icon-btn wake-btn ${wakeEnabled ? "active" : ""} ${wakePhase}`}
                onClick={onWakeToggle}
                disabled={connectionStatus === "offline"}
                title={
                  wakeEnabled
                    ? wakePhase === "armed"
                      ? "已唤醒 — 说指令（点击关闭监听）"
                      : wakePhase === "conversation"
                        ? "对话中 — 继续说即可（点击关闭监听）"
                        : "正在监听唤醒词「你好贾维斯」（点击关闭）"
                    : "开启语音唤醒：说「你好贾维斯」即可对话"
                }
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
                  <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
                </svg>
              </button>
            </div>
            <Suspense fallback={<div style={{width:32,height:32}}/>}>
              <VoiceControl
                voiceMode={voiceMode} selectedVoice={selectedVoice}
                onVoiceModeChange={onVoiceModeChange} onVoiceSelect={onVoiceSelect}
                onTranscript={onVoiceTranscript} onListeningChange={onListeningChange}
                disabled={isStreaming || connectionStatus === "offline" || wakeEnabled}
              />
            </Suspense>
            <div className="input-actions-right">
              {(isStreaming || isVoicePlaying) ? (
                <button className="stop-btn" onClick={onStop} title="停止">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
                </button>
              ) : (
                <button className="send-btn" onClick={onSend}
                  disabled={!input.trim() && pendingImages.length === 0 && pendingFiles.length === 0 || isStreaming || connectionStatus === "offline"}
                  title="发送">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Quick actions row */}
        <div className="input-quick-actions">
          <button className="quick-action-btn" onClick={() => {}} title="调用视觉 Agent 控制桌面">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
            </svg>
            <span>云电脑</span>
          </button>
          <button className="quick-action-btn" onClick={() => {}} title="打开工作空间">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            <span>项目</span>
          </button>
          <button className="quick-action-btn" onClick={() => onOpenSkillCenter?.()} title="技能中心">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>
            </svg>
            <span>技能</span>
          </button>
          <button className="quick-action-btn" onClick={() => onOpenScheduler?.()} title="定时任务">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>
            </svg>
            <span>定时任务</span>
          </button>
          <button className="quick-action-btn" onClick={() => onToggleSettings?.()} title="插件与连接器">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51"/>
            </svg>
            <span>连接器</span>
          </button>
        </div>

        <div className="input-hint">
          <span>Enter 发送 · Shift+Enter 换行</span>
          {input.length > 0 && <span>{input.length} 字</span>}
        </div>
        {regionToast && <div className="region-toast"><span>{regionToast}</span></div>}
      </div>

      {/* Status bar */}
      <div className="status-bar">
        <div className="status-bar-left">
          <div className={`status-dot ${connectionStatus}`}/>
          <span>{connectionStatus === "online" ? "已连接" : connectionStatus === "offline" ? "已断开" : "检测中…"}</span>
        </div>
        <div className="status-bar-right">
          <div className="model-pill" onClick={() => setModelDropdownOpen(v => !v)}>
            <span className="model-pill-label">{currentModel || "未设置模型"}</span>
            <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
              <polyline points="6 9 12 15 18 9"/>
            </svg>
            {modelDropdownOpen && (
              <div className="model-dropdown" onClick={e => e.stopPropagation()}>
                {availableModels.map(m => (
                  <button key={m.id} className={`model-item ${m.is_active ? 'active' : ''}`}
                    onClick={async () => { setModelDropdownOpen(false); if (!m.is_active && onSwitchModel) await onSwitchModel(m.id); }}>
                    {m.name}{m.is_active && <span className="model-check">✓</span>}
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
