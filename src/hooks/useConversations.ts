import { useCallback, useEffect, useRef, useState } from "react";
import type { Conversation, Message } from "../lib/types";
import {
  listConversations,
  getConversation,
  createConversation,
  deleteConversation,
  forkConversation,
  updateConversationTitle,
} from "../lib/api";

export interface ConversationsController {
  conversations: Conversation[];
  /** 当前会话 id；流式回调里要读最新值，所以用 ref 镜像一份。 */
  activeId: string | null;
  activeIdRef: React.MutableRefObject<string | null>;
  messages: Message[];
  messagesRef: React.MutableRefObject<Message[]>;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  /** 侧栏标题：优先用会话标题，否则取首条用户消息的前 30 字。 */
  getTitle(): string;
  select(id: string): Promise<void>;
  createNew(): Promise<Conversation | null>;
  remove(id: string): Promise<void>;
  fork(messageId: number): Promise<void>;
  /** 用首条消息自动命名新会话。 */
  autoTitle(convoId: string, text: string): void;
  /** 流式结束后刷新列表（标题可能已被后端改写）。 */
  refresh(): Promise<void>;
  /** 保证有一个会话；没有就新建。返回 id 与是否新建。 */
  ensureActive(): Promise<{ id: string; created: boolean }>;
}

/** 会话列表 + 当前会话消息的读写。 */
export function useConversations(
  isStreaming: boolean,
  onError: (msg: string) => void
): ConversationsController {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);

  const activeIdRef = useRef<string | null>(null);
  const messagesRef = useRef<Message[]>([]);
  const deletingRef = useRef<string | null>(null);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const fail = useCallback(
    (prefix: string, e: unknown) => {
      const detail = e instanceof Error ? e.message : "未知错误";
      const msg = `${prefix}: ${detail}`;
      // 错误统一上报给 App
      onError(msg);
    },
    [onError]
  );

  const refresh = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch {
      // 列表刷新失败不影响主流程
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setActive = useCallback((id: string | null) => {
    setActiveId(id);
    activeIdRef.current = id;
  }, []);

  const select = useCallback(
    async (id: string) => {
      if (isStreaming) return;
      setActive(id);
      try {
        const convo = await getConversation(id);
        setMessages(convo.messages || []);
      } catch (e) {
        fail("加载对话失败", e);
        setMessages([]);
      }
    },
    [isStreaming, setActive, fail]
  );

  const createNew = useCallback(async (): Promise<Conversation | null> => {
    try {
      const convo = await createConversation();
      setConversations((prev) => [convo, ...prev]);
      setActive(convo.id);
      setMessages([]);
      return convo;
    } catch (e) {
      fail("创建对话失败", e);
      return null;
    }
  }, [setActive, fail]);

  const remove = useCallback(
    async (id: string) => {
      if (isStreaming) return;
      if (deletingRef.current === id) return; // 防重复删除
      deletingRef.current = id;
      try {
        await deleteConversation(id);
        setConversations((prev) => prev.filter((c) => c.id !== id));
        if (activeId === id) {
          setActive(null);
          setMessages([]);
        }
      } catch (e) {
        fail("删除对话失败", e);
      } finally {
        deletingRef.current = null;
      }
    },
    [isStreaming, activeId, setActive, fail]
  );

  const fork = useCallback(
    async (messageId: number) => {
      const currentId = activeIdRef.current || activeId;
      if (!currentId || isStreaming) return;
      try {
        const newConvo = await forkConversation(currentId, messageId);
        setConversations((prev) => [newConvo, ...prev]);
        setActive(newConvo.id);
        const full = await getConversation(newConvo.id);
        setMessages(full.messages || []);
      } catch (e) {
        fail("分叉对话失败", e);
      }
    },
    [activeId, isStreaming, setActive, fail]
  );

  const autoTitle = useCallback((convoId: string, text: string) => {
    if (!text) return;
    const title = text.length > 30 ? text.slice(0, 30) + "..." : text;
    updateConversationTitle(convoId, title)
      .then(() => {
        setConversations((prev) =>
          prev.map((c) => (c.id === convoId ? { ...c, title } : c))
        );
      })
      .catch(() => {
        // 标题更新失败无所谓
      });
  }, []);

  const ensureActive = useCallback(async () => {
    const existing = activeIdRef.current || activeId;
    if (existing) return { id: existing, created: false };
    const convo = await createNew();
    return convo ? { id: convo.id, created: true } : { id: "", created: false };
  }, [activeId, createNew]);

  const getTitle = useCallback((): string => {
    if (!activeId) return "JARVIS";
    const convo = conversations.find((c) => c.id === activeId);
    if (!convo) return "JARVIS";
    if (convo.title) return convo.title;
    const firstUser = messages.find((m) => m.role === "user");
    if (firstUser) {
      return (
        firstUser.content.slice(0, 30) +
        (firstUser.content.length > 30 ? "..." : "")
      );
    }
    return "新对话";
  }, [activeId, conversations, messages]);


  return {
    conversations,
    activeId,
    activeIdRef,
    messages,
    messagesRef,
    setMessages,
    getTitle,
    select,
    createNew,
    remove,
    fork,
    autoTitle,
    refresh,
    ensureActive,
  };
}
