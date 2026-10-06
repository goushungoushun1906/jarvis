import { useCallback, useState } from "react";
import type { RightPanelTab } from "../lib/types";

/** 常驻的画布页签不可关闭，且永远是回退目标。 */
export const CANVAS_TAB_ID = "canvas-default";

const INITIAL_TABS: RightPanelTab[] = [
  { id: CANVAS_TAB_ID, title: "画布", type: "canvas", closable: false },
];

export interface RightPanelController {
  tabs: RightPanelTab[];
  activeTabId: string;
  collapsed: boolean;
  /** 打开或刷新一个页签（同 id 会合并而不是叠加）。 */
  open(tab: RightPanelTab): void;
  close(id: string): void;
  activate(id: string): void;
  toggleCollapse(): void;
  setCollapsed(value: boolean): void;
  /** 在系统浏览器里打开页签内容。 */
  openExternal(tab: RightPanelTab): void;
}

/** 右侧展示面板的页签状态机。 */
export function useRightPanel(): RightPanelController {
  const [tabs, setTabs] = useState<RightPanelTab[]>(INITIAL_TABS);
  const [activeTabId, setActiveTabId] = useState<string>(CANVAS_TAB_ID);
  const [collapsed, setCollapsed] = useState(false);

  const open = useCallback((tab: RightPanelTab) => {
    setTabs((prev) => {
      const existing = prev.find((t) => t.id === tab.id);
      if (existing) {
        return prev.map((t) => (t.id === tab.id ? { ...t, ...tab } : t));
      }
      return [...prev, tab];
    });
    setActiveTabId(tab.id);
  }, []);

  const close = useCallback((id: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      setActiveTabId((current) =>
        current === id ? (next.length > 0 ? next[next.length - 1].id : CANVAS_TAB_ID) : current
      );
      return next;
    });
  }, []);

  const activate = useCallback((id: string) => setActiveTabId(id), []);

  const toggleCollapse = useCallback(() => setCollapsed((v) => !v), []);

  const openExternal = useCallback((tab: RightPanelTab) => {
    if (tab.url) {
      window.open(tab.url, "_blank", "noopener,noreferrer");
      return;
    }
    if (tab.type === "markdown" || tab.type === "code") {
      const blob = new Blob([tab.content || ""], {
        type: "text/plain;charset=utf-8",
      });
      window.open(URL.createObjectURL(blob), "_blank");
    }
  }, []);

  return {
    tabs,
    activeTabId,
    collapsed,
    open,
    close,
    activate,
    toggleCollapse,
    setCollapsed,
    openExternal,
  };
}
