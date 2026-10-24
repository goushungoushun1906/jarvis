import { useEffect, useRef, useState, useCallback } from "react";
import {
  getWakeInfo,
  reloadWakeword,
  serverRecordStart,
  serverRecordStop,
  startWakeword,
  streamWakeword,
  type ServerRecordResult,
} from "../lib/api";

/**
 * 语音唤醒：订阅后端关键词引擎的事件流，命中后进入**连续对话**。
 *
 * 与最初版本的三个区别（对标手机助手）：
 *
 * 1. **不再用录音轮询**判定唤醒——后端常驻的专用关键词引擎逐帧判定，
 *    事件毫秒级推过来（见 docs/WAKE_WORD_DESIGN.md）。
 * 2. **VAD 采集替代固定窗口**——旧实现命中后固定录 4 秒（没听清再补
 *    2.5 秒），说完话也得干等窗口走完；现在后端检测到"说完话静音
 *    700ms"就自动停并立即转写，延迟从固定 4s+ 变成"说完 ~1s"。
 * 3. **连续对话**——唤醒一次后进入会话模式：每轮回答播完自动继续听
 *    下一句，不用重新喊唤醒词；连续两轮没人说话（或出故障）自动退回
 *    待唤醒。这就是小爱同学"唤醒一次、连续对话"的行为。
 *
 * 唤醒之前的监听阶段完全不占录音通道，因此不会和「按住 Space 说话」
 * 互抢后端那个全局录音单例。
 */

/** VAD 模式下，一直没人开口时等多久放弃这一轮（首次给足反应时间）。 */
const LEADING_FIRST_MS = 6000;
/** 会话中后续轮次的等待开口时长。 */
const LEADING_NEXT_MS = 5000;
/** 连续几轮没听到人声就退出会话、回到待唤醒。 */
const MAX_IDLE_ROUNDS = 2;
/** 事件流断开后的重连上限（指数退避到此为止）。 */
const MAX_BACKOFF_MS = 15000;
/** 引擎不可用时多久复查一次配置（用户可能正在设置页填密钥）。 */
const ENGINE_RETRY_MS = 5000;
/**
 * 回答"播完"的判定：流结束到 TTS 起播之间有 200–800ms 合成空档，
 * 必须连续安静这么久才算真的说完了，否则会在空档里误开麦。
 *
 * 这个值被刻意压到 500ms。早先是 1200ms，加上 TAIL_MS(600) 意味着播报
 * 结束后还要**整整 1.8 秒**才轮到麦克风——而真人就是"它一停我就开口"，
 * 于是第二句话永远录不进去。分句播报的 speaking 状态已经把"合成中+播放中"
 * 全覆盖了，剩下的合成空档远没有 1.2 秒那么长。
 */
const ANSWER_CALM_MS = 500;
/** TTS 播完后的尾音缓冲：内置麦离扬声器太近，立刻开麦会录进尾音。 */
const TAIL_MS = 250;
/** 等回答结束的兜底上限（模型卡死/异常时别把会话挂死）。 */
const ANSWER_TIMEOUT_MS = 120_000;
/**
 * 会话循环"疑似卡死"的静默时长。超过它就不再信任旧循环，由新的唤醒接管。
 *
 * 这个是给「多轮对话第二轮之后彻底没反应」兜底的：会话循环完全有可能永久
 * 卡在某个 await 上（后端 VAD 请求没回来、浏览器把后台标签的定时器节流、
 * fetch 本身也没有超时）。一旦卡住，``conversing`` 就永远是 true，之后每
 * 一次唤醒都会被 ``if (conversing) return`` 静默吞掉 —— 用户看到的现象正是
 * 「喊了你好贾维斯，界面跳到聆听，然后永远没有下文」，而且**不会报错**。
 *
 * 所以这里用「最后一次有实质进展的时间」做心跳：超时就认定旧循环已死，掐断
 * 它的在途请求，起新一代接管。取 25s 是因为一轮最长可以是
 * leading(6s) + max(9s) + 转写(数秒) ≈ 20s，正常循环不该超过它太多。
 */
const CONVERSATION_STALL_MS = 25_000;

export type WakePhase = "off" | "listening" | "armed" | "conversation";

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * 剥掉识别文本开头那句唤醒词。
 *
 * 真人从手机助手学来的习惯是「喊唤醒词 → 略微停顿 → 说指令」，而 VAD 恰好
 * 会在那个停顿处判停，于是被录进去、又被送给 LLM 的往往只剩「你好贾维斯」
 * —— 表现为「第二轮开始问什么都不对，因为它一直在回答一句唤醒词」。
 *
 * 返回空串表示用户只喊了唤醒词、后面没有指令，调用方应该再听一轮，而不是
 * 把这句唤醒词当成提问发出去。
 */
export function stripLeadingKeyword(text: string, keywords: string[]): string {
  let out = (text || "").trim();
  for (const keyword of keywords) {
    if (!keyword) continue;
    if (out.startsWith(keyword)) {
      out = out.slice(keyword.length);
      break;
    }
  }
  // 切完之后常剩下「，」「。」这种残缀，顺手清掉
  return out.replace(/^[\s,，.。!！?？、;；:：]+/, "").trim();
}

interface UseWakeWordOptions {
  enabled: boolean;
  onCommand: (text: string) => void;
  /** 播报中：命中时依然允许打断，但会话循环要等它结束才开麦。 */
  isPlayingRef?: React.MutableRefObject<boolean>;
  /**
   * 是否"正在出声"（合成中 + 播放中）。
   *
   * 只看 isPlayingRef 不够：TTS 字节到达浏览器才置 isPlaying=true，
   * "流完 → 合成 → 才播"这段空档里它是 false，会话循环就在这个空档开麦，
   * 把自己的播报声完整录了进去——这就是多轮对话串音的根源。
   */
  speakingRef?: React.MutableRefObject<boolean>;
  /** 对话流式进行中。 */
  busyRef?: React.MutableRefObject<boolean>;
  /** 外部临时挂起（例如用户正按住 Space 录音）。 */
  suspendedRef?: React.MutableRefObject<boolean>;
  /** 命中唤醒词时触发，用来打断当前播报。 */
  onWake?: () => void;
  /** 引擎的实时电平，可直接喂给形象动效。 */
  onLevel?: (rms: number) => void;
  /** 状态变化，用于 UI 提示。 */
  onPhaseChange?: (phase: WakePhase) => void;
  /** 命中时回调（唤醒词本身 / 采集到的指令文本）。 */
  onHeard?: (keyword: string) => void;
  /**
   * 防自激守卫：播报刚被打断的几百毫秒内不该判定。
   * 传入 motionRefs 里 createWakeGuard 的实例即可。
   */
  canWake?: () => boolean;
  /** 指定麦克风设备下标；null 用系统默认。 */
  deviceIndex?: number | null;
}

export interface WakeWordControl {
  phase: WakePhase;
  lastHeard: string;
  error: string | null;
  stopListening: () => void;
}

export function useWakeWord({
  enabled,
  onCommand,
  isPlayingRef,
  speakingRef,
  busyRef,
  suspendedRef,
  onWake,
  onLevel,
  onPhaseChange,
  onHeard,
  canWake,
  deviceIndex,
}: UseWakeWordOptions): WakeWordControl {
  const [phase, setPhase] = useState<WakePhase>("off");
  const [lastHeard, setLastHeard] = useState("");
  const [error, setError] = useState<string | null>(null);

  // 回调放 ref，避免它们变化时重启整个订阅
  const onCommandRef = useRef(onCommand);
  const onWakeRef = useRef(onWake);
  const onHeardRef = useRef(onHeard);
  const onLevelRef = useRef(onLevel);
  const onPhaseRef = useRef(onPhaseChange);
  const canWakeRef = useRef(canWake);
  useEffect(() => {
    onCommandRef.current = onCommand;
  }, [onCommand]);
  useEffect(() => {
    onWakeRef.current = onWake;
  }, [onWake]);
  useEffect(() => {
    onHeardRef.current = onHeard;
  }, [onHeard]);
  useEffect(() => {
    onLevelRef.current = onLevel;
  }, [onLevel]);
  useEffect(() => {
    onPhaseRef.current = onPhaseChange;
  }, [onPhaseChange]);
  useEffect(() => {
    canWakeRef.current = canWake;
  }, [canWake]);

  const phaseRef = useRef<WakePhase>("off");
  const setPhaseSafe = useCallback((p: WakePhase) => {
    if (phaseRef.current === p) return;
    phaseRef.current = p;
    setPhase(p);
    onPhaseRef.current?.(p);
  }, []);

  useEffect(() => {
    if (!enabled) {
      setPhaseSafe("off");
      return;
    }

    let cancelled = false;
    let recording = false;
    // 会话循环是否在跑：防止 detection 重入起第二个循环
    let conversing = false;
    // 会话世代。每接管一次就 +1，旧循环发现自己过期后自行退出。
    // 没有这层，被顶掉的旧循环回来时会错误地把新循环标记为"已结束"。
    let sessionGen = 0;
    let sessionAbort: AbortController | null = null;
    // 会话心跳：任何实质进展都刷新它，detection 据此判断循环是否还活着
    let beat = Date.now();
    const touchBeat = () => {
      beat = Date.now();
    };
    /**
     * 会话正在等"这一轮回答播完"，而用户已经先开口了 → 立刻停止等待去开麦。
     *
     * 这是「多轮对话只能撑一两轮」最主要的一环。真人不会等满 privileged 的
     * 静默判定才说话，他就在最后一次播报收音的那一刻开口。旧逻辑里这段等待
     * 最长 1.8 秒，而这段时间麦克风**完全没开**——用户整句话说完的时候，
     * 循环才刚刚到达 armed，于是只能录到一片空白，白等两轮然后退出会话。
     */
    let pendingListen = false;
    const controller = new AbortController();
    // 唤醒词列表：用来从识别结果里剥掉开头那句"你好贾维斯"
    let keywords: string[] = [];

    const isMakingSound = () =>
      (isPlayingRef?.current ?? false) || (speakingRef?.current ?? false);

    const finishRecording = async () => {
      if (!recording) return null;
      recording = false;
      try {
        return await serverRecordStop({ immediate: true }).catch(() => null);
      } catch {
        return null;
      }
    };

    /** VAD 采集一轮：说完自动停，返回识别结果；null = 这一轮没法采。 */
    const vadCapture = async (
      leadingMs: number,
      signal?: AbortSignal
    ): Promise<ServerRecordResult | null> => {
      // 用户可能正在按住 Space 录音，让路
      if (suspendedRef?.current) {
        await sleep(300);
        return null;
      }
      // 还在出声（含合成中）就绝不开麦——这时候录进去的全是自己的声音。
      // 后端还有一把输出锁兜底，这里是主防线，快一拍。
      if (isMakingSound()) {
        await sleep(300);
        return null;
      }
      try {
        await serverRecordStart({ vad: true, leadingMs, maxMs: 9000 });
        recording = true;
        // VAD 模式下 stop = "等录音自然结束再转写"，说完整句话才返回。
        // 必须带上 signal：这个请求可能永远挂着，没出口的话整个会话
        // 循环就被钉死在这儿，后面的唤醒全部无效。
        const result = await serverRecordStop({ signal });
        recording = false;
        return result;
      } catch {
        await finishRecording();
        return null;
      }
    };

    /** 等这一轮的回答彻底结束：流完 + 播完 + 安静满 ANSWER_CALM_MS。 */
    const waitAnswerDone = async (myGen: number) => {
      let calmMs = 0;
      const deadline = Date.now() + ANSWER_TIMEOUT_MS;
      while (!cancelled && myGen === sessionGen && Date.now() < deadline) {
        // 用户抢先开口了：别再等"确认播完"，立刻把麦克风交出去。
        // 少这一行，用户在播报尾音里说的话就整句丢失。
        if (pendingListen) {
          pendingListen = false;
          return;
        }
        const busy = busyRef?.current ?? false;
        if (busy || isMakingSound()) {
          calmMs = 0;
        } else {
          calmMs += 200;
          if (calmMs >= ANSWER_CALM_MS) return;
        }
        touchBeat();
        await sleep(200);
      }
    };

    /**
     * 连续对话主循环：听指令 → 发对话 → 等回答播完 → 再听……
     * 连续 MAX_IDLE_ROUNDS 轮没人说话（或麦克风故障）就退出，
     * 回到待唤醒状态。
     */
    const runConversation = async (myGen: number) => {
      let idleRounds = 0;
      while (!cancelled && myGen === sessionGen) {
        touchBeat();
        pendingListen = false;
        setPhaseSafe("armed");
        const leading = idleRounds === 0 ? LEADING_FIRST_MS : LEADING_NEXT_MS;
        const res = await vadCapture(leading, sessionAbort?.signal);
        touchBeat();
        if (cancelled || myGen !== sessionGen) return;

        if (!res) {
          // 出声期间 / 按住 Space 期间开不了麦，这是时序问题不是用户没理它；
          // 计进 idleRounds 会让会话在自己还在播报时就提前退出。
          if (!isMakingSound() && !suspendedRef?.current) {
            idleRounds += 1;
          }
        } else if (res.idle) {
          // VAD 等满 leading 也没等到人声：正常空闲
          idleRounds += 1;
        } else if (res.text && res.text.trim()) {
          // 用户习惯"喊完唤醒词再说指令"，VAD 常在两者之间的停顿处判停，
          // 剥掉开头那句唤醒词才是真正要发的指令。
          const spoken = stripLeadingKeyword(res.text, keywords);
          if (!spoken) {
            // 只喊了唤醒词、后面没有指令：接着听，
            // 别把"你好贾维斯"当成提问发给模型。
            setLastHeard("");
          } else {
            idleRounds = 0;
            setLastHeard(spoken);
            onHeardRef.current?.(spoken);
            onCommandRef.current(spoken);
            setPhaseSafe("conversation");
            await waitAnswerDone(myGen);
            if (cancelled || myGen !== sessionGen) return;
            await sleep(TAIL_MS);
            continue;
          }
        } else if (res.error) {
          // 麦克风故障（收不到声音）必须立刻说出来并退出会话；
          // "有声音但没听清"则当作一次空闲，再给一次机会。
          if (res.error.includes("没有收到声音")) {
            setError(res.error);
            return;
          }
          idleRounds += 1;
        }

        if (idleRounds >= MAX_IDLE_ROUNDS) return;
      }
    };

    const beginConversation = async () => {
      sessionGen += 1;
      const myGen = sessionGen;
      sessionAbort = new AbortController();
      conversing = true;
      touchBeat();
      void runConversation(myGen)
        .catch(() => undefined)
        .finally(() => {
          // 只有**最新一代**循环退出才允许复位：被顶掉的旧循环回来时若也
          // 复位，会把正在跑的新循环误判成已结束，下一次唤醒就彻底失效。
          if (myGen !== sessionGen) return;
          conversing = false;
          recording = false;
          if (!cancelled) setPhaseSafe("listening");
        });
    };

    const handleDetection = (payload: Record<string, unknown>) => {
      if (canWakeRef.current && !canWakeRef.current()) return;
      const keyword = String(payload.keyword ?? "");
      setLastHeard(keyword);
      onHeardRef.current?.(keyword);
      // 命中唤醒词 = 用户要插嘴，先把正在念的停掉
      onWakeRef.current?.();

      const stalled = Date.now() - beat > CONVERSATION_STALL_MS;
      if (conversing) {
        if (!stalled) {
          // 会话已经在跑。旧代码到这里直接 return，结果用户在"等播报"
          // 那段时间里说出的话全部丢失——因为麦克风根本还没打开。
          // 这里改成把它记为"用户已经开口了"，让等待循环立刻让路开麦。
          pendingListen = true;
          return;
        }
        // 走到这里说明旧循环卡死在某个 await 上。日志里的表现正是
        // 「唤醒命中了，但之后再没有任何 /record/start」——必须先掐断
        // 它的在途请求，否则新旧两个循环会抢同一路录音。
        sessionAbort?.abort();
        void finishRecording();
        conversing = false;
      }
      void beginConversation();
    };

    const handleEvent = (type: string, payload: Record<string, unknown>) => {
      switch (type) {
        case "detection":
          handleDetection(payload);
          break;
        case "level": {
          const rms = Number(payload.rms ?? 0);
          if (Number.isFinite(rms)) onLevelRef.current?.(rms);
          break;
        }
        case "status": {
          const status = String(payload.status ?? "");
          if (status === "suspended") break; // 展示层保持不变，别来回闪
          if (status === "error") {
            setError(String(payload.error ?? "唤醒引擎出错"));
            setPhaseSafe("off");
          } else if (status === "idle") {
            // 引擎重启（如设置页改灵敏度）后别把正在进行的会话标记打断，
            // 只更新"底层在听"这个事实。
            if (!conversing) setPhaseSafe("listening");
          }
          break;
        }
        case "error": {
          setError(String(payload.error ?? "唤醒引擎出错"));
          break;
        }
        default:
          break;
      }
    };

    const connect = async () => {
      let attempt = 0;
      while (!cancelled) {
        try {
          const info = await getWakeInfo();
          keywords = info.keywords ?? [];
          // 只有 porcupine 需要 AccessKey。sherpa 与 vosk 都是免密钥的本地引擎，
          // 拿密钥去卡它们会让引擎永远起不来。
          // 判定写成就认 porcupine 一种，而不是"除了 sherpa 都要"——
          // 否则将来每加一种免密钥引擎都得回来改一次。
          const needsKey = info.engine === "porcupine";
          if ((needsKey && !info.access_key_set) || info.status === "error") {
            // 引擎不可用时必须说出来，否则用户只会觉得"点了没反应"
            setError(info.error || info.hint || "语音唤醒未就绪，请检查设置");
            setPhaseSafe("off");
            await sleep(ENGINE_RETRY_MS);
            continue;
          }
          if (!info.running) {
            await startWakeword(deviceIndex ?? null);
          }
          setError(null);
          setPhaseSafe("listening");
          attempt = 0;
          await streamWakeword(controller.signal, handleEvent);
          // 正常返回说明流断了，立刻重连
        } catch (e) {
          if (cancelled || controller.signal.aborted) return;
          const delay = Math.min(2 ** Math.min(attempt, 5) * 1000, MAX_BACKOFF_MS);
          attempt += 1;
          setError(`唤醒连接中断，${Math.round(delay / 1000)} 秒后重试`);
          await sleep(delay);
        }
      }
    };

    void connect();

    return () => {
      cancelled = true;
      controller.abort();
      sessionAbort?.abort();
      void finishRecording();
      setPhaseSafe("off");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, deviceIndex, setPhaseSafe]);

  return {
    phase,
    lastHeard,
    error,
    stopListening: useCallback(() => setPhaseSafe("off"), [setPhaseSafe]),
  };
}

/** 供诊断面板使用：改完密钥后热重载，不用重启应用。 */
export const reloadWakeEngine = reloadWakeword;
