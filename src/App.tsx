import React, { useState, useEffect, useCallback, useRef, Suspense } from "react";
import type {
  Conversation,
  Message,
  AppConfig,
  ConnectionStatus,
  JavisStatus,
  VoiceMode,
  ImageAttachment,
  FileAttachment,
} from "./lib/types";
import {
  checkHealth,
  listConversations,
  getConversation,
  createConversation,
  deleteConversation,
  getConfig,
  streamChat,
  synthesizeSpeech,
  uploadImage,
  uploadFile,
  forkConversation,
  switchModel,
  updateConversationTitle,
} from "./lib/api";
import { stripMarkdown } from "./lib/stripMarkdown";
import useAudioPlayer from "./hooks/useAudioPlayer";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import SkillCenter from "./components/SkillCenter";

import SystemMonitor from "./components/SystemMonitor";

// Lazy-load heavy panels to keep initial bundle small
const SettingsPanel = React.lazy(() => import("./components/SettingsPanel"));

export default function App() {
  // === Core state ===
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvoId, setActiveConvoId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // === UI state ===
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [skillCenterOpen, setSkillCenterOpen] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("checking");
  const [javisStatus, setJavisStatus] = useState<JavisStatus>("idle");
  const [config, setConfig] = useState<AppConfig | null>(null);

  // === Voice state ===
  const [voiceMode, setVoiceMode] = useState<VoiceMode>("auto");
  const [selectedVoice, setSelectedVoice] = useState<string>("");
  const [isListening, setIsListening] = useState(false);
  const { play: playAudio, stop: stopAudio, isPlaying } = useAudioPlayer();
  const lastPlayedContentRef = useRef<string>("");

  // === Multimodal state ===
  const [pendingImages, setPendingImages] = useState<ImageAttachment[]>([]);
  const [pendingFiles, setPendingFiles] = useState<FileAttachment[]>([]);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // === Refs ===
  const abortRef = useRef<AbortController | null>(null);
  const activeConvoIdRef = useRef<string | null>(null);
  const messagesRef = useRef<Message[]>([]);
  const selectedVoiceRef = useRef<string>("");
  const voiceModeRef = useRef<VoiceMode>("auto");

  // Keep refs in sync with state
  useEffect(() => { messagesRef.current = messages; }, [messages]);
  useEffect(() => { selectedVoiceRef.current = selectedVoice; }, [selectedVoice]);
  useEffect(() => { voiceModeRef.current = voiceMode; }, [voiceMode]);

  // === Health check ===
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

  // === Load config ===
  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch(() => {
        // Silently ignore config load errors
      });
  }, []);

  // === Load conversation list ===
  useEffect(() => {
    listConversations()
      .then(setConversations)
      .catch(() => {
        // Ignore on init
      });
  }, []);

  // === Listen for floating window queries (global shortcut) ===
  useEffect(() => {
    if (!window.electronAPI?.onFloatingQuery) return;
    const cleanup = window.electronAPI.onFloatingQuery((query: string) => {
      if (!query.trim() || isStreaming) return;
      sendVoiceMessageRef.current(query.trim());
    });
    return cleanup;
  }, [isStreaming]);

  // === Listen for selection assistant queries ===
  useEffect(() => {
    if (!window.electronAPI?.onSelectionQuery) return;
    const cleanup = window.electronAPI.onSelectionQuery((query: string) => {
      if (!query.trim() || isStreaming) return;
      sendVoiceMessageRef.current(query.trim());
    });
    return cleanup;
  }, [isStreaming]);

  // === Get display title for current conversation ===
  const getConversationTitle = useCallback((): string => {
    if (!activeConvoId) return "JARVIS";
    const convo = conversations.find((c) => c.id === activeConvoId);
    if (!convo) return "JARVIS";
    if (convo.title) return convo.title;
    const firstUser = messages.find((m) => m.role === "user");
    if (firstUser) {
      return firstUser.content.slice(0, 30) + (firstUser.content.length > 30 ? "..." : "");
    }
    return "新对话";
  }, [activeConvoId, conversations, messages]);

  // === Select conversation ===
  const handleSelectConversation = useCallback(async (id: string) => {
    if (isStreaming) return;
    setActiveConvoId(id);
    activeConvoIdRef.current = id;
    setError(null);
    try {
      const convo = await getConversation(id);
      setMessages(convo.messages || []);
    } catch (e) {
      setError(`加载对话失败: ${e instanceof Error ? e.message : "未知错误"}`);
      setMessages([]);
    }
  }, [isStreaming]);

  // === New chat ===
  const handleNewChat = useCallback(async () => {
    if (isStreaming) return;
    setError(null);
    try {
      const convo = await createConversation();
      setConversations((prev) => [convo, ...prev]);
      setActiveConvoId(convo.id);
      activeConvoIdRef.current = convo.id;
      setMessages([]);
      setInput("");
      setJavisStatus("idle");
    } catch (e) {
      setError(`创建对话失败: ${e instanceof Error ? e.message : "未知错误"}`);
    }
  }, [isStreaming]);

  // === Delete conversation ===
  const deletingRef = useRef<string | null>(null);
  const handleDeleteConversation = useCallback(
    async (id: string) => {
      if (isStreaming) return;
      if (deletingRef.current === id) return; // prevent duplicate deletes
      deletingRef.current = id;
      try {
        await deleteConversation(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeConvoId === id) {
          setActiveConvoId(null);
          activeConvoIdRef.current = null;
          setMessages([]);
        }
      } catch (e) {
        setError(`删除对话失败: ${e instanceof Error ? e.message : "未知错误"}`);
      } finally {
        deletingRef.current = null;
      }
    },
    [isStreaming, activeConvoId]
  );

  // === Image/File upload handlers ===
  const handleImageSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const result = await uploadImage(file);
      setPendingImages(prev => [...prev, {
        type: "image_url",
        image_url: { url: result.data_url },
        filename: result.filename,
      }]);
    } catch (err: any) {
      setError(`图片上传失败: ${err.message}`);
    }
    e.target.value = "";
  }, []);

  const handleFileSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const result = await uploadFile(file);
      setPendingFiles(prev => [...prev, {
        type: "file",
        filename: result.filename,
        mime_type: result.mime_type,
        extracted_text: result.extracted_text,
      }]);
    } catch (err: any) {
      setError(`文件上传失败: ${err.message}`);
    }
    e.target.value = "";
  }, []);

  // === Fork conversation handler ===
  const handleForkConversation = useCallback(async (messageId: number) => {
    if (!activeConvoId || isStreaming) return;
    try {
      const newConvo = await forkConversation(activeConvoId, messageId);
      setConversations(prev => [newConvo, ...prev]);
      setActiveConvoId(newConvo.id);
      activeConvoIdRef.current = newConvo.id;
      const fullConvo = await getConversation(newConvo.id);
      setMessages(fullConvo.messages || []);
    } catch (e: any) {
      setError(`分叉对话失败: ${e.message}`);
    }
  }, [activeConvoId, isStreaming]);

  // === Send message ===
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if ((!text && pendingImages.length === 0 && pendingFiles.length === 0) || isStreaming) return;

    setError(null);

    // Stop any ongoing voice playback before sending new message
    if (isPlaying) {
      stopAudio();
    }

    // If no active conversation, create one first
    let convoId = activeConvoIdRef.current || activeConvoId;
    let isNewConvo = false;
    if (!convoId) {
      try {
        const convo = await createConversation();
        setConversations((prev) => [convo, ...prev]);
        setActiveConvoId(convo.id);
        activeConvoIdRef.current = convo.id;
        convoId = convo.id;
        isNewConvo = true;
      } catch (e) {
        setError(`创建对话失败: ${e instanceof Error ? e.message : "未知错误"}`);
        return;
      }
    }

    // Auto-generate title from first user message
    if (isNewConvo && text) {
      const autoTitle = text.length > 30 ? text.slice(0, 30) + "..." : text;
      updateConversationTitle(convoId, autoTitle).then(() => {
        setConversations((prev) =>
          prev.map((c) => (c.id === convoId ? { ...c, title: autoTitle } : c))
        );
      }).catch(() => {
        // Silently ignore title update failures
      });
    }

    const userMsg: Message = {
      role: "user",
      content: text,
      images: pendingImages.length > 0 ? [...pendingImages] : undefined,
      files: pendingFiles.length > 0 ? [...pendingFiles] : undefined,
    };
    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setPendingImages([]);
    setPendingFiles([]);

    // Add empty assistant message for streaming
    const assistantMsg: Message = { role: "assistant", content: "" };
    setMessages([...newMessages, assistantMsg]);
    setIsStreaming(true);
    setJavisStatus("thinking");

    abortRef.current = new AbortController();

    try {
      let accumulated = "";

      await streamChat(
        {
          messages: newMessages,
          stream: true,
          conversation_id: convoId,
        },
        abortRef.current.signal,
        (content) => {
          // onChunk
          accumulated += content;
          setJavisStatus("speaking");
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              content: accumulated,
            };
            return updated;
          });
        },
        (err) => {
          // onError
          setError(err);
          setJavisStatus("error");
        },
        () => {
          // onDone
          // Auto-TTS: synthesize the full accumulated response
          if (voiceMode === "auto" && accumulated.trim()) {
            synthesizeSpeech(stripMarkdown(accumulated), selectedVoice || undefined)
              .then((blob) => {
                lastPlayedContentRef.current = accumulated;
                playAudio(blob);
                setJavisStatus("speaking");
              })
              .catch(() => {
                // TTS failed, stay idle
                setJavisStatus("idle");
              });
          } else {
            setJavisStatus("idle");
          }
        }
      );
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") {
        // User cancelled
        setJavisStatus("idle");
      } else {
        const msg = err instanceof Error ? err.message : "未知错误";
        setError(msg);
        setJavisStatus("error");
        // Remove empty assistant message if no content was streamed
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last.role === "assistant" && !last.content) {
            return prev.slice(0, -1);
          }
          return prev;
        });
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;

      // Refresh conversation list to update titles
      try {
        const convos = await listConversations();
        setConversations(convos);
      } catch {
        // ignore
      }
    }
  }, [input, messages, isStreaming, activeConvoId, voiceMode, playAudio, selectedVoice, isPlaying, stopAudio, pendingImages, pendingFiles]);

  // === Stop streaming + voice playback ===
  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    stopAudio();
    setJavisStatus("idle");
  }, [stopAudio]);

  // === Voice: synthesize and play AI response ===
  const handlePlayMessage = useCallback(async (content: string) => {
    try {
      const blob = await synthesizeSpeech(stripMarkdown(content), selectedVoice || undefined);
      lastPlayedContentRef.current = content;
      playAudio(blob);
      setJavisStatus("speaking");
    } catch {
      // TTS failed silently
    }
  }, [selectedVoice, playAudio]);

  // === Voice: stop playback ===
  const handleStopVoice = useCallback(() => {
    stopAudio();
    setJavisStatus("idle");
  }, [stopAudio]);

  // === Voice: handle transcript from mic ===
  // === Voice transcript → auto send ===
  // Forward ref to sendVoiceMessage (defined below) so handleVoiceTranscript
  // always calls the latest version without stale closure issues.
  const sendVoiceMessageRef = useRef<(text: string) => Promise<void>>(() => Promise.resolve());

  const handleVoiceTranscript = useCallback((text: string) => {
    if (!text.trim()) return;
    setInput(text);
    sendVoiceMessageRef.current(text.trim());
  }, []);

  // === Send a voice message directly (bypasses input state) ===
  const sendVoiceMessage = useCallback(async (text: string) => {
    if (!text || isStreaming) return;

    setError(null);

    let convoId = activeConvoIdRef.current || activeConvoId;
    if (!convoId) {
      try {
        const convo = await createConversation();
        setConversations((prev) => [convo, ...prev]);
        setActiveConvoId(convo.id);
        activeConvoIdRef.current = convo.id;
        convoId = convo.id;
      } catch (e) {
        setError(`创建对话失败: ${e instanceof Error ? e.message : "未知错误"}`);
        return;
      }
    }

    const currentMessages = messagesRef.current;
    const userMsg: Message = { role: "user", content: text };
    const newMessages = [...currentMessages, userMsg];
    setMessages(newMessages);
    setInput("");

    const assistantMsg: Message = { role: "assistant", content: "" };
    setMessages([...newMessages, assistantMsg]);
    setIsStreaming(true);
    setJavisStatus("thinking");

    abortRef.current = new AbortController();

    const currentVoice = selectedVoiceRef.current;
    const currentMode = voiceModeRef.current;

    try {
      let accumulated = "";
      await streamChat(
        { messages: newMessages, stream: true, conversation_id: convoId },
        abortRef.current.signal,
        (chunk) => {
          accumulated += chunk;
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = { role: "assistant", content: accumulated };
            return updated;
          });
        },
        (err) => {
          setError(err);
          setJavisStatus("error");
        },
        () => {
          // onDone — auto TTS
          if (currentMode === "auto" && accumulated.trim()) {
            synthesizeSpeech(stripMarkdown(accumulated), currentVoice || undefined)
              .then((blob) => {
                lastPlayedContentRef.current = accumulated;
                playAudio(blob);
                setJavisStatus("speaking");
              })
              .catch(() => {
                setJavisStatus("idle");
              });
          } else {
            setJavisStatus("idle");
          }
        },
      );
    } catch (e: any) {
      if (e.name !== "AbortError") {
        setError(`AI 回复失败: ${e.message}`);
        // Remove empty assistant message on error
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last.role === "assistant" && !last.content) {
            return prev.slice(0, -1);
          }
          return prev;
        });
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;

      // Refresh conversation list to update titles
      try {
        const convos = await listConversations();
        setConversations(convos);
      } catch {
        // ignore
      }
    }
  }, [activeConvoId, isStreaming, playAudio]);

  // Keep ref in sync (useEffect to avoid initialization order issues)
  useEffect(() => {
    sendVoiceMessageRef.current = sendVoiceMessage;
  }, [sendVoiceMessage]);

  // === Auto-TTS when streaming finishes ===
  useEffect(() => {
    if (!isPlaying && javisStatus === "speaking") {
      // Playback finished, reset status
      setJavisStatus("idle");
    }
  }, [isPlaying, javisStatus]);

  // === Config update from settings panel ===
  const handleConfigUpdate = useCallback((newConfig: AppConfig) => {
    setConfig(newConfig);
  }, []);

  // === Switch model ===
  const handleSwitchModel = useCallback(async (modelId: string) => {
    try {
      const data = await switchModel(modelId);
      if (config) {
        setConfig({ ...config, model_config: data });
      }
    } catch (e) {
      setError(`切换模型失败: ${e instanceof Error ? e.message : '未知错误'}`);
    }
  }, [config]);

  // === Skill center ===
  const handleOpenSkillCenter = useCallback(() => setSkillCenterOpen(true), []);
  const handleCloseSkillCenter = useCallback(() => setSkillCenterOpen(false), []);
  const handleSkillSendMessage = useCallback((text: string) => {
    if (!text.trim() || isStreaming) return;
    sendVoiceMessageRef.current(text.trim());
  }, [isStreaming]);

  return (
    <div className="app-layout">
      {/* System Resource Monitor */}
      <SystemMonitor />

      {/* Sidebar */}
      <Sidebar
        conversations={conversations}
        activeId={activeConvoId}
        collapsed={sidebarCollapsed}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        onDeleteConversation={handleDeleteConversation}
        onToggleCollapse={() => setSidebarCollapsed((v) => !v)}
        onToggleSettings={() => setSettingsOpen((v) => !v)}
      />

      {/* Main Chat Area */}
      <ChatArea
        messages={messages}
        isStreaming={isStreaming}
        javisStatus={javisStatus}
        connectionStatus={connectionStatus}
        error={error}
        input={input}
        currentModel={config?.model_config?.model || "加载中..."}
        conversationTitle={getConversationTitle()}
        settingsOpen={settingsOpen}
        onInputChange={setInput}
        onSend={handleSend}
        onStop={handleStop}
        onClearError={() => setError(null)}
        onToggleSettings={() => setSettingsOpen((v) => !v)}
        voiceMode={voiceMode}
        selectedVoice={selectedVoice}
        isVoicePlaying={isPlaying}
        isListening={isListening}
        onVoiceModeChange={setVoiceMode}
        onVoiceSelect={setSelectedVoice}
        onVoiceTranscript={handleVoiceTranscript}
        onListeningChange={setIsListening}
        onPlayMessage={handlePlayMessage}
        onStopVoice={handleStopVoice}
        pendingImages={pendingImages}
        pendingFiles={pendingFiles}
        onImageSelect={handleImageSelect}
        onFileSelect={handleFileSelect}
        onRemovePendingImage={(i) => setPendingImages(prev => prev.filter((_, idx) => idx !== i))}
        onRemovePendingFile={(i) => setPendingFiles(prev => prev.filter((_, idx) => idx !== i))}
        imageInputRef={imageInputRef}
        fileInputRef={fileInputRef}
        onForkFromMessage={handleForkConversation}
        onSwitchModel={handleSwitchModel}
        onOpenSkillCenter={handleOpenSkillCenter}
      />

      {/* Skill Center */}
      {skillCenterOpen && (
        <SkillCenter
          connectionStatus={connectionStatus}
          onSendMessage={handleSkillSendMessage}
          onClose={handleCloseSkillCenter}
        />
      )}

      {/* Settings Panel */}
      {settingsOpen && (
        <Suspense fallback={<div style={{ padding: 24, color: "#94a3b8" }}>加载设置中...</div>}>
          <SettingsPanel
            config={config}
            connectionStatus={connectionStatus}
            onConfigUpdate={handleConfigUpdate}
            onClose={() => setSettingsOpen(false)}
          />
        </Suspense>
      )}
    </div>
  );
}
