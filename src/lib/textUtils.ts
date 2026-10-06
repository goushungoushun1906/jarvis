/**
 * Text helpers for streamed chat content.
 */

/**
 * Matches the fallback tool-call form the model emits when it does NOT use
 * native function calling:
 *
 *     {"tool": "calculator", "args": {"expression": "1+1"}}
 *
 * The backend's regex fallback (`_extract_tool_calls`) turns this into a real
 * tool call, but the same characters were already streamed as *text*, so the
 * user would otherwise see raw JSON in the middle of the answer.
 */
const EMBEDDED_TOOL_JSON =
  /\{"tool"\s*:\s*"[\w.-]+"\s*,\s*"args"\s*:\s*(?:\{(?:[^{}]|\{[^{}]*\})*\}|\[[\s\S]*?\])\s*\}/g;

/**
 * Remove embedded `{"tool": ..., "args": ...}` fragments from displayed text.
 *
 * Only *complete* matches are removed, so a partial fragment still arriving
 * chunk-by-chunk is left alone until it closes.
 */
export function stripToolCallJson(text: string): string {
  if (!text || !text.includes('"tool"')) return text;
  return text.replace(EMBEDDED_TOOL_JSON, "");
}
