import { useState, useEffect, useCallback } from "react";
import type { Skill, ConnectionStatus } from "../lib/types";
import { listSkills, invokeSkill } from "../lib/api";
import "./SkillCenter.css";

interface SkillCenterProps {
  connectionStatus: ConnectionStatus;
  onSendMessage: (text: string) => void;
  onClose: () => void;
}

export default function SkillCenter({
  connectionStatus,
  onSendMessage,
  onClose,
}: SkillCenterProps) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [invoking, setInvoking] = useState(false);

  const fetchSkills = useCallback(async () => {
    if (connectionStatus !== "online") {
      setLoading(false);
      return;
    }
    try {
      const data = await listSkills();
      setSkills(data.skills || []);
      setError(null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [connectionStatus]);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const handleSkillClick = (skill: Skill) => {
    setSelectedSkill(skill);
    setInputValue(skill.default_input || "");
  };

  const handleInvoke = async () => {
    if (!selectedSkill) return;
    setInvoking(true);
    try {
      const result = await invokeSkill(selectedSkill.id, inputValue);
      if (result.success && result.prompt) {
        onSendMessage(result.prompt);
        onClose();
      } else {
        setError(result.error || "技能调用失败");
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setInvoking(false);
    }
  };

  const categories = Array.from(new Set(skills.map((s) => s.category)));

  return (
    <div className="skill-center-overlay" onClick={onClose}>
      <div className="skill-center-panel" onClick={(e) => e.stopPropagation()}>
        <div className="skill-center-header">
          <h2>技能中心</h2>
          <button className="skill-center-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>

        {loading && <div className="skill-center-loading">加载技能中...</div>}
        {error && <div className="skill-center-error">{error}</div>}

        {!loading && !selectedSkill && (
          <div className="skill-center-content">
            {categories.map((category) => (
              <div key={category} className="skill-category">
                <div className="skill-category-title">{category}</div>
                <div className="skill-grid">
                  {skills
                    .filter((s) => s.category === category)
                    .map((skill) => (
                      <button
                        key={skill.id}
                        className="skill-card"
                        onClick={() => handleSkillClick(skill)}
                      >
                        <span className="skill-card-icon">{skill.icon}</span>
                        <span className="skill-card-name">{skill.name}</span>
                        <span className="skill-card-desc">{skill.description}</span>
                      </button>
                    ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {selectedSkill && (
          <div className="skill-invoke-panel">
            <button
              className="skill-back-button"
              onClick={() => setSelectedSkill(null)}
            >
              ← 返回技能列表
            </button>
            <div className="skill-invoke-header">
              <span className="skill-invoke-icon">{selectedSkill.icon}</span>
              <div>
                <div className="skill-invoke-name">{selectedSkill.name}</div>
                <div className="skill-invoke-desc">{selectedSkill.description}</div>
              </div>
            </div>

            {selectedSkill.requires_input ? (
              <div className="skill-input-group">
                <label>{selectedSkill.input_label}</label>
                <textarea
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  placeholder={selectedSkill.input_placeholder}
                  rows={5}
                />
              </div>
            ) : (
              <div className="skill-input-group">
                <p className="skill-no-input">该技能不需要额外输入，点击即可执行。</p>
              </div>
            )}

            <button
              className="skill-invoke-button"
              onClick={handleInvoke}
              disabled={invoking}
            >
              {invoking ? "执行中..." : "执行技能"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
