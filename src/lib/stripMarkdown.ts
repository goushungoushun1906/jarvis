/**
 * Convert Markdown-formatted text to plain text suitable for TTS.
 * Removes syntax symbols while preserving readable content.
 */

export function stripMarkdown(md: string): string {
  if (!md) return "";

  let text = md;

  // Remove code blocks (```...```) — replace with brief placeholder or just remove
  text = text.replace(/```[\s\S]*?```/g, " [代码块已省略] ");

  // Remove inline code (`...`)
  text = text.replace(/`([^`]+)`/g, "$1");

  // Remove images ![alt](url) — keep alt text
  text = text.replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1");

  // Remove links [text](url) — keep text
  text = text.replace(/\[([^\]]*)\]\([^)]+\)/g, "$1");

  // Remove bold/italic markers: ***text***, **text**, *text*
  text = text.replace(/\*{3}(.+?)\*{3}/g, "$1");
  text = text.replace(/\*{2}(.+?)\*{2}/g, "$1");
  text = text.replace(/\*(.+?)\*/g, "$1");

  // Remove underline markers: __text__, _text_
  text = text.replace(/__(.+?)__/g, "$1");
  text = text.replace(/_(.+?)_/g, "$1");

  // Remove strikethrough: ~~text~~
  text = text.replace(/~~(.+?)~~/g, "$1");

  // Remove headings: ## Heading → "Heading"
  text = text.replace(/^#{1,6}\s+/gm, "");

  // Remove blockquotes: > text → "text"
  text = text.replace(/^>\s?/gm, "");

  // Remove horizontal rules: ---, ***, ___
  text = text.replace(/^[-*_]{3,}\s*$/gm, "");

  // Remove unordered list markers: - item, * item, + item
  text = text.replace(/^\s*[-*+]\s+/gm, "");

  // Remove ordered list markers: 1. item
  text = text.replace(/^\s*\d+\.\s+/gm, "");

  // Clean up excessive whitespace (but keep single newlines)
  text = text.replace(/\n{3,}/g, "\n\n");
  text = text.replace(/[ \t]+/g, " ");

  return text.trim();
}
