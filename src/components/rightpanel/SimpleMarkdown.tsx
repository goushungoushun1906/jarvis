import { useMemo } from "react";

interface Block {
  type: string;
  content: string;
  language?: string;
}

/**
 * 极简 Markdown 渲染器。
 *
 * 只覆盖标题 / 列表 / 代码块 / 段落——够展示 AI 产出的文档，又不值得为此
 * 引入一整个 markdown 库（还会带来 XSS 面）。**不做 HTML 透传**。
 */
export default function SimpleMarkdown({ source }: { source: string }) {
  const blocks = useMemo<Block[]>(() => {
    const lines = source.split("\n");
    const result: Block[] = [];
    let codeBuffer: string[] = [];
    let codeLang = "";
    let inCode = false;

    for (const rawLine of lines) {
      if (rawLine.startsWith("```")) {
        if (inCode) {
          result.push({ type: "code", content: codeBuffer.join("\n"), language: codeLang });
          codeBuffer = [];
          codeLang = "";
          inCode = false;
        } else {
          inCode = true;
          codeLang = rawLine.slice(3).trim();
        }
        continue;
      }
      if (inCode) {
        codeBuffer.push(rawLine);
        continue;
      }
      if (rawLine.startsWith("# ")) result.push({ type: "h1", content: rawLine.slice(2) });
      else if (rawLine.startsWith("## ")) result.push({ type: "h2", content: rawLine.slice(3) });
      else if (rawLine.startsWith("### ")) result.push({ type: "h3", content: rawLine.slice(4) });
      else if (rawLine.startsWith("- ") || rawLine.startsWith("* ")) result.push({ type: "li", content: rawLine.slice(2) });
      else if (rawLine.trim() === "") result.push({ type: "br", content: "" });
      else result.push({ type: "p", content: rawLine });
    }
    if (inCode && codeBuffer.length) {
      result.push({ type: "code", content: codeBuffer.join("\n"), language: codeLang });
    }
    return result;
  }, [source]);

  return (
    <div className="simple-markdown">
      {blocks.map((block, idx) => {
        if (block.type === "h1") return <h1 key={idx}>{block.content}</h1>;
        if (block.type === "h2") return <h2 key={idx}>{block.content}</h2>;
        if (block.type === "h3") return <h3 key={idx}>{block.content}</h3>;
        if (block.type === "li") return <li key={idx}>{block.content}</li>;
        if (block.type === "br") return <div key={idx} className="md-spacer" />;
        if (block.type === "code") {
          return (
            <pre key={idx} className="md-code-block">
              {block.language && <div className="md-code-lang">{block.language}</div>}
              <code>{block.content}</code>
            </pre>
          );
        }
        return <p key={idx}>{block.content}</p>;
      })}
    </div>
  );
}
