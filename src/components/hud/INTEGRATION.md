# JARVIS HUD · C 方案「双态融合」集成说明

> 目标：**动起来、能交付、能塞进项目**。
> 设计规格来源：`贾维斯C方案-交互规范.md`（12 条迁移表 / 46 条微交互 / 帧预算 / 降级 / 无障碍）。
> 视觉样板：`贾维斯视觉方案-概念稿.html` → 第 3 个方案（双态融合）。

---

## 0. 已经做了什么（非破坏性）

| 改动 | 类型 | 说明 |
| --- | --- | --- |
| `src/components/hud/**` | **新增** | 全部 HUD 组件，零改动现有文件 |
| `src/lib/types.ts` | **新增一个联合成员** | `JavisStatus` 补 `offline`（规范 §1.1 要求六态）。无 `Record<JavisStatus,…>` / `switch` 穷举依赖，已 `tsc` 验证无副作用 |
| `src/App.tsx` | **新增约 30 行** | HUD 开关 + phase 三态推导。**没有删除/修改任何现有 JSX** |

**没有动过**的东西：`ParticleAvatar.tsx`、`src/components/avatar/`、`JarvisAvatar3D.tsx`、
`index.css` / `light-theme.css` / `dark-theme.css`、`ChatArea.tsx`、四个语音 hook。

**关键**：HUD 的 CSS 令牌全部挂在 `.jhud` 作用域下，**一个都没上全局 `:root`**。
现有豆包浅色风主题用的是 `:root` 上的 `--bg-primary` / `--text-primary` 等变量，
如果 HUD 的 `--bg` / `--text` 污染了 `:root`，会把正在用的浅色 UI 直接改坏。这是本方案最容易踩的坑，已规避。

---

## 1. 文件清单

```
src/components/hud/
├── hud-theme.css       # 设计令牌（颜色/时长/缓动/字体）+ 六态映射 + 降级/无障碍兜底 + 入口按钮样式
├── JarvisHud.css       # 组合根组件的骨架布局
├── motionRefs.ts       # 共享可变引用 + 音频映射数学 + 颜色解析 + 唤醒守卫
├── useJarvisMotion.ts  # 全局唯一 rAF 编排器 + Tier 降级探测 + 麦克风接入 + 状态播报器
├── EnergyCore.tsx/.css # 中央 AI 能量核心（环阵 / 呼吸核 / 青紫双反向弧段 / 粒子 / 涟漪）
├── StatusBar.tsx/.css  # 六态状态指示器（五重编码）
├── WaveformBar.tsx/.css# 频谱条
├── HudBackground.tsx/.css # 背景网格 + 四角括号 + 扫描线
├── JarvisHud.tsx       # 组合根组件 + HudToggle 入口按钮
└── INTEGRATION.md      # 本文
```

---

## 2. Props 接口

```ts
interface JarvisHudProps {
  status: JavisStatus;                              // 六态，必填

  phaseRef?: MutableRefObject<SpeakPhase>;          // 'stream' | 'tts' | null
  levelRef?: MutableRefObject<number>;              // 实时电平 0..1
  bandsRef?: MutableRefObject<Float32Array>;        // 频谱（长度 64）

  onInterrupt?: () => void;                         // 打断
  reducedMotion?: boolean;                          // 不传则跟随系统 prefers-reduced-motion
  tier?: "A" | "B" | "C" | "D";                     // 不传则自动探测 + 自动降档
  micMode?: "off" | "auto" | "always";              // 默认 "auto"（仅 listening 时开麦）

  onExit?: () => void;                              // 显示「退出 HUD」
  onStatusSelect?: (s: JavisStatus) => void;        // 传入则六枚状态胶囊可点击（演示/调试）

  messages?: HudMessage[];                          // 可选会话流，不传则不显示会话区
  onSend?: (text: string) => void;                  // 可选底部输入发送

  overlay?: boolean;                                // 默认 true，作为全窗口浮层
  coreSize?: number;                                // 中央核心尺寸，默认 320
  className?: string;
}

interface HudMessage { role: "user" | "assistant"; text: string; time?: string }
```

`levelRef` / `bandsRef` / `phaseRef` **都可以不传**：
不传时用模块级共享 ref（`getSharedMotionRefs()`），电平缺失会自动走合成包络兜底，
视觉上仍有起伏 —— 绝不会出现"播报中看起来像卡住"。

### 最简用法（三行）

```tsx
import JarvisHud from "./components/hud/JarvisHud";

<JarvisHud status={javisStatus} onInterrupt={stopAll} />
```

### 完整用法（已在 App.tsx 里接好）

```tsx
<JarvisHud
  status={connectionStatus === "offline" ? "offline" : javisStatus}
  phaseRef={hudMotion.phaseRef}
  levelRef={voice.levelRef}
  bandsRef={hudMotion.bandsRef}
  messages={hudMessages}
  onSend={(t) => { chat.setInput(t); void chat.sendText(t); }}
  onInterrupt={() => { voice.stop(); chat.stop(); }}
  onExit={() => setHudMode(false)}
/>
```

---

## 3. 开关怎么接（App.tsx 里已接好，这里说明原理）

```tsx
const [hudMode, setHudMode] = useState(() => localStorage.getItem("jarvis.hud") === "1");
useEffect(() => { localStorage.setItem("jarvis.hud", hudMode ? "1" : "0"); }, [hudMode]);

{hudMode ? <JarvisHud ... onExit={() => setHudMode(false)} />
         : <HudToggle onClick={() => setHudMode(true)} />}
```

- **入口按钮** `HudToggle` 固定在右下角（`z-index: 8900`），点一下进 HUD。
- **出口**：HUD 右上角「退出 HUD」，或 Esc（仅打断，不退出）。
- HUD 是 `position: fixed; inset: 0; z-index: 9000` 的浮层，**覆盖但不销毁**下面的浅色 UI ——
  聊天状态、流式、唤醒监听全部照常运行，切回来就是原样。
- 偏好写在 `localStorage["jarvis.hud"]`，重启保持。

想换成「设置面板里加一项」也可以：把 `setHudMode` 传进 `SettingsPanel` 即可，逻辑不变。

---

## 4. 三个高危项的处理方式（规范 §10）

### ① speaking 双阶段闪跳 → `phase` 三态

SSE `done` 到 TTS 起播有 200–800ms 空档，只看 `status` 会 `idle → speaking` 闪一下。

App.tsx 里已加：

```tsx
useEffect(() => {
  hudMotion.phaseRef.current =
    javisStatus !== "speaking" ? null
      : voice.isPlaying ? "tts"
      : chat.isStreaming ? "stream"
      : null;                       // ← 空档期：status 仍是 speaking，phase=null
}, [javisStatus, voice.isPlaying, chat.isStreaming, hudMotion.phaseRef]);
```

HUD 的文案与振幅驱动源随之切换：

| phase | 文案 | 振幅驱动源 |
| --- | --- | --- |
| `stream` | `TRANSMITTING` | 合成包络 `0.35 + 0.15*sin(t/220ms)` |
| `null`（空档） | `SYNTHESIZING` | 低频呼吸 `0.18`，**维持"在播报"观感** |
| `tts` | `SPEAKING` | 真实 `levelRef`（AnalyserNode RMS） |

### ② TTS 自激死循环 → `wakeGuard`

`useJarvisMotion` 返回的 `motion.wakeGuard` 是一个 400ms cooldown 的守卫：

```ts
useEffect(() => {
  if (status === "thinking" || status === "speaking") motion.wakeGuard.suspend();
}, [status, motion.wakeGuard]);

// 打断时：
handleInterrupt() {
  motion.wakeGuard.releaseAfterInterrupt();  // 400ms 后才允许再唤醒
  onInterrupt?.();
}
```

把它接进现有 `src/hooks/useWakeWord.ts` 的循环判断即可（每轮开麦前问一句 `canWake()`）：

```ts
import { getSharedMotionRefs, createWakeGuard } from "./components/hud/motionRefs";
// 或直接用 motion.wakeGuard（推荐，从 JarvisHud 内部拿）
```

> 现状说明：项目里的 `useWakeWord` 已经用 `isPlayingRef` / `busyRef` 做了粗粒度挂起，
> 功能上不至于自激；`wakeGuard` 补的是「打断后 400ms 尾音」这一段，需要时接上即可。

### ③ 降级探测误判

`useJarvisMotion` 里三条防线全部实现：

- **跳过前 90 帧**（启动编译期 + 首绘必然超标）
- **`visibilitychange` → hidden 时 cancel rAF 且不计入采样**（切后台 rAF 被节流到 1fps，p95 会爆表）
- **降档后 10s 冻结**（`now - lastTierChange > 10000` 才允许再次评估），且只降不升，避免振荡

档位只写 `root.dataset.tier`，**不 setState**；CSS 用 `[data-tier="C"]` 关掉对应模块。

---

## 5. 性能红线落实清单

| 约束 | 实现 | 验证方式 |
| --- | --- | --- |
| 每帧 setState = 0 | 所有每帧数据走 `levelRef` / `bandsRef`；`FrameCtx` 类型里**故意不含任何 setter** | React DevTools Profiler：状态迁移时才有 commit |
| 每帧 inline 写入 ≤ 8 个元素 | **实际 7 个**：`.ec-shell`(transform) `.ec-glow`(opacity) `.ec-ring--1/2/3`(rotate) `.ec-arc--a/b`(stroke-dashoffset)。粒子/频谱走 Canvas（0 次 DOM 写入） | Performance 面板 Recalculate Style < 1.5ms |
| 禁止每帧往根元素写 CSS 变量 | 只有 `data-tier` / `data-phase` / `data-size` / `data-motion` 在**变化时**写一次，从不写 `--xxx` | 代码搜索 `setProperty` → HUD 内 0 处 |
| 只用合成属性 | 所有 keyframes 只含 `transform` / `opacity`；`stroke-dashoffset` 限 2 个元素（arc-a / arc-b）+ 320×320 视口 + `contain: paint` + `translateZ(0)` | 代码搜索 `transition:` / `@keyframes` |
| 禁止动画 box-shadow / filter 参数 | 辉光改为预烘焙 `radial-gradient` 图层 + `opacity` 动画；噪点用静态 `repeating-conic-gradient` + `rotate`/`opacity`；offline 用静态 `mix-blend-mode: saturation` | 代码搜索 `box-shadow`、`filter` → HUD 内无动画用法 |
| 全局唯一 rAF | `useJarvisMotion` 是唯一 `requestAnimationFrame` 调用点；`useMicLevel` 通过 `registerSampler` 挂进同一个循环 | 代码搜索 `requestAnimationFrame` → HUD 内 1 处 |
| `will-change` ≤ 8 | `.ec-shell` `.ec-glow` `.ec-ring--1/2/3` `.hb-scan` = 6 个 | CSS 搜索 `will-change` |
| 禁止 `transition: all` | 全部显式列属性 | CSS 搜索 `transition: all` → 0 处 |
| 消费 `--core` 的元素 ≤ 40 | 3 环 + 2 弧段 + 栅格 + 涟漪 + 内环 + spiner + 品牌框 + 状态条 6 + 播报文本 ≈ 25 | 目视 |

### 一处**有意偏离**规范（已评估，写在 `WaveformBar.tsx` 头部）

规范 §4.2 写的是 64 个 DOM div 各写一次 `transform: scaleY()`。那等于每帧对 64 个元素做
inline 写入，直接打破「≤8 个白名单元素」的死线，且 64 个独立合成层不可能（§7.2 第 10 条：合成层 ≤12）。

→ **改用 Canvas**：每帧 DOM 写入 = 0。条数（64/32/16/0）与降级规则（§7.4）完全照搬，
条宽 3px / gap 3px / 高 4–56px / 峰值帽 / 回落系数 0.86 全部保留。视觉结果与 DOM 方案一致。

### 已知的一处既有 rAF（不属于本次新增）

`src/hooks/useAudioPlayer.ts` 有一个自己的 rAF 用于采样 TTS 电平。它是**既有的、可工作的**
代码，本次按"非破坏性"原则没动它。若想彻底统一，把那段的 `getByteTimeDomainData` 采样
挪进 `registerSampler` 即可，改动很小。

---

## 6. 无障碍落实清单

- **状态五重编码**：颜色（`--core-text`）+ 文字（`STANDBY`/`LISTENING`/`PROCESSING`/`TRANSMITTING|SYNTHESIZING|SPEAKING`/`FAULT`/`NO LINK`）+ 图标（6 个独立几何形状）+ 动效节律（状态点 2000/500/800/300/500step/**0**ms）+ 几何形状（弧段内收 / 外扩 / 断裂 / 涟漪向外）。
  offline 的节律 = 0（完全静止）本身就是一条辨识线索。
- **`aria-live`**：`JarvisHud` 内唯一出口，`role="status" aria-live="polite"`，**最小驻留 800ms**
  （thinking 一闪而过不播报，否则读屏被刷屏）。error 额外一个 `role="alert"`。
- **核心 `aria-hidden="true"`**：纯装饰，由播报器代述，避免与 `aria-live` 重复。
- **对比度硬伤已修**：
  - offline `#64748B` 对底色仅 4.12:1 → **文字/图标一律走 `--core-text: #94A3B8`（7.72:1）**；图形仍用 `#64748B`。
  - 原描边 `#24314F` 对面板仅 1.47:1 → **功能性控件边界（输入框、按钮、胶囊、会话卡）一律用 `--stroke-strong: #4E6088`（3.06:1）**；`--stroke` 只允许画网格和装饰分隔线。
- **焦点环**：`:focus-visible { outline: 2px solid var(--core); outline-offset: 2px }`（11.95:1 ✅）。
- **键盘**：`Esc` = 打断（输入框非空时优先清空，二次 Esc 才打断）；停止按钮带 `aria-keyshortcuts="Escape"`。核心区不可聚焦（纯装饰）。
- **`prefers-reduced-motion`**：JS 监听 + CSS 兜底双保险，写 `data-motion="off"`。
  reduce ≠ 无动画：**状态色迁移完全保留**，靠颜色/文字/图标/形状四重冗余辨识；
  粒子 Canvas 不启动；涟漪不发射；呼吸固定；频谱降为 16 条 @10fps（保留"有声音"的证据）。
- **`forced-colors: active`**（Windows 高对比度）：Canvas 层 `display:none`，描边改 `CanvasText`。

---

## 7. 六态动效实现摘要

| 状态 | 核心 | 环阵 | 弧段 | 粒子 | 频谱 | 状态条 |
| --- | --- | --- | --- | --- | --- | --- |
| **idle** | 慢呼吸 4s，`scale 1→1.045` | 3 环慢转 0.06rps（顺/逆/顺） | 对称，青+紫双反向 | 基线半径 | 基线 4px，不隐藏 | `STANDBY` · 点脉冲 2s |
| **listening** | 呼吸 1.8s + **真实麦克风电平**驱动收缩/扩张（×1.35 增益） | 加速到 0.10rps | **内收 −14° + scale .94** + 外圈接收栅格显现 | 半径 ×(1+0.18·level) | 真实频谱 64 条 | `LISTENING` · 点脉冲 0.5s |
| **thinking** | 呼吸 1.2s，核心收拢 | **加速到 0.25rps**（240ms 平滑插值，不硬切） | 加速扫动 | 收紧 | 频谱淡到 .35 | `PROCESSING` · 中心三段 spinner · 点脉冲 0.8s |
| **speaking** | 呼吸 0.6s + **TTS 真实频谱**驱动；周期性发射声波涟漪（220–900ms，池 3 个） | 0.14rps | **外扩 +6° + scale 1.05** | 半径 ×1.28 上限 + 拖尾 | 真实频谱 + 峰值帽 | `TRANSMITTING`/`SYNTHESIZING`/`SPEAKING` · 点脉冲 0.3s |
| **error** | 横向抖动 ±1.5px，60ms steps(2,end) ×3 | 停转 | dasharray 断裂（40 30 40 250）+ 噪点层 opacity .18 | 照常 | 照常 | `FAULT` · 点 step-end 闪烁 |
| **offline** | **完全静止**；彩色层→灰层 opacity 交叉 420ms | 仅 1 条虚线环，其余隐藏 | opacity .25，动画 none | Canvas 不绘制 | 隐藏 | `NO LINK` · **唯一无动画态** |

状态切换时 `--core` 颜色 **420ms 插值**：`--core` 本身不做 transition，
由 `stroke` / `color` / `fill` / `background-color` / `border-color` 各自过渡 420ms（`--e-std`），
浏览器在样式系统内完成，零 JS。Canvas 部分由 `approachRGB` 用 420ms 时间常数逼近同一色值。

---

## 8. 帧预算（目标 vs 实现）

| 阶段 | 预算 | 实现 |
| --- | --- | --- |
| JS（rAF 回调 + targets.apply） | ≤ 2.0ms | 7 次 inline 写入 + 2 次 Canvas 绘制；粒子 A 档 900 点按面积缩放（320px 核心即 900，240px 降到 506） |
| Style + Layout | ≤ 1.5ms | 每帧只碰 7 个元素的合成属性，无 CSS 变量写入，无布局属性 |
| Paint | ≤ 3.0ms | `stroke-dashoffset` 限 2 个元素、限 320×320 视口、`contain: paint` 独立成层；Canvas 面积 ≤ 480×480，DPR clamp 1.5 |
| Composite | ≤ 4.0ms | 合成层：shell / glow / 3 环 / 2 弧 / 扫描线 / 粒子 / 频谱 ≈ 10 层（≤12） |

---

## 9. 单独使用某个组件

```tsx
import { useJarvisMotion } from "./components/hud/useJarvisMotion";
import EnergyCore from "./components/hud/EnergyCore";
import StatusBar from "./components/hud/StatusBar";
import WaveformBar from "./components/hud/WaveformBar";

const rootRef = useRef<HTMLDivElement>(null);
const motion = useJarvisMotion({ rootRef, status: javisStatus });

<div ref={rootRef} className="jhud" data-status={javisStatus}>
  <EnergyCore size={280} register={motion.register} coreColorRef={motion.coreColorRef} />
  <StatusBar status={javisStatus} />
  <WaveformBar register={motion.register} coreColorRef={motion.coreColorRef} width={360} />
</div>;
```

> 注意：单独使用时**必须**自己加 `className="jhud"` 和 `data-status`，
> 否则拿不到 `--core` 等令牌（令牌作用域是 `.jhud`）。

---

## 10. 还没做的（如果要做到 100%）

规范里这几条需要改**现有业务组件**，本次按"非破坏性"原则没有动：

1. **T4 首 token 才进 speaking**：现在 `useChatStream.ts:146` 是 `onChunk` 里无条件
   `setJavisStatus("speaking")`。改成 `if (chunk.trim() || toolEventSeen)` 判空后再切更严谨。
2. **T2/T4b 的 3500ms elapsed 计时**、**T9 首 token 超时 20s 转 error**。
3. **T7 的 60ms gain ramp**：现在 `voice.stop()` 是否爆 click 音取决于 `useAudioPlayer` 实现，
   建议在那里加 `linearRampToValueAtTime(0, now + 0.06)` 再 `source.stop()`。
4. **armed 9s 倒计时环**（`ArmedRing`）：需要 `useWakeWord` 暴露 armed 截止时间。
5. **`useAudioPlayer` 的 `rms * 4.5` 线性放大** → 建议换成 `normalizeLevel(rms)`（感知曲线，弱声更可见）。

这些都是**加法**，随时可以做，不影响 HUD 本身运行。
