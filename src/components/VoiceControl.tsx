import { useState, useEffect, useRef, useCallback } from "react";
import type { VoiceInfo, VoiceMode } from "../lib/types";
import {
  getVoices,
  serverRecordStart,
  serverRecordStop,
  micCheck,
  type MicCheckResult,
} from "../lib/api";

interface VoiceControlProps {
  voiceMode: VoiceMode;
  selectedVoice: string;
  onVoiceModeChange: (mode: VoiceMode) => void;
  onVoiceSelect: (voiceId: string) => void;
  onTranscript: (text: string) => void;
  onListeningChange?: (listening: boolean) => void;
  disabled?: boolean;
}

const MODE_CYCLE: VoiceMode[] = ["off", "auto", "manual"];
const MODE_LABELS: Record<VoiceMode, string> = {
  off: "语音关闭",
  auto: "自动朗读",
  manual: "手动朗读",
};

export default function VoiceControl({
  voiceMode,
  selectedVoice,
  onVoiceModeChange,
  onVoiceSelect,
  onTranscript,
  onListeningChange,
  disabled = false,
}: VoiceControlProps) {
  const [voices, setVoices] = useState<VoiceInfo[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [voicesLoaded, setVoicesLoaded] = useState(false);
  const [sttError, setSttError] = useState<string | null>(null);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [micChecking, setMicChecking] = useState(false);
  const [micResult, setMicResult] = useState<MicCheckResult | null>(null);
  const [micCheckError, setMicCheckError] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Load voices list
  useEffect(() => {
    if (!voicesLoaded) {
      getVoices()
        .then((v) => {
          setVoices(v);
          setVoicesLoaded(true);
        })
        .catch(() => {
          // Voice API not available — that's fine
          setVoicesLoaded(true);
        });
    }
  }, [voicesLoaded]);

  // Notify parent of listening state changes
  useEffect(() => {
    onListeningChange?.(isRecording || isTranscribing);
  }, [isRecording, isTranscribing, onListeningChange]);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleMicToggle = useCallback(async () => {
    if (disabled) return;
    setSttError(null);

    if (isRecording) {
      // Stop recording + transcribe on server
      setIsRecording(false);
      setIsTranscribing(true);
      try {
        const result = await serverRecordStop();
        if (result.text?.trim()) {
          onTranscript(result.text.trim());
        } else if (result.error) {
          // 后端已经区分了"麦克风没声音"和"有声音但没听清"，直接转述；
          // 以前这里静默吞掉，用户只能看到"点了一下没反应"
          setSttError(result.error);
        } else {
          setSttError("没听清，请再说一次");
        }
      } catch {
        setSttError("语音识别失败");
      } finally {
        setIsTranscribing(false);
      }
    } else {
      // Start server-side recording
      try {
        setIsRecording(true);
        await serverRecordStart();
      } catch {
        setIsRecording(false);
        setSttError("无法启动录音（请确认后端有麦克风访问权限）");
      }
    }
  }, [disabled, isRecording, onTranscript]);

  const handleVoiceModeClick = useCallback(() => {
    const idx = MODE_CYCLE.indexOf(voiceMode);
    const next = MODE_CYCLE[(idx + 1) % MODE_CYCLE.length];
    onVoiceModeChange(next);
  }, [voiceMode, onVoiceModeChange]);

  // "说了话没反应"在用户侧有三种分不清的原因：系统没放行麦克风、默认
  // 输入设备选错、模型没打进包。自检把这三样一次测出来，省得靠猜。
  const handleMicCheck = useCallback(async () => {
    setMicChecking(true);
    setMicResult(null);
    try {
      setMicResult(await micCheck(2));
    } catch (e) {
      setMicCheckError(e instanceof Error ? e.message : "检测失败");
    } finally {
      setMicChecking(false);
    }
  }, []);

  const micTitle = isRecording
    ? "点击停止录音"
    : isTranscribing
      ? "正在识别中..."
      : "点击开始语音输入";

  const displayError = sttError;

  return (
    <div className="voice-control">
      {/* Mic button */}
      <button
        className={`mic-btn ${isRecording ? "recording" : ""}`}
        onClick={handleMicToggle}
        disabled={disabled || isTranscribing}
        title={micTitle}
      >
        {isRecording ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            {/* Microphone with recording indicator */}
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2H3v2a9 9 0 0 0 8 8.94V23h2v-2.06A9 9 0 0 0 21 12v-2h-2z" />
          </svg>
        ) : isTranscribing ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            {/* Processing/spinning indicator */}
            <circle cx="12" cy="12" r="10" opacity="0.3" />
            <path d="M12 2a10 10 0 0 1 10 10" fill="none" stroke="currentColor" strokeWidth="2.5" />
          </svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" stroke="none" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
        )}
      </button>

      {/* Waveform + listening indicator (visible when recording or transcribing) */}
      {(isRecording || isTranscribing) && (
        <div className="voice-waveform-wrapper">
          <div className="voice-listening-text">
            {isRecording ? "正在聆听..." : "正在识别..."}
          </div>
          <div className="voice-waveform">
            <span className="wave-bar" style={{ animationDelay: "0s" }} />
            <span className="wave-bar" style={{ animationDelay: "0.15s" }} />
            <span className="wave-bar" style={{ animationDelay: "0.3s" }} />
            <span className="wave-bar" style={{ animationDelay: "0.45s" }} />
            <span className="wave-bar" style={{ animationDelay: "0.6s" }} />
          </div>
        </div>
      )}

      {/* Voice mode toggle (speaker icon) */}
      <button
        className={`voice-mode-btn ${voiceMode}`}
        onClick={handleVoiceModeClick}
        title={MODE_LABELS[voiceMode]}
      >
        {voiceMode === "off" ? (
          /* Muted speaker */
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" stroke="none" />
            <line x1="23" y1="9" x2="17" y2="15" />
            <line x1="17" y1="9" x2="23" y2="15" />
          </svg>
        ) : voiceMode === "auto" ? (
          /* Speaker with sound waves */
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" stroke="none" />
            <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
            <path d="M15.54 8.46a5 5 0 0 1 0 7.08" />
          </svg>
        ) : (
          /* Plain speaker */
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor" stroke="none" />
          </svg>
        )}
      </button>

      {/* Voice settings dropdown */}
      <div className="voice-settings-wrapper" ref={dropdownRef}>
        <button
          className="voice-settings-btn"
          onClick={() => setDropdownOpen((v) => !v)}
          title="语音设置"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>

        {dropdownOpen && (
          <div className="voice-dropdown">
            <div className="voice-dropdown-header">语音设置</div>

            {/* Voice selector */}
            <div className="voice-dropdown-field">
              <label className="voice-dropdown-label">语音</label>
              <select
                className="voice-dropdown-select"
                value={selectedVoice}
                onChange={(e) => onVoiceSelect(e.target.value)}
              >
                {voices.length > 0 ? (
                  voices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.desc}
                    </option>
                  ))
                ) : (
                  <option value="">默认语音</option>
                )}
              </select>
            </div>

            {/* 朗读模式选择器 */}
            <div className="voice-dropdown-field">
              <label className="voice-dropdown-label">朗读模式</label>
              <div className="voice-mode-options">
                {(["off", "auto", "manual"] as VoiceMode[]).map((mode) => (
                  <button
                    key={mode}
                    className={`voice-mode-option ${mode === voiceMode ? "active" : ""}`}
                    onClick={() => {
                      onVoiceModeChange(mode);
                      setDropdownOpen(false);
                    }}
                  >
                    {MODE_LABELS[mode]}
                  </button>
                ))}
              </div>
            </div>

            {/* 麦克风自检 */}
            <div className="voice-dropdown-field">
              <label className="voice-dropdown-label">麦克风自检</label>
              <button
                className="voice-dropdown-check-btn"
                onClick={handleMicCheck}
                disabled={micChecking || isRecording}
              >
                {micChecking ? "正在检测（请说话）..." : "检测麦克风"}
              </button>

              {micCheckError && (
                <div className="voice-check-result bad">{micCheckError}</div>
              )}

              {micResult && (
                <div className="voice-check-result">
                  <div className="voice-check-row">
                    <span>输入设备</span>
                    <b>{micResult.device?.name ?? "未知"}</b>
                  </div>
                  <div className="voice-check-row">
                    <span>收到声音</span>
                    <b className={micResult.heard_sound ? "ok" : "bad"}>
                      {micResult.heard_sound ? "是" : "否（全静音）"}
                    </b>
                  </div>
                  <div className="voice-check-row">
                    <span>识别模型</span>
                    <b className={micResult.model_ready ? "ok" : "bad"}>
                      {micResult.model_ready ? "已就绪" : "缺失"}
                    </b>
                  </div>
                  <div className="voice-check-meter">
                    <i style={{ width: `${Math.max(2, micResult.level_pct)}%` }} />
                  </div>
                  <div className="voice-check-hint">
                    {micResult.heard_sound
                      ? "麦克风正常，可直接语音输入"
                      : "系统可能未允许桌面应用访问麦克风，或默认输入设备不是你的麦克风"}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Error tooltip */}
      {displayError && (
        <div className="voice-error-tip">{displayError}</div>
      )}

      {/* Debug panel */}
      {(isRecording || displayError) && (
        <div style={{
          position: "absolute", bottom: "100%", left: 0, right: 0,
          background: "rgba(0,0,0,0.85)", color: "#00ff88",
          padding: "6px 10px", borderRadius: "8px 8px 0 0",
          fontSize: "11px", fontFamily: "monospace", marginBottom: "4px",
          pointerEvents: "none", whiteSpace: "pre-wrap",
        }}>
          {isRecording ? "[服务器端录音中...]" : null}
          {displayError ? `\n[错误] ${displayError}` : null}
        </div>
      )}
    </div>
  );
}
