import React, { useCallback, useEffect, useRef, useState, Suspense } from "react";
import type {
  AppConfig,
  ConnectionStatus,
  JavisStatus,
  RightPanelTab,
} from "./lib/types";
import { checkHealth, getConfig, switchModel, uploadImage, uploadFile } from "./lib/api";
import { useTheme } from "./hooks/useTheme";
import { useConversations } from "./hooks/useConversations";
import { useToolCallTracker } from "./hooks/useToolCallTracker";
import { useVoicePipeline } from "./hooks/useVoicePipeline";
import { useChatStream } from "./hooks/useChatStream";
import { useRightPanel } from "./hooks/useRightPanel";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import SkillCenter from "./components/SkillCenter";
import SchedulerPanel from "./components/SchedulerPanel";
import SystemMonitor from "./components/SystemMonitor";
import ApprovalDialog from "./components/ApprovalDialog";
import RightPanel from "./components/RightPanel";

// Lazy-load heavy panels to keep initial bundle small
const SettingsPanel = React.lazy(() => import("./components/SettingsPanel"));

export default function App() {
  const { theme, toggleTheme } = useTheme();

  // === 全局错误条 ===
  const [error, setError] = useState<string | null>(null);
  const showError = useCallback((msg: string) => setError(msg), []);

  // === UI 状态 ===
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [skillCenterOpen, setSkillCenterOpen] = useState(false);
  const [schedulerOpen, setSchedulerOpen] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("checking");
  const [javisStatus, setJavisStatus] = useState<JavisStatus>("idle");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);

  // === 领域状态（已抽成 hooks）===
  const conversations = useConversations(isStreaming, showError);
  const tools = useToolCallTracker();
  const voice = useVoicePipeline(javisStatus, setJavisStatus);
  const rightPanel = useRightPanel();

  // 浏览器打开成功后镜像进右侧面板，并展开面板（看不见的页签等于没有）
  const mirrorWebPage = useCallback(
    (tab: RightPanelTab) => {
      rightPanel.open(tab);
      rightPanel.setCollapsed(false);
    },
    [rightPanel]
  );

  const chat = useChatStream(
    { conversations, tools, voice, onMirrorWebPage: mirrorWebPage },
    setJavisStatus,
    showError,
    setIsStreaming
  );

  // 附件选择框的 ref
  const imageInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // === 健康检查（15s 轮询）===
  useEffect(() => {
    const check = async () => {
      try {
        const ctrl = new AbortController();
        const timeout = setTimeout(() => ctrl.abort(), 3000);
        await checkHealth();
        clearTimeout(timeout);
        setConnectionStatus("online");
      } catch {
        setConnectionStatus("offline");
      }
    };
    check();
    const interval = setInterval(check, 15000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch(() => {
        // 配置加载失败不打断主流程
      });
  }, []);

  // === 全局快捷键 / 划词助手：把查询投递给 chat.sendText ===
  useEffect(() => {
    if (!window.electronAPI?.onFloatingQuery) return;
    return window.electronAPI.onFloatingQuery((query: string) => {
      if (!query.trim() || isStreaming) return;
      void chat.sendText(query.trim());
    });
  }, [isStreaming, chat.sendText]);

  useEffect(() => {
    if (!window.electronAPI?.onSelectionQuery) return;
    return window.electronAPI.onSelectionQuery((query: string) => {
      if (!query.trim() || isStreaming) return;
      void chat.sendText(query.trim());
    });
  }, [isStreaming, chat.sendText]);

  // === 附件上传 ===
  const handleImageSelect = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const result = await uploadImage(file);
        chat.setPendingImages((prev) => [
          ...prev,
          {
            type: "image_url",
            image_url: { url: result.data_url },
            filename: result.filename,
          },
        ]);
      } catch (err: any) {
        showError(`图片上传失败: ${err.message}`);
      }
      e.target.value = "";
    },
    [chat, showError]
  );

  const handleFileSelect = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      try {
        const result = await uploadFile(file);
        chat.setPendingFiles((prev) => [
          ...prev,
          {
            type: "file",
            filename: result.filename,
            mime_type: result.mime_type,
            extracted_text: result.extracted_text,
            data_url: result.data_url,
          },
        ]);
      } catch (err: any) {
        showError(`文件上传失败: ${err.message}`);
      }
      e.target.value = "";
    },
    [chat, showError]
  );

  // === 设置 / 模型 ===
  const handleConfigUpdate = useCallback((newConfig: AppConfig) => {
    setConfig(newConfig);
  }, []);

  const handleSwitchModel = useCallback(
    async (modelId: string) => {
      try {
        const data = await switchModel(modelId);
        setConfig((prev) => (prev ? { ...prev, model_config: data } : prev));
      } catch (e) {
        showError(`切换模型失败: ${e instanceof Error ? e.message : "未知错误"}`);
      }
    },
    [showError]
  );

  const toggleSettings = useCallback(() => setSettingsOpen((v) => !v), []);

  // === 语音转写 → 直接发送 ===
  const handleVoiceTranscript = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      chat.setInput(text);
      void chat.sendText(text.trim());
    },
    [chat]
  );

  // === 技能中心 / 定时任务投递 ===
  const handleSkillSendMessage = useCallback(
    (text: string) => {
      if (!text.trim() || isStreaming) return;
      void chat.sendText(text.trim());
    },
    [isStreaming, chat.sendText]
  );

  return (
    <div className="app-layout">
      <SystemMonitor />

      <Sidebar
        conversations={conversations.conversations}
        activeId={conversations.activeId}
        collapsed={sidebarCollapsed}
        onSelectConversation={(id) => void conversations.select(id)}
        onNewChat={() => void conversations.createNew()}
        onDeleteConversation={(id) => void conversations.remove(id)}
        onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
        onToggleSettings={toggleSettings}
        onOpenSkillCenter={() => setSkillCenterOpen(true)}
        onOpenScheduler={() => setSchedulerOpen(true)}
      />

      <ChatArea
        messages={conversations.messages}
        isStreaming={chat.isStreaming}
        javisStatus={javisStatus}
        connectionStatus={connectionStatus}
        error={error}
        input={chat.input}
        currentModel={config?.model_config?.model || "加载中..."}
        conversationTitle={conversations.getTitle()}
        settingsOpen={settingsOpen}
        onInputChange={chat.setInput}
        onSend={() => void chat.send()}
        onStop={chat.stop}
        onClearError={() => setError(null)}
        onToggleSettings={toggleSettings}
        voiceMode={voice.voiceMode}
        selectedVoice={voice.selectedVoice}
        isVoicePlaying={voice.isPlaying}
        isListening={voice.isListening}
        onVoiceModeChange={voice.setVoiceMode}
        onVoiceSelect={voice.setSelectedVoice}
        onVoiceTranscript={handleVoiceTranscript}
        onListeningChange={voice.setListening}
        onPlayMessage={(content) => void voice.play(content)}
        onStopVoice={voice.stop}
        pendingImages={chat.pendingImages}
        pendingFiles={chat.pendingFiles}
        onImageSelect={handleImageSelect}
        onFileSelect={handleFileSelect}
        onRemovePendingImage={(i) =>
          chat.setPendingImages((prev) => prev.filter((_, idx) => idx !== i))
        }
        onRemovePendingFile={(i) =>
          chat.setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))
        }
        imageInputRef={imageInputRef}
        fileInputRef={fileInputRef}
        onForkFromMessage={(messageId) => void conversations.fork(messageId)}
        onSwitchModel={handleSwitchModel}
        onOpenSkillCenter={() => setSkillCenterOpen(true)}
        onOpenScheduler={() => setSchedulerOpen(true)}
        onOpenInRightPanel={(tab) => {
          rightPanel.open(tab);
          rightPanel.setCollapsed(false);
        }}
      />

      <RightPanel
        tabs={rightPanel.tabs}
        activeTabId={rightPanel.activeTabId}
        onActivateTab={rightPanel.activate}
        onCloseTab={rightPanel.close}
        onOpenExternal={rightPanel.openExternal}
        collapsed={rightPanel.collapsed}
        onToggleCollapse={rightPanel.toggleCollapse}
      />

      {skillCenterOpen && (
        <SkillCenter
          connectionStatus={connectionStatus}
          onSendMessage={handleSkillSendMessage}
          onClose={() => setSkillCenterOpen(false)}
        />
      )}

      {schedulerOpen && <SchedulerPanel onClose={() => setSchedulerOpen(false)} />}

      {settingsOpen && (
        <Suspense fallback={<div style={{ padding: 24, color: "#94a3b8" }}>加载设置中...</div>}>
          <SettingsPanel
            config={config}
            connectionStatus={connectionStatus}
            theme={theme}
            onToggleTheme={toggleTheme}
            onConfigUpdate={handleConfigUpdate}
            onClose={() => setSettingsOpen(false)}
          />
        </Suspense>
      )}

      {tools.pendingApprovals.length > 0 && (
        <ApprovalDialog
          toolCall={tools.pendingApprovals[0]}
          onApprove={tools.approve}
          onReject={tools.reject}
        />
      )}
    </div>
  );
}
