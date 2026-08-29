import type { Conversation } from "../lib/types";
import "./Sidebar.css";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  collapsed: boolean;
  onSelectConversation: (id: string) => void;
  onNewChat: () => void;
  onDeleteConversation: (id: string) => void;
  onToggleCollapse: () => void;
  onToggleSettings?: () => void;
}

function formatTime(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "刚刚";
    if (diffMin < 60) return `${diffMin}分钟前`;
    const diffH = Math.floor(diffMin / 60);
    if (diffH < 24) return `${diffH}小时前`;
    const diffD = Math.floor(diffH / 24);
    if (diffD < 7) return `${diffD}天前`;
    return `${d.getMonth() + 1}/${d.getDate()}`;
  } catch {
    return "";
  }
}

function getConvoTitle(convo: Conversation): string {
  if (convo.title) return convo.title;
  if (convo.messages && convo.messages.length > 0) {
    const firstUser = convo.messages.find((m) => m.role === "user");
    if (firstUser) {
      return firstUser.content.slice(0, 30) + (firstUser.content.length > 30 ? "..." : "");
    }
  }
  return "新对话";
}

export default function Sidebar({
  conversations,
  activeId,
  collapsed,
  onSelectConversation,
  onNewChat,
  onDeleteConversation,
  onToggleCollapse,
  onToggleSettings,
}: SidebarProps) {
  return (
    <div className={`sidebar ${collapsed ? "collapsed" : ""}`} role="navigation" aria-label="对话列表">
      {/* Header */}
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <div className="logo-icon">J</div>
          <span className="logo-text">JARVIS</span>
        </div>
      </div>

      {/* New Chat Button */}
      <button className="new-chat-btn" onClick={onNewChat} title="新建对话" aria-label="新建对话">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        <span className="new-chat-label">新建对话</span>
      </button>

      {/* Conversation list */}
      <div className="convo-list">
        {conversations.map((convo) => (
          <div
            key={convo.id}
            className={`convo-item ${convo.id === activeId ? "active" : ""}`}
            onClick={() => onSelectConversation(convo.id)}
            title={getConvoTitle(convo)}
          >
            <div className="convo-icon">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </div>
            <div className="convo-info">
              <span className="convo-title">{getConvoTitle(convo)}</span>
              <span className="convo-time">{formatTime(convo.updated_at)}</span>
            </div>
            {convo.parent_id && (
              <span className="fork-badge" title="从其他对话分叉">&#x2901;</span>
            )}
            <button
              className="convo-delete-btn"
              onClick={(e) => {
                e.stopPropagation();
                e.preventDefault();
                onDeleteConversation(convo.id);
              }}
              title="删除对话"
              aria-label="删除对话"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        ))}

        {conversations.length === 0 && !collapsed && (
          <div
            style={{
              padding: "20px 12px",
              textAlign: "center",
              fontSize: "12px",
              color: "var(--text-muted)",
            }}
          >
            暂无对话记录
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="sidebar-footer">
        {onToggleSettings && (
          <button className="sidebar-settings-btn" onClick={onToggleSettings} title="设置" aria-label="设置">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            <span className="sidebar-settings-label">设置</span>
          </button>
        )}
        <button className="collapse-btn" onClick={onToggleCollapse} title={collapsed ? "展开侧栏" : "收起侧栏"} aria-label={collapsed ? "展开侧栏" : "收起侧栏"}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={{
              transform: collapsed ? "rotate(180deg)" : "none",
              transition: "transform 0.2s ease",
            }}
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
          <span className="collapse-btn-label">{collapsed ? "" : "收起侧栏"}</span>
        </button>
      </div>
    </div>
  );
}
