/**
 * Lightweight i18n system for JARVIS (no external dependencies).
 * Supports zh-CN (default) and en locales.
 */

/** Supported locales */
export type Locale = "zh-CN" | "en";

/** Translation map type */
export type Translations = Record<string, string>;

// ────────────────────────────────────────────────────────────
// Chinese (default) translations
// ────────────────────────────────────────────────────────────
const zhCN: Translations = {
  // ── SettingsPanel ──
  "settings.title": "设置",
  "settings.closeSettings": "关闭设置",
  "settings.tab.models": "模型",
  "settings.tab.persona": "人设",
  "settings.tab.plugins": "插件",
  "settings.tab.privacy": "隐私",

  // Models tab
  "settings.models.switchSection": "模型切换",
  "settings.models.currentModel": "当前模型",
  "settings.models.currentBadge": "当前",
  "settings.models.titleActive": "当前使用的模型",
  "settings.models.titleClickSwitch": "点击切换到",
  "settings.models.deleteModel": "删除此模型",
  "settings.models.customSection": "自定义模型",
  "settings.models.displayName": "显示名称",
  "settings.models.modelId": "模型 ID",
  "settings.models.baseUrl": "Base URL",
  "settings.models.apiKeyOptional": "API Key (可选)",
  "settings.models.apiKeyPlaceholder": "留空则使用全局密钥",
  "settings.models.showKey": "显示",
  "settings.models.hideKey": "隐藏",
  "settings.models.addModel": "添加模型",
  "settings.models.adding": "添加中...",
  "settings.models.customProvider": "自定义",
  "settings.models.switched": "模型已切换",
  "settings.models.switchFailed": "切换失败",
  "settings.models.unknownError": "未知错误",
  "settings.models.customAdded": "自定义模型已添加",
  "settings.models.addFailed": "添加失败",

  // Persona tab
  "settings.persona.section": "人设设置",
  "settings.persona.aiName": "AI 名称",
  "settings.persona.toneStyle": "语气风格",
  "settings.persona.toneProfessional": "专业",
  "settings.persona.toneCasual": "随和",
  "settings.persona.toneHumorous": "幽默",
  "settings.persona.toneFormal": "正式",
  "settings.persona.ownerTitle": "如何称呼用户",
  "settings.persona.selfTitle": "AI 自称",
  "settings.persona.save": "保存人设设置",
  "settings.persona.saving": "保存中...",
  "settings.persona.saved": "人设设置已保存",
  "settings.persona.saveFailed": "保存失败",

  // About section
  "settings.about.section": "关于",
  "settings.about.version": "版本",
  "settings.about.connectionStatus": "连接状态",
  "settings.about.connected": "已连接",
  "settings.about.disconnected": "已断开",
  "settings.about.detecting": "检测中...",
  "settings.about.currentModel": "当前模型",
  "settings.about.notSet": "未设置",
  "settings.about.testConnection": "测试连接",
  "settings.about.testing": "测试中...",
  "settings.about.connectSuccess": "连接成功",
  "settings.about.connectFailed": "连接失败",
  "settings.about.unknownVersion": "未知",

  // Privacy & Data tab
  "settings.privacy.section": "隐私与数据",
  "settings.privacy.dataStats": "数据统计",
  "settings.privacy.conversationCount": "对话数量",
  "settings.privacy.messageCount": "消息总数",
  "settings.privacy.dbSize": "数据库大小",
  "settings.privacy.exportData": "导出数据",
  "settings.privacy.clearAllData": "清除所有数据",
  "settings.privacy.clearConfirm": "确定要清除所有数据吗？此操作不可恢复。",
  "settings.privacy.confirmClear": "确认清除",
  "settings.privacy.cancel": "取消",
  "settings.privacy.clearing": "清除中...",
  "settings.privacy.processing": "处理中...",
  "settings.privacy.noData": "无数据",
  "settings.privacy.loading": "加载中...",

  // Privacy messages
  "settings.privacy.statsFailed": "获取数据统计失败",
  "settings.privacy.statsFailedConnect": "获取数据统计失败: 无法连接服务器",
  "settings.privacy.exportFailed": "导出失败: 服务器返回错误",
  "settings.privacy.exportFailedConnect": "导出失败: 无法连接服务器",
  "settings.privacy.exportSuccess": "数据导出成功",
  "settings.privacy.clearFailed": "清除数据失败: 服务器返回错误",
  "settings.privacy.clearFailedConnect": "清除数据失败: 无法连接服务器",
  "settings.privacy.cleared": "所有数据已清除",

  // Success/error check helper
  "common.success": "成功",
  "common.failed": "失败",

  // ── Sidebar ──
  "sidebar.newChat": "新建对话",
  "sidebar.deleteConversation": "删除对话",
  "sidebar.collapse": "收起侧栏",
  "sidebar.expand": "展开侧栏",
  "sidebar.settings": "设置",
  "sidebar.noConversations": "暂无对话记录",
  "sidebar.newConversation": "新对话",
  "sidebar.justNow": "刚刚",
  "sidebar.minutesAgo": "分钟前",
  "sidebar.hoursAgo": "小时前",
  "sidebar.daysAgo": "天前",

  // ── ChatArea ──
  "chat.inputPlaceholder": "输入消息...",
  "chat.sendMessage": "发送消息",
  "chat.jarvis": "JARVIS",
  "chat.you": "用户",
  "chat.system": "系统",
  "chat.image": "图片",
  "chat.file": "文件",
  "chat.uploadImage": "上传图片",
  "chat.uploadFile": "上传文件",
  "chat.enterSend": "Enter 发送，Shift+Enter 换行",
  "chat.charCount": "字",
  "chat.stopGenerating": "停止生成",
  "chat.stopReading": "停止朗读",
  "chat.modelNotSet": "未设置模型",
  "chat.connected": "已连接",
  "chat.disconnected": "已断开",
  "chat.detecting": "检测中...",
  "chat.emptyTagline": "有什么可以帮你的？",
  "chat.emptyHint": "我是 JARVIS，你的 AI 助手",
  "chat.listeningBanner": "正在聆听，请说话...",

  // ── MessageBubble ──
  "message.readThis": "朗读此消息",
  "message.stopReading": "停止朗读",
  "message.forkFromHere": "从此处分叉对话",

  // ── PluginManagement ──
  "plugin.title": "插件管理",
  "plugin.install": "安装插件 (.zip)",
  "plugin.noPlugins": "暂无插件",
  "plugin.enabled": "已启用",
  "plugin.disabled": "已禁用",
  "plugin.reload": "重载",
  "plugin.configure": "配置",
  "plugin.uninstall": "卸载",
  "plugin.commands": "可用命令",
  "plugin.loading": "加载中...",
  "plugin.error": "错误:",
  "plugin.config.title": "配置:",
  "plugin.config.save": "保存",
  "plugin.config.cancel": "取消",
  "plugin.config.saving": "保存中...",
  "plugin.config.saved": "配置已保存",
  "plugin.config.saveFailed": "保存失败",
  "plugin.uninstall.confirm": "确定要卸载插件",

  // ── VoiceControl ──
  "voice.modeOff": "语音关闭",
  "voice.modeAuto": "自动朗读",
  "voice.modeManual": "手动朗读",
  "voice.settings": "语音设置",
  "voice.voice": "语音",
  "voice.readMode": "朗读模式",
  "voice.defaultVoice": "默认语音",
  "voice.clickStartInput": "点击开始语音输入",
  "voice.clickStopRecording": "点击停止录音",
  "voice.recognizing": "正在识别中...",
  "voice.listening": "正在聆听...",
  "voice.transcribing": "正在识别...",
  "voice.sttFailed": "语音识别失败",
  "voice.recordFailed": "无法启动录音（请确认后端有麦克风访问权限）",
  "voice.serverRecording": "[服务器端录音中...]",
  "voice.errorLabel": "[错误]",
};

// ────────────────────────────────────────────────────────────
// English translations
// ────────────────────────────────────────────────────────────
const en: Translations = {
  // ── SettingsPanel ──
  "settings.title": "Settings",
  "settings.closeSettings": "Close settings",
  "settings.tab.models": "Models",
  "settings.tab.persona": "Persona",
  "settings.tab.plugins": "Plugins",
  "settings.tab.privacy": "Privacy & Data",

  // Models tab
  "settings.models.switchSection": "Model Switch",
  "settings.models.currentModel": "Current Model",
  "settings.models.currentBadge": "Active",
  "settings.models.titleActive": "Currently active model",
  "settings.models.titleClickSwitch": "Click to switch to",
  "settings.models.deleteModel": "Delete this model",
  "settings.models.customSection": "Custom Model",
  "settings.models.displayName": "Display Name",
  "settings.models.modelId": "Model ID",
  "settings.models.baseUrl": "Base URL",
  "settings.models.apiKeyOptional": "API Key (Optional)",
  "settings.models.apiKeyPlaceholder": "Leave empty to use global key",
  "settings.models.showKey": "Show",
  "settings.models.hideKey": "Hide",
  "settings.models.addModel": "Add Model",
  "settings.models.adding": "Adding...",
  "settings.models.customProvider": "Custom",
  "settings.models.switched": "Model switched",
  "settings.models.switchFailed": "Switch failed",
  "settings.models.unknownError": "Unknown error",
  "settings.models.customAdded": "Custom model added",
  "settings.models.addFailed": "Add failed",

  // Persona tab
  "settings.persona.section": "Persona Settings",
  "settings.persona.aiName": "AI Name",
  "settings.persona.toneStyle": "Tone Style",
  "settings.persona.toneProfessional": "Professional",
  "settings.persona.toneCasual": "Casual",
  "settings.persona.toneHumorous": "Humorous",
  "settings.persona.toneFormal": "Formal",
  "settings.persona.ownerTitle": "How to Address User",
  "settings.persona.selfTitle": "AI Self-Title",
  "settings.persona.save": "Save Persona",
  "settings.persona.saving": "Saving...",
  "settings.persona.saved": "Persona settings saved",
  "settings.persona.saveFailed": "Save failed",

  // About section
  "settings.about.section": "About",
  "settings.about.version": "Version",
  "settings.about.connectionStatus": "Connection Status",
  "settings.about.connected": "Connected",
  "settings.about.disconnected": "Disconnected",
  "settings.about.detecting": "Detecting...",
  "settings.about.currentModel": "Current Model",
  "settings.about.notSet": "Not set",
  "settings.about.testConnection": "Test Connection",
  "settings.about.testing": "Testing...",
  "settings.about.connectSuccess": "Connection successful",
  "settings.about.connectFailed": "Connection failed",
  "settings.about.unknownVersion": "Unknown",

  // Privacy & Data tab
  "settings.privacy.section": "Privacy & Data",
  "settings.privacy.dataStats": "Data Statistics",
  "settings.privacy.conversationCount": "Conversations",
  "settings.privacy.messageCount": "Messages",
  "settings.privacy.dbSize": "Database Size",
  "settings.privacy.exportData": "Export Data",
  "settings.privacy.clearAllData": "Clear All Data",
  "settings.privacy.clearConfirm": "Are you sure you want to clear all data? This action cannot be undone.",
  "settings.privacy.confirmClear": "Confirm Clear",
  "settings.privacy.cancel": "Cancel",
  "settings.privacy.clearing": "Clearing...",
  "settings.privacy.processing": "Processing...",
  "settings.privacy.noData": "No data",
  "settings.privacy.loading": "Loading...",

  // Privacy messages
  "settings.privacy.statsFailed": "Failed to fetch statistics",
  "settings.privacy.statsFailedConnect": "Failed to fetch statistics: cannot connect to server",
  "settings.privacy.exportFailed": "Export failed: server error",
  "settings.privacy.exportFailedConnect": "Export failed: cannot connect to server",
  "settings.privacy.exportSuccess": "Data exported successfully",
  "settings.privacy.clearFailed": "Clear failed: server error",
  "settings.privacy.clearFailedConnect": "Clear failed: cannot connect to server",
  "settings.privacy.cleared": "All data cleared",

  // Success/error check helper
  "common.success": "success",
  "common.failed": "failed",

  // ── Sidebar ──
  "sidebar.newChat": "New Chat",
  "sidebar.deleteConversation": "Delete conversation",
  "sidebar.collapse": "Collapse Sidebar",
  "sidebar.expand": "Expand Sidebar",
  "sidebar.settings": "Settings",
  "sidebar.noConversations": "No conversations",
  "sidebar.newConversation": "New Chat",
  "sidebar.justNow": "Just now",
  "sidebar.minutesAgo": "min ago",
  "sidebar.hoursAgo": "hours ago",
  "sidebar.daysAgo": "days ago",

  // ── ChatArea ──
  "chat.inputPlaceholder": "Type a message...",
  "chat.sendMessage": "Send message",
  "chat.jarvis": "JARVIS",
  "chat.you": "You",
  "chat.system": "System",
  "chat.image": "Image",
  "chat.file": "File",
  "chat.uploadImage": "Upload image",
  "chat.uploadFile": "Upload file",
  "chat.enterSend": "Enter to send, Shift+Enter for new line",
  "chat.charCount": "chars",
  "chat.stopGenerating": "Stop generating",
  "chat.stopReading": "Stop reading",
  "chat.modelNotSet": "Model not set",
  "chat.connected": "Connected",
  "chat.disconnected": "Disconnected",
  "chat.detecting": "Detecting...",
  "chat.emptyTagline": "How can I help you?",
  "chat.emptyHint": "I am JARVIS, your AI assistant",
  "chat.listeningBanner": "Listening, please speak...",

  // ── MessageBubble ──
  "message.readThis": "Read this message",
  "message.stopReading": "Stop reading",
  "message.forkFromHere": "Fork conversation from here",

  // ── PluginManagement ──
  "plugin.title": "Plugin Management",
  "plugin.install": "Install Plugin (.zip)",
  "plugin.noPlugins": "No plugins installed",
  "plugin.enabled": "Enabled",
  "plugin.disabled": "Disabled",
  "plugin.reload": "Reload",
  "plugin.configure": "Configure",
  "plugin.uninstall": "Uninstall",
  "plugin.commands": "Available Commands",
  "plugin.loading": "Loading...",
  "plugin.error": "Error:",
  "plugin.config.title": "Configure:",
  "plugin.config.save": "Save",
  "plugin.config.cancel": "Cancel",
  "plugin.config.saving": "Saving...",
  "plugin.config.saved": "Configuration saved",
  "plugin.config.saveFailed": "Save failed",
  "plugin.uninstall.confirm": "Are you sure you want to uninstall plugin",

  // ── VoiceControl ──
  "voice.modeOff": "Voice Off",
  "voice.modeAuto": "Auto Read",
  "voice.modeManual": "Manual Read",
  "voice.settings": "Voice Settings",
  "voice.voice": "Voice",
  "voice.readMode": "Read Mode",
  "voice.defaultVoice": "Default Voice",
  "voice.clickStartInput": "Click to start voice input",
  "voice.clickStopRecording": "Click to stop recording",
  "voice.recognizing": "Recognizing",
  "voice.listening": "Listening...",
  "voice.transcribing": "Transcribing...",
  "voice.sttFailed": "Speech recognition failed",
  "voice.recordFailed": "Cannot start recording (ensure backend has microphone access)",
  "voice.serverRecording": "[Server-side recording...]",
  "voice.errorLabel": "[Error]",
};

// ────────────────────────────────────────────────────────────
// i18n engine
// ────────────────────────────────────────────────────────────

const STORAGE_KEY = "jarvis-locale";

const allTranslations: Record<Locale, Translations> = { "zh-CN": zhCN, en };

let _currentLocale: Locale = (localStorage.getItem(STORAGE_KEY) as Locale) || "zh-CN";
const _listeners: Array<(locale: Locale) => void> = [];

/** Look up a translation key. Falls back to the key itself if not found. */
export function t(key: string): string {
  return allTranslations[_currentLocale][key] ?? key;
}

/** Get the current locale. */
export function getLocale(): Locale {
  return _currentLocale;
}

/** Switch the active locale and persist the choice. */
export function setLocale(locale: Locale): void {
  if (locale === _currentLocale) return;
  _currentLocale = locale;
  localStorage.setItem(STORAGE_KEY, locale);
  _listeners.forEach((fn) => fn(locale));
}

/** Subscribe to locale changes. Returns an unsubscribe function. */
export function onLocaleChange(callback: (locale: Locale) => void): () => void {
  _listeners.push(callback);
  return () => {
    const idx = _listeners.indexOf(callback);
    if (idx >= 0) _listeners.splice(idx, 1);
  };
}