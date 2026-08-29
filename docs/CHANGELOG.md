# JARVIS 更新日志

## v1.0.1 (2026-08-25)

### 修复与优化

- **修复 AI 回复空内容**：`agnes-15` 内置模型由不可用的 `agnes-1.5-flash` 修正为 `agnes-2.0-flash`
- **模型路由运行时降级**：当首选模型返回 503/model_not_found 时，自动尝试下一 tier 的可用 profile
- **修复循环导入**：`DEFAULT_TIER_MAP` 从 `model_router.py` 迁移至 `models.py`
- **增强 LLM 调试日志**：记录每次调用的 tier/profile/model，以及 API 响应和空内容警告
- **完善空内容兜底**：`agent_chat` 与流式端点在模型返回空内容时给出可读的提示信息

## v1.0.0 (2026-06-29)

### 核心架构

- React + TypeScript + Vite 前端，Python FastAPI 后端
- 流式对话（SSE），支持多轮对话管理
- 前后端分离架构，后端运行在 18200 端口

### Phase 1 — 基础对话

- 多模型 LLM 支持（agnes-2.0-flash）
- 对话创建/删除/切换
- 侧栏对话列表 + 收起功能
- 流式文本输出

### Phase 2 — 语音系统

- 语音识别（Web Speech API / Whisper 本地模型）
- 语音合成（TTS）+ 音频播放
- 语音控制面板（开始/停止朗读、语音设置）
- 麦克风录音 + 媒体录制 hook

### Phase 3 — 人设与记忆

- 自定义人设（persona）系统
- 对话记忆管理（记忆存储/检索）
- 情绪分类器（emotionClassifier）
- 情感感知对话状态

### Phase 4 — 插件与工具

- 插件系统框架（base/manager/registry）
- 内置工具集（builtins/tools）
- 插件管理 UI（安装/卸载/配置）
- Agent 智能体框架（agent.py）
- 工具调用路由（routes/tools.py）

### Phase 5 — 多模态

- 图片上传 + 文件上传
- 图片附件/文件附件消息类型
- HTML 代码块检测 + 内联预览工具栏（预览/复制/打开）
- HtmlPreviewModal 沙箱 iframe 预览

### Phase 6 — 优化与发布

- i18n 国际化框架（150+ 翻译条目，中/英切换）
- 隐私数据管理 API（统计/导出/清除）
- Debug 中间件（环境信息端点）
- 自动更新检查 API
- 单元测试（11 个 API 测试全部通过）
- 性能基准测试（所有端点 <12ms）
- 打包脚本（scripts/build.py）
- 代码分割（React.lazy/Suspense，设置面板等组件懒加载）

### Phase 7 ~ 10 — 编号保留

> 这些阶段编号当前未分配具体功能，保留用于后续规划。

### Phase 11 — 浏览器自动化与区域截图

- Playwright + Chromium 无头浏览器工具集（`browser_tools.py`）
- 浏览器工具：`browser/open`、`browser/extract`、`browser/screenshot`、`browser/click`、`browser/close`
- 截图 API：`/screenshot` 支持 fullscreen / active_window / region 三种模式
- Electron 全屏区域选择器（`electron/region-selector.html` + `ipcMain`）
- 前端区域截图组件（`RegionSelector.tsx`）并集成到 `ChatArea`
- 后端工具注册与 API 路由已接入主程序

### Phase 12 — 三层模型路由与成本看板

- Fast/Mid/Deep 三层模型策略（`model_router.py`）
- 基于输入长度、附件、工具、历史长度、关键词的自动档位选择
- 内置模型档位映射（agnes-flash/gpt4o-mini → fast，agnes-15/gpt4o/doubao → mid，deepseek/claude/deepseek-r1-ollama → deep）
- 自定义模型档案默认 `mid`，支持持久化 `tier` 字段
- LLM 调用统一入口（`llm.py`）集成路由选择与成本记录
- `llm_calls` 数据表记录每次调用的模型、档位、字符数、耗时、成功状态、预估成本
- 成本统计 API：`/api/costs/summary`、`/api/costs/recent`、`/api/costs/by-model`、`/api/costs/by-day`
- 前端成本看板组件（`CostDashboard.tsx`）：总览卡片、模型分布、每日趋势、最近调用列表
- 前端 API 封装（`src/lib/api.ts`）与类型定义（`src/lib/types.ts`）

### 3D 全息数字人

- Three.js + React Three Fiber 3D 渲染
- 自定义 GLSL 着色器（扫描线 + 菲涅尔边缘发光）
- 线框头部结构：上半部分 + 可分离下颌（说话动画）
- 发光双眼（自动眨眼，3-5 秒周期）
- 嘴部 3D 声波环（64 点 BufferGeometry，说话时实时波形）
- 200 个悬浮粒子（球形壳层内弹跳）
- 两条轨道环（自动旋转）
- 6 种状态：idle / listening / thinking / speaking / error / offline
- 全息外观风格，科幻电影感
- 对话布局重构：数字人居中 + 消息面板可折叠

### UI 功能

- 呼吸灯状态指示器（BreathingIndicator）
- 消息气泡（用户/助手分色）
- 模型切换面板
- 设置面板（模型/人设/插件/隐私与数据 4 个标签页）
- 对话记录面板（可展开/收起）
- 响应式侧栏

### 已知问题

- 后端 AI 回复在某些配置下可能返回空内容（需检查 API Key 及模型可用性）
- THREE.Clock 弃用警告（R3F 内部使用，不影响功能）
- 隐私导出 API 待完善（当前返回基础统计）
