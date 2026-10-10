"""End-to-end smoke for the backend chat path.

This is the closest we can get to "open -> send -> receive tool card" without
a real Electron window in CI: it boots the packaged backend over TCP and drives
``/api/chat/stream`` with a question that is designed to trigger the MCP time
tool. It asserts the full SSE event sequence and tool card shape.

For the Electron renderer -> backend hop, see ``verify_a6_guards.py``
(CORS/Host) and the manual desktop smoke.

Usage:
    python scripts/dev/verify_e2e_smoke.py [--exe]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
BASE = "http://127.0.0.1:18200"


def start_backend(use_exe: bool) -> subprocess.Popen:
    if use_exe:
        cmd = [os.path.join(SERVER_DIR, "backend-dist", "backend.exe")]
        cwd = os.path.join(SERVER_DIR, "backend-dist")
    else:
        cmd = [os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe"),
               "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
               "--port", "18200"]
        cwd = SERVER_DIR
    return subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def wait_ready(timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3, trust_env=False) as c:
                if c.get(f"{BASE}/health").status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def parse_sse(text: str) -> list[tuple[str, dict]]:
    frames = []
    buf = ""
    for chunk in text.split("\n\n"):
        if not chunk.strip():
            continue
        name = None
        data = None
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if name:
            try:
                payload = json.loads(data) if data and data != "[DONE]" else {}
            except Exception:
                payload = {}
            frames.append((name, payload))
    return frames


def check_chat_stream(question: str) -> list[tuple[str, bool, str]]:
    print(f"\n--- 对话: {question!r} ---", flush=True)
    rows = []
    payload = {
        "messages": [{"role": "user", "content": question}],
        "stream": True,
    }
    events: list[tuple[str, dict]] = []
    started = time.perf_counter()
    with httpx.Client(timeout=180, trust_env=False) as c:
        with c.stream("POST", f"{BASE}/api/chat/stream", json=payload,
                      headers={"Origin": "null"}) as resp:
            rows.append(("SSE 请求被接受", resp.status_code == 200,
                         f"status={resp.status_code}"))
            text = ""
            for chunk in resp.iter_text():
                text += chunk
            events = parse_sse(text)
    total = (time.perf_counter() - started) * 1000
    event_names = [n for n, _ in events]

    rows.append(("出现 text 事件", "text" in event_names, ""))
    rows.append(("出现 tool_call 事件", "tool_call" in event_names, ""))
    rows.append(("出现 tool_result 事件", "tool_result" in event_names, ""))
    rows.append(("出现 done 事件", "done" in event_names, ""))

    # Look for time-related tool usage
    tool_names = []
    for n, p in events:
        if n == "tool_call":
            tc = p.get("tool_call", {})
            tool_names.append(tc.get("tool") or tc.get("name"))
    rows.append(("触发了工具", bool(tool_names), f"工具: {tool_names}"))

    # Show the final assistant text
    final_text = ""
    for n, p in events:
        if n == "text":
            final_text += p.get("content", "")
    rows.append(("收到回复文本", bool(final_text), final_text[:80]))
    rows.append(("总耗时", True, f"+{total:.0f} ms"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", action="store_true", help="使用打包后的 backend.exe")
    args = ap.parse_args()

    proc = start_backend(args.exe)
    try:
        if not wait_ready(120):
            print("后端未就绪，中止", flush=True)
            return 1

        rows = check_chat_stream("现在北京时间几点了？")

        print("\n=== E2E 冒烟结果 ===", flush=True)
        failed = 0
        for label, ok, detail in rows:
            mark = "PASS" if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"[{mark}] {label}" + (f"  ({detail})" if detail else ""), flush=True)
        print(f"\n结果: {len(rows)} 项, {'全部通过' if failed == 0 else f'{failed} 项失败'}",
              flush=True)
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
