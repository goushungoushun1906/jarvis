import { useCallback, useRef, useState } from "react";
import type {
  Message,
  JavisStatus,
  ImageAttachment,
  FileAttachment,
  ToolCallRecord,
  RightPanelTab,
} from "../lib/types";
import { streamChat } from "../lib/api";
import { stripToolCallJson } from "../lib/textUtils";
import type { ToolCallTracker } from "./useToolCallTracker";
import type { ConversationsController } from "./useConversations";
import type { VoicePipeline } from "./useVoicePipeline";

export interface ChatStreamDeps {
  conversations: ConversationsController;
  tools: ToolCallTracker;
  voice: VoicePipeline;
  /** 浏览器打开成功后把页面镜像进右侧面板。 */
  onMirrorWebPage: (tab: RightPanelTab) => void;
}

export interface ChatStreamController {
  isStreaming: boolean;
  input: string;
  setInput: (value: string) => void;
  pendingImages: ImageAttachment[];
  setPendingImages: React.Dispatch<React.SetStateAction<ImageAttachment[]>>;
  pendingFiles: FileAttachment[];
  setPendingFiles: React.Dispatch<React.SetStateAction<FileAttachment[]>>;
  /** 发送输入框内容（含待发附件）。 */
  send(): Promise<void>;
  /** 直接发送一段文本，绕过输入框——语音转写、划词助手、快捷指令走这条。 */
  sendText(text: string): Promise<void>;
  stop(): void;
}

/**
 * 流式对话管线。
 *
 * 历史上这里有**两份几乎一样**的实现：`handleSend`（输入框）与
 * `sendVoiceMessage`（语音）。二者逐渐漂移出真实差异——语音路径不做
 * `stripToolCallJson`、也不把 `browser_open` 的结果镜像到右侧面板，属于 bug。
 * 现在合并成一条管线，两条入口共用，行为保证一致。
 */
export function useChatStream(
  deps: ChatStreamDeps,
  setJavisStatus: (status: JavisStatus) => void,
  onError: (message: string) => void,
  onStreamingChange: (streaming: boolean) => void
): ChatStreamController {
  const { conversations, tools, voice, onMirrorWebPage } = deps;

  const [input, setInput] = useState("");
  const [pendingImages, setPendingImages] = useState<ImageAttachment[]>([]);
  const [pendingFiles, setPendingFiles] = useState<FileAttachment[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  // 并发守卫：send 是异步的，光靠 state 挡不住快速连点
  const busyRef = useRef(false);

  const setStreaming = useCallback(
    (value: boolean) => {
      setIsStreaming(value);
      onStreamingChange(value);
    },
    [onStreamingChange]
  );

  const patchLastAssistant = useCallback(
    (patch: Partial<Message>) => {
      conversations.setMessages((prev) => {
        if (prev.length === 0) return prev;
        const updated = [...prev];
        updated[updated.length - 1] = {
          ...updated[updated.length - 1],
          ...patch,
        };
        return updated;
      });
    },
    [conversations]
  );

  /**
   * 执行一次完整的流式回合。
   *
   * @param text        用户文本
   * @param attachments 待发附件（仅输入框路径有）
   * @param shouldAutoTitle 新会话是否用首条消息自动命名
   */
  const run = useCallback(
    async (
      text: string,
      attachments: { images?: ImageAttachment[]; files?: FileAttachment[] },
      shouldAutoTitle: boolean
    ) => {
      const hasAttachments =
        (attachments.images?.length ?? 0) > 0 ||
        (attachments.files?.length ?? 0) > 0;
      if ((!text && !hasAttachments) || busyRef.current) return;

      busyRef.current = true;
      setStreaming(true);

      // 发新消息前先停掉正在播放的语音，否则两路声音会叠在一起
      if (voice.isPlaying) voice.stop();

      try {
        const { id: convoId, created } = await conversations.ensureActive();
        if (!convoId) return;

        if (created && shouldAutoTitle && text) {
          conversations.autoTitle(convoId, text);
        }

        const userMsg: Message = {
          role: "user",
          content: text,
          images: attachments.images?.length ? [...attachments.images] : undefined,
          files: attachments.files?.length ? [...attachments.files] : undefined,
        };
        // 用 ref 读而不是闭包里的 state —— 连续发送时闭包会拿到旧值
        const base = [...conversations.messagesRef.current, userMsg];
        conversations.setMessages([
          ...base,
          { role: "assistant", content: "" },
        ]);
        setInput("");
        setPendingImages([]);
        setPendingFiles([]);
        setJavisStatus("thinking");

        tools.reset();
        abortRef.current = new AbortController();

        let accumulated = "";
        // 分句播报：已经排进播报队列的字符数。首句一生成完就开念，
        // 不用等整段回答（旧行为：整段生成完才合成 → 体感"思考完还要干等"）。
        let spokenUpTo = 0;
        const autoSpeak = () => voice.voiceModeRef.current === "auto";

        /** 从 pending 里切出一个可念的整句；没有完整句子就返回 null。 */
        const takeSentence = (pending: string): string | null => {
          if (pending.trim().length < 6) return null;
          // 句末标点优先
          let cut = -1;
          for (const ch of ["。", "！", "？", "!", "?", "\n"]) {
            const i = pending.lastIndexOf(ch);
            if (i > cut) cut = i;
          }
          // 长句迟迟不来句号时，退而在逗号处切一刀，避免一直憋着不念
          if (cut < 0 && pending.length >= 60) {
            for (const ch of ["，", ",", "；", ";", "、"]) {
              const i = pending.lastIndexOf(ch);
              if (i > cut) cut = i;
            }
          }
          if (cut < 0) return null;
          return pending.slice(0, cut + 1);
        };

        await streamChat(
          { messages: base, stream: true, conversation_id: convoId },
          abortRef.current.signal,
          (chunk) => {
            accumulated += chunk;
            setJavisStatus("speaking");
            patchLastAssistant({
              // 模型不走原生 function calling 时会把 {"tool":...,"args":{...}}
              // 当正文吐出来，这里必须剥掉，否则会显示成一段乱码 JSON。
              content: stripToolCallJson(accumulated),
              tool_calls: tools.list(),
            });

            if (autoSpeak()) {
              const pending = stripToolCallJson(accumulated).slice(spokenUpTo);
              const piece = takeSentence(pending);
              if (piece) {
                spokenUpTo += piece.length;
                // 工具调用的 JSON 不能念出来
                if (!piece.includes('"tool"')) voice.enqueue(piece);
              }
            }
          },
          (err) => {
            onError(err);
            setJavisStatus("error");
          },
          () => {
            const visible = stripToolCallJson(accumulated);
            if (autoSpeak() && visible.trim()) {
              // 只补念最后那截没凑成整句的内容，前面已经念过的不再重复
              const tail = visible.slice(spokenUpTo).trim();
              if (tail && !tail.includes('"tool"')) {
                voice.enqueue(tail);
              }
            } else {
              setJavisStatus("idle");
            }
          },
          (toolCall: ToolCallRecord) => {
            tools.enrich(toolCall);
            patchLastAssistant({ tool_calls: tools.list() });
          },
          (toolResult: ToolCallRecord) => {
            const current = tools.mergeResult(toolResult);
            patchLastAssistant({ tool_calls: current });

            // 打开网页的目的就是让用户看见——镜像进右侧面板。
            // 固定 id 让重复浏览复用同一个页签，而不是越堆越多。
            const meta = toolResult.metadata;
            if (toolResult.success && toolResult.tool === "browser_open" && meta?.url) {
              onMirrorWebPage({
                id: "webview-agent",
                title: meta.title || meta.url,
                type: "webview",
                url: meta.url,
                embeddable: meta.embeddable,
                embed_block_reason: meta.embed_block_reason,
                closable: true,
              });
            }
          },
          (thinking) => patchLastAssistant({ thinking })
        );
      } catch (err: unknown) {
        const isAbort = err instanceof Error && err.name === "AbortError";
        if (isAbort) {
          setJavisStatus("idle");
        } else {
          const msg = err instanceof Error ? err.message : "未知错误";
          onError(msg);
          setJavisStatus("error");
          // 没收到任何内容时，把占位的空 assistant 消息撤掉
          conversations.setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role === "assistant" && !last.content) {
              return prev.slice(0, -1);
            }
            return prev;
          });
        }
        // 中止或失败，都不能让工具卡片一直转圈
        tools.markRunningAsCancelled();
        patchLastAssistant({ tool_calls: tools.list() });
      } finally {
        abortRef.current = null;
        busyRef.current = false;
        setStreaming(false);
        await conversations.refresh();
      }
    },
    [
      conversations,
      tools,
      voice,
      onMirrorWebPage,
      setJavisStatus,
      onError,
      patchLastAssistant,
      setStreaming,
    ]
  );

  const send = useCallback(async () => {
    const text = input.trim();
    if ((!text && pendingImages.length === 0 && pendingFiles.length === 0) || busyRef.current) {
      return;
    }
    await run(
      text,
      {
        images: pendingImages.length ? pendingImages : undefined,
        files: pendingFiles.length ? pendingFiles : undefined,
      },
      true
    );
  }, [input, pendingImages, pendingFiles, run]);

  const sendText = useCallback(
    async (text: string) => {
      if (!text || busyRef.current) return;
      await run(text, {}, false);
    },
    [run]
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    voice.stop();
    setJavisStatus("idle");
  }, [voice, setJavisStatus]);

  return {
    isStreaming,
    input,
    setInput,
    pendingImages,
    setPendingImages,
    pendingFiles,
    setPendingFiles,
    send,
    sendText,
    stop,
  };
}
