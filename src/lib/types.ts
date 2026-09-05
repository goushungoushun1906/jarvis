// ====== Message ======
export interface Message {
  role: "user" | "assistant" | "system";
  content: string;
  id?: string;
  created_at?: string;
  images?: ImageAttachment[];
  files?: FileAttachment[];
}

// ====== Conversation ======
export interface Conversation {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  messages?: Message[];
  parent_id?: string | null;
  forked_from_message_id?: number | null;
}

// ====== Attachments ======
export interface ImageAttachment {
  type: "image_url";
  image_url: { url: string };
  filename?: string;
}

export interface FileAttachment {
  type: "file";
  filename: string;
  mime_type: string;
  extracted_text: string;
}

export interface ImageUploadResult {
  data_url: string;
  filename: string;
  mime_type: string;
}

export interface FileUploadResult {
  filename: string;
  mime_type: string;
  extracted_text: string;
  char_count: number;
}

// ====== Chat Request / Response ======
export interface ChatRequest {
  messages: Message[];
  stream?: boolean;
  conversation_id?: string;
}

export interface ChatResponse {
  content: string;
  role: string;
  conversation_id?: string;
  tool_calls?: ToolCallRecord[];
}

// ====== Tool Calls ======
export interface ToolCallRecord {
  tool: string;
  args: Record<string, unknown>;
  result: string;
  success: boolean;
}

// ====== SSE Stream Chunk ======
export interface StreamChunk {
  content?: string;
  error?: string;
  done?: boolean;
}

// ====== Model Config ======
export interface ModelConfig {
  model: string;
  base_url?: string;
}

// ====== Persona Config ======
export interface PersonaConfig {
  name: string;
  tone: string;
  owner_title: string;
  self_title: string;
}

// ====== Model Profiles ======
export type ModelTier = "fast" | "mid" | "deep";

export interface ModelProfile {
  id: string;
  name: string;
  model: string;
  base_url: string;
  api_key?: string;
  provider: string;
  is_active: boolean;
  tier: ModelTier;
}

export interface ModelConfigInfo {
  model: string;
  base_url: string;
  active_profile?: ModelProfile;
}

// ====== Full Config ======
export interface AppConfig {
  model_config: ModelConfigInfo;
  persona_config: PersonaConfig;
  available_models?: { id: string; name: string; provider: string; is_active: boolean; tier: ModelTier }[];
}

// ====== Cost / Usage Dashboard ======
export interface LLMCallRecord {
  id: number;
  timestamp: string;
  model: string;
  tier: ModelTier;
  profile_id: string | null;
  input_chars: number;
  output_chars: number;
  duration_ms: number;
  success: boolean;
  error: string | null;
  cost_estimate: number;
}

export interface CostsSummary {
  days: number;
  total_calls: number;
  successful_calls: number;
  failed_calls: number;
  total_input_chars: number;
  total_output_chars: number;
  total_duration_ms: number;
  total_cost: number;
  avg_duration_ms: number;
}

export interface CostsByModel {
  model: string;
  tier: ModelTier;
  calls: number;
  input_chars: number;
  output_chars: number;
  duration_ms: number;
  cost: number;
  successful: number;
}

export interface CostsByDay {
  day: string;
  calls: number;
  cost: number;
  chars: number;
}

// ====== Health ======
export interface HealthResponse {
  status: string;
  version?: string;
}

// ====== App State ======
export type ConnectionStatus = "checking" | "online" | "offline";
export type JavisStatus = "idle" | "thinking" | "speaking" | "error";
export type ToneOption = "professional" | "casual" | "humorous" | "formal";

// ====== Voice ======
export interface VoiceInfo {
  id: string;
  lang: string;
  gender: string;
  desc: string;
}

export type VoiceMode = "off" | "manual" | "auto";
// off = never play voice
// manual = click play button on individual messages to play TTS
// auto = auto-play TTS for all AI responses

// ====== Skills ======
export interface Skill {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  requires_input: boolean;
  input_label: string;
  input_placeholder: string;
  default_input: string;
}
