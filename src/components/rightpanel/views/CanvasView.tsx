import { useState } from "react";
import type { ViewProps } from "../types";

interface Node {
  id: number;
  x: number;
  y: number;
  text: string;
}

/** 自由画布：可添加/清空的便签节点。 */
export default function CanvasView({ tab }: ViewProps) {
  void tab;
  const [elements, setElements] = useState<Node[]>([
    { id: 1, x: 40, y: 40, text: "想法 1" },
    { id: 2, x: 200, y: 120, text: "想法 2" },
  ]);
  const [nextId, setNextId] = useState(3);

  const addNode = () => {
    setElements((prev) => [
      ...prev,
      { id: nextId, x: 80 + nextId * 20, y: 80 + nextId * 15, text: `想法 ${nextId}` },
    ]);
    setNextId((id) => id + 1);
  };

  return (
    <div className="canvas-view">
      <div className="canvas-toolbar">
        <button className="canvas-tool" onClick={addNode}>
          + 添加节点
        </button>
        <button className="canvas-tool" onClick={() => setElements([])}>
          清空
        </button>
      </div>
      <div className="canvas-board">
        {elements.map((el) => (
          <div key={el.id} className="canvas-node" style={{ left: el.x, top: el.y }}>
            {el.text}
          </div>
        ))}
      </div>
    </div>
  );
}
