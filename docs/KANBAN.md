# JARVIS 项目看板

> 最后更新：2026-08-25

---

## 已完成

### Phase 1 — 基础对话

| 任务 | 状态 | 说明 |
|------|------|------|
| FastAPI 后端搭建 | 完成 | 路由框架、CORS、SSE 流式 |
| LLM 对话接口 | 完成 | streamChat 流式输出 |
| React 前端框架 | 完成 | Vite + TypeScript + React |
| 对话管理 | 完成 | 创建/删除/切换对话 |
| 侧栏 UI | 完成 | 对话列表、收起侧栏 |
| 消息气泡 | 完成 | 用户/助手分色、流式渲染 |
| 呼吸灯指示器 | 完成 | 连接/状态/错误视觉反馈 |

### Phase 2 — 语音系统

| 任务 | 状态 | 说明 |
|------|------|------|
| Web Speech API 语音识别 | 完成 | useSpeechRecognition hook |
| Whisper 本地 STT | 完成 | 本地 whisper-tiny 模型 |
| TTS 语音合成 | 完成 | synthesizeSpeech API |
| 音频播放器 | 完成 | useAudioPlayer hook |
| 语音控制面板 | 完成 | VoiceControl 组件 |
| 麦克风录音 | 完成 | useAudioRecorder hook |

### Phase 3 — 人设与记忆

| 任务 | 状态 | 说明 |
|------|------|------|
| 人设系统 | 完成 | persona.py + 设置面板 |
| 对话记忆 | 完成 | memory.py + 路由 |
| 情绪分类 | 完成 | emotionClassifier.ts |
| 情感状态驱动 UI | 完成 | JARVIS 状态反馈 |

### Phase 4 — 插件与工具

| 任务 | 状态 | 说明 |
|------|------|------|
| 插件框架 | 完成 | base/manager/registry |
| Agent 智能体 | 完成 | agent.py |
| 内置工具集 | 完成 | builtins + tools 路由 |
| 插件管理 UI | 完成 | PluginManagement 组件 |

### Phase 5 — 多模态

| 任务 | 状态 | 说明 |
|------|------|------|
| 图片上传 | 完成 | 拖拽/粘贴/按钮 |
| 文件上传 | 完成 | 文件附件消息 |
| HTML 代码预览 | 完成 | 检测 + 工具栏 + iframe 沙箱 |
| 代码块工具栏 | 完成 | 预览/复制/打开按钮 |

### Phase 6 — 优化与发布

| 任务 | 状态 | 说明 |
|------|------|------|
| i18n 国际化 | 完成 | 150+ 条目，中/英切换 |
| 隐私 API | 完成 | 统计/导出/清除 |
| Debug 中间件 | 完成 | 环境信息端点 |
| 自动更新检查 | 完成 | update 路由 |
| 单元测试 | 完成 | 11 个 API 测试通过 |
| 性能测试 | 完成 | 所有端点 <12ms |
| 打包脚本 | 完成 | scripts/build.py |
| 代码分割 | 完成 | `VoiceControl`、`SettingsPanel` 懒加载 |

### Phase 7 ~ 10 — 编号保留

> 这些阶段编号当前未分配具体功能，保留用于后续规划。

### Phase 11 — 浏览器自动化与区域截图

| 任务 | 状态 | 说明 |
|------|------|------|
| Playwright 与 Chromium 安装 | 完成 | 无头浏览器依赖 |
| 浏览器工具模块 | 完成 | `browser_tools.py`：open/extract/screenshot/click/close |
| 浏览器工具 API 路由 | 完成 | `/api/browser/*` + `/api/screenshot` |
| Electron 全屏区域选择器 | 完成 | `electron/region-selector.html` + IPC |
| 前端区域截图 UI | 完成 | `RegionSelector.tsx` + `ChatArea` 集成 |
| 前端区域截图 API | 完成 | `captureRegionScreenshot` in `src/lib/api.ts` |

### Phase 12 — 三层模型路由与成本看板

| 任务 | 状态 | 说明 |
|------|------|------|
| 三层模型策略设计 | 完成 | fast/mid/deep 档位定义与路由规则 |
| 模型路由中间件/服务 | 完成 | `model_router.py` + `llm.py` 集成 |
| 调用成本记录与统计 API | 完成 | `llm_calls` 表 + `/api/costs/*` 端点 |
| 前端成本看板页面 | 完成 | `CostDashboard.tsx` + API 封装 + 样式 |
| 模型档位持久化 | 完成 | `ModelProfile.tier` 字段与数据库同步 |

### 3D 全息数字人

| 任务 | 状态 | 说明 |
|------|------|------|
| Three.js 3D 渲染管线 | 完成 | R3F + 自定义 GLSL |
| 线框头部 + 下颌动画 | 完成 | IcosahedronGeometry 分离 |
| 扫描线 + 菲涅尔着色器 | 完成 | 自定义 vertex/fragment |
| 发光双眼 + 眨眼 | 完成 | 自动 3-5 秒周期 |
| 3D 声波环 | 完成 | 64 点 BufferGeometry |
| 悬浮粒子系统 | 完成 | 200 粒子弹跳 |
| 轨道环 | 完成 | 双环反向旋转 |
| 6 状态颜色/动画 | 完成 | idle/listening/thinking/speaking/error/offline |
| 对话布局重构 | 完成 | 数字人居中 + 消息可折叠 |

---

## 已完成（新增）

| 任务 | 优先级 | 说明 |
|------|--------|------|
| AI 回复空内容修复 | 高 | 根因：`agnes-15` 内置模型指向不可用的 `agnes-1.5-flash`；已改为 `agnes-2.0-flash`，并新增模型不可用时的运行时 profile 降级 |
| Speaking 状态验证 | 中 | AI 已正常返回内容，Speaking 动画触发链路已恢复 |

## 进行中

| 任务 | 优先级 | 说明 |
|------|--------|------|

---

## 待开发

### 短期优化

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 清理废弃 CSS | 低 | index.css 中 DigitalAvatar 相关样式（约 150 行） |
| 删除 avatar-face.jpg | 低 | 旧版 CSS 数字人图片，已被 3D 全息头像替代 |
| HolographicAvatar 细节打磨 | 中 | 说话时头部微动、嘴唇开合更自然 |

### 功能增强

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 3D 球体模式切换 | 中 | 保留旧版 3D 球体作为可选模式 |
| 全屏预览 | 低 | HTML 预览支持全屏模式 |
| 语音降噪 | 中 | 麦克风录音前处理降噪 |
| 对话导出 | 中 | 导出对话记录为 Markdown/PDF |
| 主题切换 | 中 | 暗色/亮色主题切换 |
| 移动端适配 | 中 | 响应式布局优化 |
| 快捷键系统 | 低 | 键盘快捷操作 |
| 插件市场 | 低 | 在线插件浏览/安装 |

### 长期规划

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 本地 RAG 知识库 | 中 | 本地文档检索增强 |
| 多用户支持 | 低 | 用户登录/权限系统 |
| 桌面客户端打包 | 中 | Electron 打包独立应用 |
| 云端同步 | 低 | 对话/设置云端备份 |
