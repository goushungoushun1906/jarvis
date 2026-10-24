import { useCallback, useEffect, useRef, useState } from "react";
import {
  getWakeInfo,
  reloadWakeword,
  startWakeword,
  stopWakeword,
  listWakeDevices,
  setWakeSensitivity,
  listWakeCaptures,
  markWakeCapture,
  wakeCaptureUrl,
  testWakeWav,
  type WakeInfo,
  type WakeDevice,
  type WakeCapture,
} from "../../lib/api";
import type { Notify } from "./types";

/**
 * 语音唤醒诊断与调参。
 *
 * 为什么单独做一个 Tab：内置麦克风的质量差异极大，同一套参数在不同机器、
 * 不同环境噪声下表现完全不同。想让它好用，用户必须能自己来回微调 ——
 * 而"调"这件事的前提是能看见。
 *
 * 面板刻意暴露了打包自检（self_check）：过去缺少模型文件的表现是"点了没
 * 反应但不报错"，在这里会变成一行红色。
 */

interface WakeWordTabProps {
  notify: Notify;
}

function YesNo({ ok }: { ok: boolean }) {
  return (
    <span className="value" style={{ color: ok ? "var(--success)" : "var(--error)" }}>
      {ok ? "正常" : "缺失"}
    </span>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="about-row">
      <span className="label">{label}</span>
      {children}
    </div>
  );
}

function formatTs(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

export default function WakeWordTab({ notify }: WakeWordTabProps) {
  const [info, setInfo] = useState<WakeInfo | null>(null);
  const [devices, setDevices] = useState<WakeDevice[]>([]);
  const [captures, setCaptures] = useState<WakeCapture[]>([]);
  const [sens, setSens] = useState(0.6);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [wakeInfo, deviceList, captureList] = await Promise.all([
        getWakeInfo(),
        listWakeDevices(),
        listWakeCaptures(),
      ]);
      setInfo(wakeInfo);
      setDevices(deviceList.devices);
      setCaptures(captureList.captures);
      setSens(wakeInfo.sensitivities?.[0] ?? 0.6);
    } catch (e) {
      notify(`读取唤醒状态失败：${e instanceof Error ? e.message : "未知错误"}`);
    }
  }, [notify]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const running = Boolean(info?.running);

  const handleToggle = async () => {
    setBusy(true);
    try {
      if (running) {
        await stopWakeword();
        notify("已停止语音唤醒");
      } else {
        await startWakeword(info?.device ?? null);
        notify("已启动语音唤醒");
      }
      await refresh();
    } catch (e) {
      notify(`操作失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  };

  const handleReload = async () => {
    setBusy(true);
    try {
      await reloadWakeword();
      notify("已重新加载唤醒配置");
      await refresh();
    } catch (e) {
      notify(`重载失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  };

  /** 拖动过程中只更新显示，松手才提交 —— 否则一次滑动会打出几十个请求。 */
  const commitSens = async () => {
    try {
      await setWakeSensitivity([sens]);
      await refresh();
      notify(`灵敏度已调整为 ${sens.toFixed(2)}`);
    } catch (e) {
      notify(`调整失败：${e instanceof Error ? e.message : "未知错误"}`);
    }
  };

  const handleDeviceChange = async (value: string) => {
    const index = value === "" ? null : Number(value);
    setBusy(true);
    try {
      await startWakeword(index);
      notify(index === null ? "已切换到系统默认麦克风" : "已切换麦克风");
      await refresh();
    } catch (e) {
      notify(`切换失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setBusy(false);
    }
  };

  const handleMark = async (id: string, label: "false_alarm" | "true_wake") => {
    try {
      await markWakeCapture(id, label);
      await refresh();
      notify(label === "false_alarm" ? "已标记为误唤醒" : "已标记为有效唤醒");
    } catch (e) {
      notify(`标记失败：${e instanceof Error ? e.message : "未知错误"}`);
    }
  };

  /**
   * 录音要经 fetch 取回再本地播放。
   * 直接用 <audio src> 的话请求不带 API token（鉴权由 Electron 网络层注入
   * 给 XHR/fetch），会被后端拒掉。
   */
  const handlePlay = async (id: string) => {
    try {
      const resp = await fetch(wakeCaptureUrl(id));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch (e) {
      notify(`播放失败：${e instanceof Error ? e.message : "未知错误"}`);
    }
  };

  const handleTestFile = async (file: File) => {
    setTesting(true);
    try {
      const r = await testWakeWav(file);
      notify(r.hit ? `回放命中：${r.keyword}` : "回放未命中（可检查模型是否配套）");
    } catch (e) {
      notify(`回放失败：${e instanceof Error ? e.message : "未知错误"}`);
    } finally {
      setTesting(false);
    }
  };

  const check = info?.self_check;

  return (
    <>
      <div className="settings-section">
        <div className="settings-section-title">运行状态</div>
        <div className="about-info">
          <Row label="引擎">
            <span className="value">{info?.engine ?? "未加载"}</span>
          </Row>
          <Row label="监听中">
            <span
              className="value"
              style={{ color: running ? "var(--success)" : "var(--text-muted)" }}
            >
              {running ? "是" : "否"}
            </span>
          </Row>
          <Row label="唤醒词">
            <span className="value">
              {info?.keywords?.length ? info.keywords.join("、") : "未配置"}
            </span>
          </Row>
          {/* 只有 Porcupine 需要 Picovoice 密钥。sherpa 是本地开放词表模型，
              不校验密钥 —— 无条件显示会把正常状态误报成"缺失"。 */}
          {info?.engine === "porcupine" && (
            <Row label="密钥已配置">
              <YesNo ok={Boolean(info?.access_key_set)} />
            </Row>
          )}
          {info?.engine === "sherpa" && (
            <Row label="触发阈值">
              <span className="value">{check?.sherpa_threshold ?? "—"}</span>
            </Row>
          )}
          {/* Vosk 的语法必须以「单字序列」书写，写错不报错、只表现为 0 命中。
              所以把实际条目显示出来，语法没生成对时一眼就能看出来。 */}
          {info?.engine === "vosk" && (
            <Row label="唤醒语法">
              <span className="value">
                {check?.vosk_grammar?.length
                  ? `${check.vosk_grammar.join(" / ")} …`
                  : "未生成"}
              </span>
            </Row>
          )}
          <Row label="累计唤醒">
            <span className="value">{info?.detections_total ?? 0} 次</span>
          </Row>
          <Row label="已标记误唤醒">
            <span className="value">{info?.false_alarms_marked ?? 0} 次</span>
          </Row>
        </div>

        {info?.error && (
          <div
            style={{
              marginTop: 12,
              padding: "8px 12px",
              borderRadius: 8,
              fontSize: 12,
              background: "rgba(239,68,68,0.1)",
              color: "var(--error)",
            }}
          >
            {info.error}
            {info.hint ? <div style={{ opacity: 0.8 }}>{info.hint}</div> : null}
          </div>
        )}

        {/* 系统目录里的游离 DLL 是 sherpa / onnxruntime 加载失败的头号嫌疑。
            实测 sherpa 的 .pyd 会优先加载自己目录下的那份，通常不受影响；
            但一旦它真被加载进进程，这里要能立刻看见，别靠猜。 */}
        {check?.stray_system32_onnxruntime && (
          <div
            style={{
              marginTop: 12,
              padding: "8px 12px",
              borderRadius: 8,
              fontSize: 12,
              background: "rgba(245,158,11,0.1)",
              color: "var(--warning)",
            }}
          >
            检出系统目录游离 DLL：C:\Windows\System32\onnxruntime.dll
            <div style={{ opacity: 0.8 }}>
              它是 DLL 版本冲突的潜在源头。当前未引发问题（sherpa 用的是自带
              那份 {check.sherpa_ort_version || "未知版本"}），但若后续出现加载
              失败，可把那份 DLL 改名为 onnxruntime.dll.bak（需要管理员权限）。
            </div>
          </div>
        )}

        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button className="setting-btn" onClick={handleToggle} disabled={busy}>
            {busy ? "处理中..." : running ? "停止监听" : "启动监听"}
          </button>
          <button className="setting-btn" onClick={handleReload} disabled={busy}>
            {info?.engine === "porcupine" ? "重新加载密钥" : "重新加载配置"}
          </button>
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section-title">灵敏度</div>
        <div className="setting-field">
          <label className="setting-label">
            当前 {sens.toFixed(2)}　（数值越高越容易唤醒，也更容易误触发）
          </label>
          <input
            type="range"
            min={0.3}
            max={0.9}
            step={0.05}
            value={sens}
            onChange={(e) => setSens(Number(e.target.value))}
            onPointerUp={() => void commitSens()}
            style={{ width: "100%" }}
          />
        </div>
        <p style={{ fontSize: 12, opacity: 0.7, lineHeight: 1.6 }}>
          环境安静可以调高一点；如果经常被自己的声音或视频音频误触发，就往下调。
          每次调整的最佳依据是下面的"误唤醒录音"—— 打上标记后这里会累计，
          就能判断到底该往哪边调。
        </p>
      </div>

      <div className="settings-section">
        <div className="settings-section-title">麦克风</div>
        <div className="setting-field">
          <label className="setting-label">输入设备</label>
          <select
            className="setting-select"
            value={info?.device ?? ""}
            onChange={(e) => void handleDeviceChange(e.target.value)}
            disabled={busy}
          >
            <option value="">系统默认</option>
            {devices.map((d) => (
              <option key={d.index} value={d.index}>
                {d.name}
                {d.likely_builtin ? "（内置）" : ""}
              </option>
            ))}
          </select>
        </div>
        <p style={{ fontSize: 12, opacity: 0.7, lineHeight: 1.6 }}>
          若识别吃力，先在 Windows 声音设置里关闭麦克风的"音频增强 / 自动增益"，
          它会改变输入信号特征，是很多"识别不准"的真正原因。
        </p>
      </div>

      <div className="settings-section">
        <div className="settings-section-title">打包自检</div>
        <p style={{ fontSize: 12, opacity: 0.7, lineHeight: 1.6 }}>
          缺少模型文件的表现永远是"没反应却也不报错"。这里把每个文件是否存在
          直接摆出来，缺哪个一目了然。
        </p>
        <div className="about-info" style={{ marginTop: 8 }}>
          <Row label="中文声学参数">
            <YesNo ok={Boolean(check?.model_exists)} />
          </Row>
          <Row label="关键词模型">
            <YesNo ok={Boolean(check?.keyword_paths_exist?.every(Boolean))} />
          </Row>
          <Row label="原生库">
            <YesNo ok={Boolean(check?.library_exists)} />
          </Row>
          <Row label="引擎可导入">
            <YesNo ok={Boolean(check?.pvporcupine_importable)} />
          </Row>
          <Row label="录音库可导入">
            <YesNo ok={Boolean(check?.pyaudio_importable)} />
          </Row>
          <Row label="运行形态">
            <span className="value">{check?.frozen ? "打包版" : "开发版"}</span>
          </Row>
        </div>
        {check && !check.model_exists && (
          <div style={{ fontSize: 11, opacity: 0.6, marginTop: 8, wordBreak: "break-all" }}>
            期望路径：{check.model_path}
          </div>
        )}
      </div>

      <div className="settings-section">
        <div className="settings-section-title">离线回放测试</div>
        <p style={{ fontSize: 12, opacity: 0.7, lineHeight: 1.6 }}>
          选一段 16kHz 单声道 wav，不经麦克风直接走一遍判定，用来确认模型
          到底装好没有。
        </p>
        <input
          ref={fileRef}
          type="file"
          accept="audio/wav"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleTestFile(file);
            if (fileRef.current) fileRef.current.value = "";
          }}
        />
        <button
          className="setting-btn"
          onClick={() => fileRef.current?.click()}
          disabled={testing}
        >
          {testing ? "测试中..." : "选择音频文件测试"}
        </button>
      </div>

      <div className="settings-section">
        <div className="settings-section-title">误唤醒录音</div>
        {captures.length === 0 ? (
          <p style={{ fontSize: 12, opacity: 0.7 }}>暂无记录</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {captures.map((c) => (
              <div
                key={c.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  fontSize: 12,
                  padding: "6px 8px",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                }}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  {formatTs(c.ts)} · {c.keyword} · {c.score.toFixed(2)}
                  {c.label === "false_alarm" ? " · 已标误报" : ""}
                </span>
                <button className="setting-btn" onClick={() => void handlePlay(c.id)}>
                  播放
                </button>
                <button
                  className="setting-btn"
                  onClick={() => void handleMark(c.id, "false_alarm")}
                >
                  这不是我说的
                </button>
              </div>
            ))}
          </div>
        )}
        <p style={{ fontSize: 11, opacity: 0.6, lineHeight: 1.6, marginTop: 10 }}>
          录音保存在本地 <code>server/logs/wakeword/</code>，只保留最近 50 条。
          分数不是引擎给出的置信度 —— 关键词引擎只返回命中的词序，
          这里是按双灵敏度一致性与音量强度推导出来的参考值。
        </p>
      </div>
    </>
  );
}
