import type { RightPanelTab } from "../lib/types";
import { TAB_ICONS } from "./rightpanel/types";
import TabContent from "./rightpanel/TabContent";
import "./RightPanel.css";

interface RightPanelProps {
  tabs: RightPanelTab[];
  activeTabId: string | null;
  onActivateTab: (id: string) => void;
  onCloseTab: (id: string) => void;
  onOpenExternal?: (tab: RightPanelTab) => void;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

/** 折叠态：只剩一条竖着的展开条。 */
function CollapsedRail({ onToggleCollapse }: { onToggleCollapse?: () => void }) {
  return (
    <div className="right-panel collapsed">
      <button
        className="right-panel-collapse-btn"
        onClick={onToggleCollapse}
        title="展开展示面板"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
          <line x1="9" y1="3" x2="9" y2="21" />
        </svg>
        <span className="right-panel-collapse-label">画布</span>
      </button>
    </div>
  );
}

/** 无页签时的空态。 */
function EmptyState() {
  return (
    <div className="right-panel empty">
      <div className="right-panel-empty-state">
        <div className="right-panel-empty-icon">📂</div>
        <div className="right-panel-empty-title">展示面板</div>
        <div className="right-panel-empty-desc">
          你可以在这里打开画布、文档、网页或代码运行结果。
        </div>
      </div>
    </div>
  );
}

/**
 * 右侧展示面板外壳：页签条 + 工具条 + 内容区。
 *
 * 具体的内容视图全部在 `rightpanel/` 下按类型拆分，这里只负责路由与布局。
 */
export default function RightPanel({
  tabs,
  activeTabId,
  onActivateTab,
  onCloseTab,
  onOpenExternal,
  collapsed,
  onToggleCollapse,
}: RightPanelProps) {
  if (collapsed) return <CollapsedRail onToggleCollapse={onToggleCollapse} />;
  if (tabs.length === 0) return <EmptyState />;

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  return (
    <div className="right-panel">
      <div className="right-panel-tabs">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={`right-panel-tab ${tab.id === activeTabId ? "active" : ""}`}
            onClick={() => onActivateTab(tab.id)}
            title={tab.title}
          >
            <span className="right-panel-tab-icon">{TAB_ICONS[tab.type]}</span>
            <span className="right-panel-tab-title">{tab.title}</span>
            {tab.closable !== false && (
              <button
                className="right-panel-tab-close"
                onClick={(e) => {
                  e.stopPropagation();
                  onCloseTab(tab.id);
                }}
                title="关闭"
              >
                ×
              </button>
            )}
          </div>
        ))}
      </div>

      <div className="right-panel-toolbar">
        <span className="right-panel-active-title">{activeTab?.title}</span>
        <div className="right-panel-actions">
          {onOpenExternal && activeTab && (
            <button
              className="right-panel-action"
              onClick={() => onOpenExternal(activeTab)}
              title="打开"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                <polyline points="15 3 21 3 21 9" />
                <line x1="10" y1="14" x2="21" y2="3" />
              </svg>
            </button>
          )}
          <button className="right-panel-action" title="在新窗口打开" disabled>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="15 3 21 3 21 9" />
              <polyline points="9 21 3 21 3 15" />
              <line x1="21" y1="3" x2="14" y2="10" />
              <line x1="3" y1="21" x2="10" y2="14" />
            </svg>
          </button>
          <button
            className="right-panel-action collapse-action"
            onClick={onToggleCollapse}
            title="折叠面板"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <line x1="9" y1="3" x2="9" y2="21" />
            </svg>
          </button>
        </div>
      </div>

      <div className="right-panel-content">
        {activeTab && <TabContent tab={activeTab} onOpenExternal={onOpenExternal} />}
      </div>
    </div>
  );
}
