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
export async function streamChat(
  payload: ChatRequest,
  signal: AbortSignal,
  onChunk: (content: string) => void,
  onError: (err: string) => void,
  onDone: () => void
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
  let buffer = ""; // accumulate partial lines across chunks

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      // Keep the last element as it may be an incomplete line
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6).trim();
          if (data === "[DONE]") {
            onDone();
            return;
          }
          try {
            const parsed = JSON.parse(data);
            if (parsed.error) {
              const detail = parsed.detail ? ` (${parsed.detail})` : "";
              onError(parsed.error + detail);
            } else if (parsed.content) {
              onChunk(parsed.content);
            }
          } catch {
            // skip non-JSON lines
          }
        }
      }
    }
    // Process any remaining data in buffer
    if (buffer.trim()) {
      if (buffer.trim().startsWith("data: ")) {
        const data = buffer.trim().slice(6).trim();
        if (data === "[DONE]") {
          onDone();
        } else {
          try {
            const parsed = JSON.parse(data);
            if (parsed.content) onChunk(parsed.content);
          } catch { /* skip */ }
        }
      }
    }
    onDone();
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

export async function deleteModel(profileId: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/config/models/${profileId}`, { method: "DELETE" });
  if (!resp.ok) throw new Error("Failed to delete model");
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
export async function serverRecordStart(): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/voice/record/start`, { method: "POST" });
  if (!resp.ok) throw new Error("Failed to start server recording");
}

export async function serverRecordStop(): Promise<{ text: string; duration: number }> {
  const resp = await fetch(`${API_BASE}/api/voice/record/stop`, { method: "POST" });
  if (!resp.ok) throw new Error("Failed to stop server recording");
  return resp.json();
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
