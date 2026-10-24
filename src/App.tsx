import React, { useCallback, useEffect, useMemo, useRef, useState, Suspense } from "react";
import type {
  AppConfig,
  ConnectionStatus,
  JavisStatus,
  RightPanelTab,
} from "./lib/types";
import {
  checkHealth,
  getConfig,
  switchModel,
  uploadImage,
  uploadFile,
  serverRecordStart,
  serverRecordStop,
  suspendWakeword,
  resumeWakeword,
} from "./lib/api";
import { useTheme } from "./hooks/useTheme";
import { useConversations } from "./hooks/useConversations";
import { useToolCallTracker } from "./hooks/useToolCallTracker";
import { useVoicePipeline } from "./hooks/useVoicePipeline";
import { useWakeWord } from "./hooks/useWakeWord";
import { useChatStream } from "./hooks/useChatStream";
import { useRightPanel } from "./hooks/useRightPanel";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
// ====== C 方案「双态融合」HUD（opt-in，不影响现有豆包浅色风 UI）======
import JarvisHud, { HudToggle, type HudMessage } from "./components/hud/JarvisHud";
import { useMotionRefs, createWakeGuard } from "./components/hud/motionRefs";
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

  // === 语音唤醒 ===
  //
  // 唤醒监听必须知道"现在能不能听"：TTS 在放、或流式回答还没结束的时候都得
  // 闭嘴，否则麦克风会录到贾维斯自己的声音，并被当成下一条指令。
  // 这两个值变化很频繁，用 ref 传进循环，避免每次都重建监听循环。
  // 语音唤醒**默认开启**：换成专用关键词引擎之后，唤醒就是贾维斯的主入口，
  // 对标手机助手「开箱即喊」，不该要求用户每次先去点一下开关。
  // 关掉过就记住（localStorage 写 "0"），下次启动尊重用户的选择。
  const [wakeEnabled, setWakeEnabled] = useState<boolean>(
    () => window.localStorage.getItem("jarvis.wake") !== "0"
  );
  useEffect(() => {
    window.localStorage.setItem("jarvis.wake", wakeEnabled ? "1" : "0");
  }, [wakeEnabled]);
  const isPlayingRef = useRef(false);
  const busyRef = useRef(false);
  // 按住 Space 说话期间挂起唤醒监听：后端录音是全局单例，两边会互相踩
  const pttRef = useRef(false);

  // 防自激守卫：播报被打断后的一段时间内不再判定唤醒。
  // 用 600ms 而不是 motionRefs 默认的 400ms —— 笔记本内置麦克风离扬声器很近，
  // 除了 TTS 尾音还要算上扬声器外壳传导的回声，余量给足一点。
  const wakeGuard = useMemo(() => createWakeGuard(600), []);

  useEffect(() => {
    // 用 speaking 而不是 isPlaying：后者要等音频字节到了浏览器才置位，
    // 中间那 1~2 秒合成空档里它仍是 false，唤醒引擎就会把紧随其后的
    // 播报声听成唤醒词（自激）。speaking 覆盖"合成中 + 播放中"全程。
    isPlayingRef.current = voice.isPlaying;
    if (voice.speaking) {
      wakeGuard.suspend();
      if (wakeEnabled) void suspendWakeword().catch(() => {});
    } else {
      wakeGuard.releaseAfterInterrupt();
      if (wakeEnabled) void resumeWakeword().catch(() => {});
    }
    // 这里不像旧实现那样"整轮跳过监听"：引擎照常在跑，只是暂停判定，
    // 播报一停立刻就能响应，不会丢掉用户紧跟着喊的那句话。
  }, [voice.speaking, voice.isPlaying, wakeEnabled, wakeGuard]);
  useEffect(() => {
    busyRef.current = chat.isStreaming;
  }, [chat.isStreaming]);

  const handleWakeCommand = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      // 上一轮回答还没走完时，旧代码是直接 return —— 用户侧看到的就是
      // "我说了话，界面毫无反应"，而且分不清是没听清、还是模型还在想。
      // 这种情况必须给个明确反馈。
      if (chat.isStreaming) {
        void voice.play("上一个问题我还在回答，请稍等一下。");
        return;
      }
      void chat.sendText(text.trim());
    },
    [chat, voice]
  );

  const wake = useWakeWord({
    enabled: wakeEnabled,
    onCommand: handleWakeCommand,
    isPlayingRef,
    speakingRef: voice.speakingRef,
    busyRef,
    suspendedRef: pttRef,
    // 命中唤醒词 = 用户要插嘴，先把正在念的停掉
    onWake: () => voice.stop(),
    // 守卫此前全项目无调用点，TTS 自激死循环因此没有防线
    canWake: () => wakeGuard.canWake(),
  });

  // === 按住 Space 说话（PTT）：按下开录，松开识别并发送 ===
  const handlePttStart = useCallback(() => {
    pttRef.current = true;
    void serverRecordStart().catch(() => {
      pttRef.current = false;
    });
  }, []);

  const handlePttStop = useCallback(() => {
    pttRef.current = false;
    void (async () => {
      try {
        const r = await serverRecordStop();
        const text = (r.text || "").trim();
        if (text) {
          chat.setInput(text);
          void chat.sendText(text);
        }
      } catch {
        // 没听清就算了，不打断用户
      }
    })();
  }, [chat]);

  // === HUD 模式开关（C 方案「双态融合」）===
  //
  // **默认开启**：C 方案就是贾维斯的主界面，不是藏在角落里的彩蛋。
  // 只有用户显式退出过（localStorage 写了 "0"）才回到原来的豆包浅色风，
  // 那样既能用全套面板（右侧面板 / 附件 / 会话管理），也不会丢功能。
  // 切换只影响「看哪套 UI」，不动任何现有组件与主题文件。
  const [hudMode, setHudMode] = useState<boolean>(
    () => window.localStorage.getItem("jarvis.hud") !== "0"
  );
  const hudMotion = useMotionRefs();
  useEffect(() => {
    window.localStorage.setItem("jarvis.hud", hudMode ? "1" : "0");
  }, [hudMode]);

  // speaking 双阶段（规范 §10 高危项 2 的解法）：
  // SSE done 到 TTS 起播之间有 200–800ms 空档，若只看 javisStatus 会 idle→speaking 闪一下。
  // 这里把 phase 拆成 stream / tts / null 三态，空档期 status 仍是 speaking、phase=null，
  // HUD 显示 SYNTHESIZING 并用合成包络维持"在播报"的观感，绝不闪回 idle。
  useEffect(() => {
    hudMotion.phaseRef.current =
      javisStatus !== "speaking"
        ? null
        : voice.isPlaying
          ? "tts"
          : chat.isStreaming
            ? "stream"
            : null;
  }, [javisStatus, voice.isPlaying, chat.isStreaming, hudMotion.phaseRef]);

  // HUD 显示用的状态。
  // ⚠️ 关键点：项目里从来没有人 setJavisStatus("listening")，唤醒链路的
  // listening/armed 只是 useWakeWord 内部的 WakePhase（两套状态），所以必须在这里派生。
  // 否则 JarvisHud 的 micActive（= status === "listening"）永远为假，
  // 真实麦克风不会启动，"说话越大核心反应越明显"那一环就是死的。
  const hudStatus: JavisStatus = useMemo(() => {
    if (connectionStatus === "offline") return "offline";
    if (
      wakeEnabled &&
      javisStatus === "idle" &&
      (wake.phase === "listening" ||
        wake.phase === "armed" ||
        wake.phase === "conversation")
    ) {
      return "listening";
    }
    return javisStatus;
  }, [connectionStatus, wakeEnabled, javisStatus, wake.phase]);

  const hudMessages = useMemo<HudMessage[]>(
    () =>
      conversations.messages.flatMap((m) =>
        m.role === "user" || m.role === "assistant" ? [{ role: m.role, text: m.content }] : []
      ),
    [conversations.messages]
  );

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

  // === 语音唤醒兜底快捷键 ===
  //
  // 关键词引擎可能因为缺密钥、模型没打进安装包、或麦克风被系统独占而完全
  // 起不来。那种情况下用户如果连"手动开麦"的入口都没有，语音功能就等于
  // 彻底死掉 —— 所以这两个快捷键是兜底的底线，不是锦上添花。
  useEffect(() => {
    if (!window.electronAPI?.onWakeToggle) return;
    return window.electronAPI.onWakeToggle(() => {
      setWakeEnabled((v) => !v);
    });
  }, []);

  useEffect(() => {
    const onStart = window.electronAPI?.onWakePttStart;
    const onStop = window.electronAPI?.onWakePttStop;
    if (!onStart || !onStop) return;
    const offStart = onStart(() => handlePttStart());
    const offStop = onStop(() => handlePttStop());
    return () => {
      offStart();
      offStop();
    };
  }, [handlePttStart, handlePttStop]);

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
        levelRef={voice.levelRef}
        wakeEnabled={wakeEnabled}
        wakePhase={wake.phase}
        wakeHeard={wake.lastHeard}
        wakeError={wake.error}
        onWakeToggle={() => setWakeEnabled((v) => !v)}
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

      {/* ====== C 方案「双态融合」HUD：opt-in 浮层，关掉时零副作用 ====== */}
      {hudMode ? (
        <JarvisHud
          status={hudStatus}
          phaseRef={hudMotion.phaseRef}
          levelRef={voice.levelRef}
          bandsRef={hudMotion.bandsRef}
          messages={hudMessages}
          onSend={(text) => {
            chat.setInput(text);
            void chat.sendText(text);
          }}
          onInterrupt={() => {
            // 打断：先停 TTS 再停流；HUD 内部会做 400ms 唤醒 cooldown 防自激
            voice.stop();
            chat.stop();
          }}
          onExit={() => setHudMode(false)}
          onVoiceStart={handlePttStart}
          onVoiceStop={handlePttStop}
          wakeEnabled={wakeEnabled}
          wakePhase={wake.phase}
          onWakeToggle={() => setWakeEnabled((v) => !v)}
          onOpenPanel={(panel) => {
            // HUD 里放不下设置/会话管理，这些在豆包风主界面里：先退出再打开
            setHudMode(false);
            if (panel === "settings") setSettingsOpen(true);
            else void conversations.createNew();
          }}
        />
      ) : (
        <HudToggle onClick={() => setHudMode(true)} />
      )}
    </div>
  );
}
