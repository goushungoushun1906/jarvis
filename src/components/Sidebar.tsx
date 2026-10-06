import { useState, useRef, useMemo } from "react";
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
  onOpenSkillCenter?: () => void;
  onOpenScheduler?: () => void;
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

function getGroupKey(dateStr: string): string {
  try {
    const d = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffD = Math.floor(diffMs / 86400000);
    if (diffD === 0) return "today";
    if (diffD === 1) return "yesterday";
    return "older";
  } catch {
    return "older";
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

function groupConversations(convos: Conversation[]): Record<string, Conversation[]> {
  const groups: Record<string, Conversation[]> = { today: [], yesterday: [], older: [] };
  for (const c of convos) {
    const key = getGroupKey(c.updated_at);
    groups[key].push(c);
  }
  return groups;
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
  onOpenSkillCenter,
  onOpenScheduler,
}: SidebarProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchFocused, setSearchFocused] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const filteredConversations = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter((c) => getConvoTitle(c).toLowerCase().includes(q));
  }, [conversations, searchQuery]);

  const grouped = groupConversations(filteredConversations);

  const handleSearchIconClick = () => {
    searchInputRef.current?.focus();
  };

  const quickActions = [
    {
      label: "新对话",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      ),
      onClick: onNewChat,
    },
    {
      label: "定时任务",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      ),
      onClick: onOpenScheduler,
    },
    {
      label: "技能中心",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polygon points="12 2 2 7 12 12 22 7 12 2" />
          <polyline points="2 17 12 22 22 17" />
          <polyline points="2 12 12 17 22 12" />
        </svg>
      ),
      onClick: onOpenSkillCenter,
    },
    {
      label: "工作空间",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
      ),
      onClick: () => {
        // Placeholder until FileBrowser is built; open skill center as fallback
        onOpenSkillCenter?.();
      },
    },
  ];

  if (collapsed) {
    return (
      <div className="sidebar collapsed" role="navigation" aria-label="对话列表">
        <div className="sidebar-header">
          <div className="sidebar-logo">
            <div className="logo-icon">J</div>
          </div>
        </div>
        <button className="new-chat-btn" onClick={onNewChat} title="新建对话">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
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
              {convo.parent_id && <span className="fork-badge">&#x2901;</span>}
              <button
                className="convo-delete-btn"
                onClick={(e) => { e.stopPropagation(); onDeleteConversation(convo.id); }}
                title="删除对话"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
          ))}
        </div>
        <div className="sidebar-footer">
          {onToggleSettings && (
            <button className="sidebar-settings-btn" onClick={onToggleSettings} title="设置">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06-.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          )}
          <button className="collapse-btn" onClick={onToggleCollapse} title="展开侧栏">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="sidebar" role="navigation" aria-label="对话列表">
      {/* Header */}
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <div className="logo-icon">J</div>
          <span className="logo-text">JARVIS</span>
        </div>
        <div className="sidebar-header-actions">
          <button className="header-action-btn" title="搜索" aria-label="搜索" onClick={handleSearchIconClick}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
          </button>
          <button className="header-action-btn" title="新建对话" aria-label="新建对话" onClick={onNewChat}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Search */}
      <div className={`sidebar-search-wrapper ${searchFocused || searchQuery ? "focused" : ""}`}>
        <span className="sidebar-search-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
        </span>
        <input
          ref={searchInputRef}
          className="sidebar-search-input"
          type="text"
          placeholder="搜索对话..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onFocus={() => setSearchFocused(true)}
          onBlur={() => setSearchFocused(false)}
        />
        {searchQuery && (
          <button className="sidebar-search-clear" onClick={() => setSearchQuery("")} title="清空">
            ×
          </button>
        )}
      </div>

      {/* Quick actions grid */}
      <div className="sidebar-quick-actions">
        {quickActions.map((item) => (
          <button
            key={item.label}
            className="quick-action-item"
            onClick={() => item.onClick?.()}
            title={item.label}
          >
            <span className="quick-action-icon">{item.icon}</span>
            <span className="quick-action-label">{item.label}</span>
          </button>
        ))}
      </div>

      {/* Conversation list */}
      <div className="convo-list">
        {filteredConversations.length === 0 ? (
          <div className="sidebar-empty">
            {searchQuery ? "没有匹配的对话" : "暂无对话记录"}
          </div>
        ) : (
          ["today", "yesterday", "older"].map((groupKey) => {
            const items = grouped[groupKey as keyof typeof grouped];
            if (items.length === 0) return null;
            const labels: Record<string, string> = {
              today: "今天",
              yesterday: "昨天",
              older: "更早",
            };
            return (
              <div key={groupKey}>
                <div className="convo-group-label">{labels[groupKey]}</div>
                {items.map((convo) => (
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
                        onDeleteConversation(convo.id);
                      }}
                      title="删除对话"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <line x1="18" y1="6" x2="6" y2="18" />
                        <line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            );
          })
        )}
      </div>

      {/* Footer */}
      <div className="sidebar-footer">
        {onToggleSettings && (
          <button className="sidebar-settings-btn" onClick={onToggleSettings} title="设置">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06-.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            <span className="sidebar-settings-label">设置</span>
          </button>
        )}
        <button className="collapse-btn" onClick={onToggleCollapse} title="收起侧栏">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          <span className="collapse-btn-label">收起侧栏</span>
        </button>
      </div>
    </div>
  );
}
