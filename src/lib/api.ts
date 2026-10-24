import type {
  Conversation,
  Message,
  ChatRequest,
  AppConfig,
  HealthResponse,
  ModelConfig,
  ModelProfile,
  PersonaConfig,
  VoiceInfo,
  ImageAttachment,
  FileAttachment,
  ImageUploadResult,
  FileUploadResult,
  CostsSummary,
  LLMCallRecord,
  CostsByModel,
  CostsByDay,
  Skill,
  ToolCallRecord,
  StreamDonePayload,
} from "./types";

const API_BASE = "http://127.0.0.1:18200";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
    throw new Error(body.error || `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

// ====== Health ======
export async function checkHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/health");
}

// ====== Conversations ======
export async function listConversations(): Promise<Conversation[]> {
  return request<Conversation[]>("/api/conversations");
}

export async function getConversation(id: string): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${id}`);
}

export async function createConversation(title?: string): Promise<Conversation> {
  return request<Conversation>("/api/conversations", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  });
}

export async function deleteConversation(id: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/conversations/${id}`, { method: "DELETE" });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
    throw new Error(body.error || `HTTP ${resp.status}`);
  }
}

export async function updateConversationTitle(
  id: string,
  title: string
): Promise<void> {
  await request<void>(`/api/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

// ====== Chat (non-streaming) ======
export async function sendChat(
  payload: ChatRequest
): Promise<{ content: string; conversation_id?: string }> {
  return request("/api/chat", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ====== Chat Stream ======
/**
 * One Server-Sent Events frame: an optional `event:` name plus its `data:`
 * payload.
 *
 * Phase 17.3 — the backend emits BOTH lines for every event, so a client that
 * only reads `data:` (the pre-17.3 frontend) keeps working unchanged.
 */
interface SSEFrame {
  event: string | null;
  data: string;
}

/**
 * Split an SSE byte stream into complete frames.
 *
 * Frames are separated by a blank line; a trailing partial frame is returned
 * as `rest` and re-parsed once more bytes arrive. Heartbeat comments
 * (`: ping`) carry no `data:` line and are dropped here.
 */
function parseSSEFrames(buffer: string): { frames: SSEFrame[]; rest: string } {
  const parts = buffer.split("\n\n");
  const rest = parts.pop() ?? "";
  const frames: SSEFrame[] = [];

  for (const part of parts) {
    if (!part.trim()) continue;
    let event: string | null = null;
    let data: string | null = null;
    for (const line of part.split("\n")) {
      if (line.startsWith("event:")) {
        event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        data = line.slice(5).trim();
      }
    }
    if (data !== null) frames.push({ event, data });
  }
  return { frames, rest };
}

/** Fallback for payloads with no `event:` line — infer from the data keys. */
function inferEventName(payload: Record<string, unknown>): string | null {
  if (typeof payload.content === "string") return "text";
  if (payload.tool_call) return "tool_call";
  if (payload.tool_result) return "tool_result";
  if (payload.error) return "error";
  if (payload.conversation_id !== undefined || payload.tool_calls !== undefined) return "done";
  if (payload.thinking) return "thinking";
  return null;
}

// ====== Chat Stream ======
export async function streamChat(
  payload: ChatRequest,
  signal: AbortSignal,
  onChunk: (content: string) => void,
  onError: (err: string) => void,
  onDone: (done?: StreamDonePayload) => void,
  onToolCall?: (toolCall: ToolCallRecord) => void,
  onToolResult?: (toolCall: ToolCallRecord) => void,
  onThinking?: (thinking: string) => void
): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, stream: true }),
    signal,
  });

  if (!resp.ok) {
    const errData = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
    throw new Error(errData.error || `HTTP ${resp.status}`);
  }

  const reader = resp.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = ""; // accumulate partial frames across network chunks
  let donePayload: StreamDonePayload | undefined;
  let finished = false;

  const finish = () => {
    if (finished) return;
    finished = true;
    onDone(donePayload);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = parseSSEFrames(buffer);
      buffer = rest;

      for (const frame of frames) {
        if (frame.data === "[DONE]") {
          finish();
          return;
        }

        let parsed: Record<string, unknown>;
        try {
          parsed = JSON.parse(frame.data);
        } catch {
          continue; // skip non-JSON lines
        }

        switch (frame.event ?? inferEventName(parsed)) {
          case "text":
            if (typeof parsed.content === "string") onChunk(parsed.content);
            break;
          case "tool_call":
            onToolCall?.(parsed.tool_call as ToolCallRecord);
            break;
          case "tool_result":
            onToolResult?.(parsed.tool_result as ToolCallRecord);
            break;
          case "thinking":
            if (typeof parsed.thinking === "string") onThinking?.(parsed.thinking);
            break;
          case "error": {
            const detail =
              typeof parsed.detail === "string" && parsed.detail ? ` (${parsed.detail})` : "";
            onError(String(parsed.error) + detail);
            break;
          }
          case "done":
            // Kept until the stream terminates; surfaced via onDone.
            donePayload = parsed as StreamDonePayload;
            break;
          default:
            break;
        }
      }
    }
    // Stream ended without [DONE] (e.g. connection closed) — still notify.
    finish();
  } finally {
    reader.releaseLock();
  }
}

// ====== Config ======
export async function getConfig(): Promise<AppConfig> {
  return request<AppConfig>("/api/config");
}

export async function updateModel(config: ModelConfig): Promise<void> {
  await request<void>("/api/config/model", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export async function updatePersona(config: PersonaConfig): Promise<void> {
  await request<void>("/api/config/persona", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

// ====== Model Profiles ======
export async function listModels(): Promise<any[]> {
  const resp = await fetch(`${API_BASE}/api/config/models`);
  if (!resp.ok) throw new Error("Failed to fetch models");
  return resp.json();
}

export async function switchModel(profileId: string): Promise<ModelProfile> {
  const resp = await fetch(`${API_BASE}/api/config/models/switch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_id: profileId }),
  });
  if (!resp.ok) throw new Error("Failed to switch model");
  return resp.json();
}

export async function addModel(model: Omit<ModelProfile, "is_active">): Promise<ModelProfile> {
  const resp = await fetch(`${API_BASE}/api/config/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(model),
  });
  if (!resp.ok) throw new Error("Failed to add model");
  return resp.json();
}

export async function updateModelProfile(profileId: string, model: Partial<Omit<ModelProfile, "is_active" | "id">>): Promise<ModelProfile> {
  const resp = await fetch(`${API_BASE}/api/config/models/${profileId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(model),
  });
  if (!resp.ok) throw new Error("Failed to update model");
  return resp.json();
}

export async function deleteModel(profileId: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/config/models/${profileId}`, { method: "DELETE" });
  if (!resp.ok) throw new Error("Failed to delete model");
}

export interface ModelNameCheck {
  model: string;
  /** true = gateway accepts it, false = rejected, null = could not be checked */
  valid: boolean | null;
  /** id actually sent after server-side alias normalisation */
  normalized: string;
  /** ids the gateway advertises (empty when unreachable) */
  available: string[];
  /** closest id to use instead, when invalid */
  suggestion: string | null;
  checked: boolean;
  message: string;
}

/** Ask the backend whether a gateway actually accepts this model id. */
export async function validateModelName(params: {
  model: string;
  base_url?: string;
  api_key?: string;
  profile_id?: string;
}): Promise<ModelNameCheck> {
  const resp = await fetch(`${API_BASE}/api/config/models/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!resp.ok) throw new Error("Failed to validate model name");
  return resp.json();
}

// ====== Voice / TTS ======
export async function getVoices(): Promise<VoiceInfo[]> {
  const resp = await fetch(`${API_BASE}/api/voice/voices`);
  if (!resp.ok) throw new Error("Failed to fetch voices");
  return resp.json();
}

export async function synthesizeSpeech(text: string, voice?: string): Promise<Blob> {
  const resp = await fetch(`${API_BASE}/api/voice/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice: voice || undefined }),
  });
  if (!resp.ok) throw new Error("TTS synthesis failed");
  return resp.blob();
}

// ====== Voice / STT ======
export async function transcribeAudio(blob: Blob): Promise<string> {
  const formData = new FormData();
  formData.append("audio", blob, "recording.webm");
  const resp = await fetch(`${API_BASE}/api/voice/stt`, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
    throw new Error(body.error || `STT failed (${resp.status})`);
  }
  const data = await resp.json();
  return data.text || "";
}

// ====== Server-side recording (bypasses browser getUserMedia AGC issues) ======

export interface ServerRecordOptions {
  /** 说完自动停（VAD）：检测到人声后静音 ~700ms 即认为说完了。 */
  vad?: boolean;
  /** 一直没人开口时等多久放弃（毫秒）。 */
  leadingMs?: number;
  /** 单次采集硬上限（毫秒）。 */
  maxMs?: number;
}

export async function serverRecordStart(opts?: ServerRecordOptions): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/voice/record/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      vad: opts?.vad ?? false,
      leading_ms: opts?.leadingMs ?? 5000,
      max_ms: opts?.maxMs ?? 8000,
    }),
  });
  if (!resp.ok) throw new Error("Failed to start server recording");
}

/** 服务器录音的识别结果。
 *
 * 后端现在会区分四种情况：正常文本、录音太短、麦克风没声音、有声音但
 * 没听清。以前只返回 text，前端拿空字符串就当"什么都没发生"，用户完全
 * 看不出是权限问题还是没听清，所以把 error/idle/level 一并带回来。
 */
export interface ServerRecordResult {
  text: string;
  duration: number;
  /** VAD 模式下等了 leading 时长也没人说话——正常空闲，不是故障。 */
  idle?: boolean;
  error?: string;
  level?: number;
  level_pct?: number;
}

export async function serverRecordStop(
  opts?: { immediate?: boolean; signal?: AbortSignal },
): Promise<ServerRecordResult> {
  const resp = await fetch(`${API_BASE}/api/voice/record/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // immediate=true：VAD 采集中途手动截断（默认是"等说完再转写"）
    body: JSON.stringify({ immediate: opts?.immediate ?? false }),
    // VAD 模式下这个请求会一直挂到"说完话"为止。连续对话里一旦某个请求
    // 没回来，整个会话循环就再也没机会推进——所以必须能从外面掐断。
    signal: opts?.signal,
  });
  if (!resp.ok) throw new Error("Failed to stop server recording");
  return resp.json();
}

export interface MicCheckResult {
  duration: number;
  bytes: number;
  device: { name?: string; sample_rate?: number } | null;
  heard_sound: boolean;
  model_ready: boolean;
  level: number;
  level_pct: number;
}

/** 录一小段并回报音量，用来判断系统到底有没有放行麦克风。 */
export async function micCheck(seconds = 2): Promise<MicCheckResult> {
  return request(`/api/voice/mic-check?seconds=${seconds}`, { method: "POST" });
}

// ====== File/Image Upload ======
export async function uploadImage(file: File): Promise<ImageUploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  console.log("[uploadImage] Uploading", file.name, file.type, file.size);
  const resp = await fetch(`${API_BASE}/api/upload/image`, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
    console.error("[uploadImage] Failed:", resp.status, body);
    const msg = body.detail || body.error || body.message || "图片上传失败";
    throw new Error(msg);
  }
  const result = await resp.json();
  console.log("[uploadImage] Success:", result.filename, "data_url length:", result.data_url?.length);
  return result;
}

export async function uploadFile(file: File): Promise<FileUploadResult> {
  const formData = new FormData();
  formData.append("file", file);
  console.log("[uploadFile] Uploading", file.name, file.type, file.size);
  const resp = await fetch(`${API_BASE}/api/upload/file`, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
    console.error("[uploadFile] Failed:", resp.status, body);
    const msg = body.detail || body.error || body.message || "文件上传失败";
    throw new Error(msg);
  }
  const result = await resp.json();
  console.log("[uploadFile] Success:", result.filename, "chars:", result.char_count);
  return result;
}

// ====== Fork Conversation ======
export async function forkConversation(
  conversationId: string,
  messageId: number,
  newTitle?: string
): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${conversationId}/fork`, {
    method: "POST",
    body: JSON.stringify({ message_id: messageId, new_title: newTitle }),
  });
}

// ====== System Metrics ======
export interface SystemMetrics {
  success: boolean;
  text: string;
  metrics: {
    cpu_percent?: number;
    ram_percent?: number;
    ram_used_gb?: number;
    ram_total_gb?: number;
    disk_percent?: number;
    net_sent_kb?: number;
    net_recv_kb?: number;
    battery_percent?: number;
    battery_plugged?: boolean;
  };
}

export async function getSystemMetrics(): Promise<SystemMetrics> {
  return request<SystemMetrics>("/api/tools/system/metrics");
}

// ====== Cost / Usage Dashboard ======
export async function getCostsSummary(days: number = 30): Promise<CostsSummary> {
  return request<CostsSummary>(`/api/costs/summary?days=${days}`);
}

export async function getRecentCosts(
  limit: number = 50,
  offset: number = 0,
  model?: string,
  tier?: string
): Promise<{ calls: LLMCallRecord[]; limit: number; offset: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (model) params.set("model", model);
  if (tier) params.set("tier", tier);
  return request(`/api/costs/recent?${params.toString()}`);
}

export async function getCostsByModel(days: number = 30): Promise<{ days: number; models: CostsByModel[] }> {
  return request(`/api/costs/by-model?days=${days}`);
}

export async function getCostsByDay(days: number = 30): Promise<{ days: number; days_data: CostsByDay[] }> {
  return request(`/api/costs/by-day?days=${days}`);
}

// ====== Skills ======
export async function listSkills(): Promise<{ skills: Skill[] }> {
  return request<{ skills: Skill[] }>("/api/skills");
}

export async function invokeSkill(skillId: string, input: string = "", language: string = ""): Promise<{ success: boolean; prompt?: string; error?: string }> {
  return request<{ success: boolean; prompt?: string; error?: string }>("/api/skills/invoke", {
    method: "POST",
    body: JSON.stringify({ skill_id: skillId, input, language }),
  });
}


// ====== Region Screenshot + OCR ======
export interface RegionScreenshotResult {
  success: boolean;
  text: string;
  metadata?: {
    mode?: string;
    width?: number;
    height?: number;
    ocr?: boolean;
    [key: string]: any;
  };
}

export async function captureRegionScreenshot(
  region: { left: number; top: number; width: number; height: number },
  ocr: boolean = true
): Promise<RegionScreenshotResult> {
  const params = new URLSearchParams({
    mode: "region",
    ocr: String(ocr),
    left: String(region.left),
    top: String(region.top),
    width: String(region.width),
    height: String(region.height),
  });
  const resp = await fetch(`${API_BASE}/api/tools/screenshot?${params.toString()}`, {
    method: "POST",
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ error: `HTTP ${resp.status}` }));
    throw new Error(body.error || `HTTP ${resp.status}`);
  }
  return resp.json();
}

// ====== MCP (Model Context Protocol) ======

/** mcp_servers.json 里单个 server 的配置形状。 */
export interface McpServerEntry {
  transport?: string;
  command?: string;
  args?: string[];
  url?: string;
  env?: Record<string, string>;
  enabled?: boolean;
  description?: string;
}

/** 服务器运行状态（后端连接后才会有 tool_count > 0）。 */
export interface McpServerStatus {
  name: string;
  enabled: boolean;
  connected: boolean;
  tool_count: number;
  transport: string;
}

export interface McpTool {
  server: string;
  name: string;
  description: string;
}

export async function getMcpConfig(): Promise<{
  path: string;
  config: { servers: Record<string, McpServerEntry> };
}> {
  return request("/api/tools/mcp/config");
}

/** 保存整份配置并让后端立即重连。 */
export async function putMcpConfig(
  servers: Record<string, McpServerEntry>
): Promise<{ status: string; servers: McpServerStatus[] }> {
  return request("/api/tools/mcp/config", {
    method: "PUT",
    body: JSON.stringify({ servers }),
  });
}

export async function deleteMcpServer(
  name: string
): Promise<{ status: string; servers: McpServerStatus[] }> {
  return request(`/api/tools/mcp/servers/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

export async function listMcpServers(): Promise<McpServerStatus[]> {
  return request<McpServerStatus[]>("/api/tools/mcp/servers");
}

export async function listMcpTools(): Promise<McpTool[]> {
  return request<McpTool[]>("/api/tools/mcp/tools");
}

export async function reloadMcpServers(): Promise<{
  status: string;
  servers: McpServerStatus[];
}> {
  return request("/api/tools/mcp/reload", { method: "POST" });
}

/** 本地语音识别模型的状态（打包版模型没打进 exe 时这里会直接暴露出来）。 */
export async function getSttStatus(): Promise<{
  available: boolean;
  vosk_model: string | null;
  whisper_model: string | null;
  search_roots: string[];
  frozen: boolean;
}> {
  return request("/api/voice/stt-status");
}

// ====== 密钥存储（系统凭据管理器） ======

export type SecretSource = "keychain" | "env" | "missing";

export interface SecretsStatus {
  supported: boolean;
  managed: string[];
  sources: Record<string, SecretSource>;
}

export async function getSecretsStatus(): Promise<SecretsStatus> {
  return request("/api/config/secrets");
}

export async function saveSecret(
  name: string,
  value: string
): Promise<{ status: string; name: string; source: SecretSource }> {
  return request(`/api/config/secrets/${encodeURIComponent(name)}`, {
    method: "POST",
    body: JSON.stringify({ value }),
  });
}

export async function deleteSecret(
  name: string
): Promise<{ status: string; name: string; source: SecretSource }> {
  return request(`/api/config/secrets/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

// ====== 语音唤醒 v2（Porcupine 中文关键词） ======
//
// 旧链路是前端每 3 秒录一段、送识别、拿文本正则找「贾维斯」，延迟高且常把
// 唤醒词切在窗口边界。现在由后端常驻的关键词引擎逐帧判定，事件直接推过来，
// 前端只负责订阅与展示。接口细节见 docs/WAKE_WORD_DESIGN.md。

export interface WakeSelfCheck {
  frozen: boolean;
  meipass: string | null;
  model_path: string;
  model_exists: boolean;
  keyword_paths: string[];
  keyword_paths_exist: boolean[];
  library_path: string;
  library_exists: boolean;
  capture_dir_exists: boolean;
  pvporcupine_importable: boolean;
  pyaudio_importable: boolean;
  // --- sherpa-onnx（开放词表引擎）---
  sherpa_importable?: boolean;
  sherpa_model_dir?: string;
  sherpa_ready?: boolean;
  sherpa_keywords_src?: string;
  sherpa_keywords_src_exists?: boolean;
  sherpa_threshold?: number;
  sherpa_dll_dirs?: string[];
  sherpa_ort_version?: string;
  /** true 表示系统目录存在游离 onnxruntime.dll，是 DLL 冲突的潜在源头 */
  stray_system32_onnxruntime?: boolean;
  // --- Vosk（受限语法唤醒引擎）---
  vosk_importable?: boolean;
  vosk_model?: string;
  vosk_ready?: boolean;
  /**
   * 实际生效的唤醒语法条目（最多 8 条）。
   * 中文必须以「单字序列」书写，写错不会报错、只会 0 命中，
   * 所以要在界面上看得见，方便一眼确认语法生成对了。
   */
  vosk_grammar?: string[];
}

export interface WakeInfo {
  status: string;
  engine: string | null;
  keywords: string[];
  keyword_paths: string[];
  sensitivities: number[];
  device: number | null;
  access_key_set: boolean;
  cooldown_s: number;
  dual_threshold: boolean;
  allow_energy_fallback: boolean;
  save_captures: boolean;
  peak_confidence: number;
  peak_level: number;
  detections_total: number;
  false_alarms_marked: number;
  running: boolean;
  error: string | null;
  hint: string | null;
  self_check: WakeSelfCheck;
}

export interface WakeDevice {
  index: number;
  name: string;
  max_input_channels: number;
  default_sample_rate: number;
  host_api: string;
  is_default: boolean;
  likely_builtin: boolean;
}

export interface WakeDetection {
  keyword: string;
  index: number;
  score: number;
  level: number;
  sensitivity: number;
  engine: string | null;
  ts: number;
  capture_id: string | null;
  cooldown_s: number;
}

export interface WakeCapture {
  id: string;
  ts: number;
  keyword: string;
  score: number;
  label: string | null;
}

export async function getWakeInfo(): Promise<WakeInfo> {
  return request<WakeInfo>("/api/wakeword/info");
}

export async function startWakeword(
  deviceIndex?: number | null,
  sensitivities?: number[]
): Promise<WakeInfo> {
  return request<WakeInfo>("/api/wakeword/start", {
    method: "POST",
    body: JSON.stringify({
      device_index: deviceIndex ?? null,
      sensitivities: sensitivities ?? null,
    }),
  });
}

export async function stopWakeword(): Promise<WakeInfo> {
  return request<WakeInfo>("/api/wakeword/stop", { method: "POST" });
}

/** 热重读配置（含 AccessKey），改完不用重启后端。 */
export async function reloadWakeword(): Promise<WakeInfo> {
  return request<WakeInfo>("/api/wakeword/reload", { method: "POST" });
}

export async function listWakeDevices(): Promise<{ devices: WakeDevice[] }> {
  return request<{ devices: WakeDevice[] }>("/api/wakeword/devices");
}

export async function setWakeSensitivity(
  sensitivities: number[]
): Promise<WakeInfo> {
  return request<WakeInfo>("/api/wakeword/sensitivity", {
    method: "PUT",
    body: JSON.stringify({ sensitivities }),
  });
}

/** 播报 / 手动录音期间调用：后端继续读音频流，但暂停判定。 */
export async function suspendWakeword(): Promise<WakeInfo> {
  return request<WakeInfo>("/api/wakeword/suspend", { method: "POST" });
}

export async function resumeWakeword(): Promise<WakeInfo> {
  return request<WakeInfo>("/api/wakeword/resume", { method: "POST" });
}

export async function listWakeCaptures(): Promise<{ captures: WakeCapture[] }> {
  return request<{ captures: WakeCapture[] }>("/api/wakeword/captures");
}

export function wakeCaptureUrl(id: string): string {
  return `${API_BASE}/api/wakeword/captures/${encodeURIComponent(id)}`;
}

export async function markWakeCapture(
  id: string,
  label: "false_alarm" | "true_wake"
): Promise<void> {
  await request(`/api/wakeword/captures/${encodeURIComponent(id)}/mark`, {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}

/**
 * 离线回放一段音频看能否命中，**不占用麦克风**。
 * 打包产物没法对着喊话，验证它是否真的带上了模型文件全靠这条端点。
 */
export async function testWakeWav(wav: Blob): Promise<{
  hit: boolean;
  keyword?: string;
  score?: number;
  best_score?: number;
}> {
  const form = new FormData();
  form.append("file", wav, "sample.wav");
  const resp = await fetch(`${API_BASE}/api/wakeword/test`, {
    method: "POST",
    body: form,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

/**
 * 订阅唤醒事件流。
 *
 * 复用项目既有的 SSE 帧解析器（见本文件上方 parseSSEFrames），心跳帧（没有
 * data: 行的注释帧）会被它丢弃，不会污染这里的回调。
 */
export async function streamWakeword(
  signal: AbortSignal,
  onEvent: (type: string, payload: Record<string, unknown>) => void
): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/wakeword/stream`, { signal });
  if (!resp.ok) {
    throw new Error(`唤醒事件流不可达：HTTP ${resp.status}`);
  }
  const reader = resp.body?.getReader();
  if (!reader) throw new Error("唤醒事件流没有响应体");

  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = parseSSEFrames(buffer);
      buffer = rest;
      for (const frame of frames) {
        if (!frame.data) continue;
        try {
          onEvent(frame.event ?? "message", JSON.parse(frame.data));
        } catch {
          // 单帧解析失败不该让整条流断掉
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
