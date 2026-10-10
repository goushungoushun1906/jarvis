"""A6 guards + streaming regression check against a live backend.

Why this exists as a script rather than a pytest case: it boots the real
uvicorn app and drives a real streaming request, which is the only way to
prove the new ASGI Host guard does not buffer SSE (a ``BaseHTTPMiddleware``
implementation would pass unit tests and still silently break streaming).

Usage:
    python scripts/dev/verify_a6_guards.py            # dev backend (venv)
    python scripts/dev/verify_a6_guards.py --exe      # packaged backend.exe
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import httpx

BASE = "http://127.0.0.1:18200"
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
EXE_DIR = os.path.join(SERVER_DIR, "backend-dist")


def _wait_ready(timeout: int = 90) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3, trust_env=False) as c:
                if c.get(f"{BASE}/health").status_code == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def start_backend(use_exe: bool):
    if use_exe:
        cmd = [os.path.join(EXE_DIR, "backend.exe")]
        cwd = EXE_DIR
    else:
        py = os.path.join(SERVER_DIR, ".venv", "Scripts", "python.exe")
        cmd = [py, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
               "--port", "18200", "--app-dir", SERVER_DIR]
        cwd = SERVER_DIR
    print(f"启动: {' '.join(cmd[:2])} ...", flush=True)
    return subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def check_guards() -> list[tuple[str, bool, str]]:
    """Host + CORS assertions against the live server."""
    results: list[tuple[str, bool, str]] = []

    with httpx.Client(timeout=15, trust_env=False) as c:
        # 1. loopback Host with the packaged app's file:// origin
        r = c.get(f"{BASE}/health", headers={"Origin": "null"})
        ok = r.status_code == 200 and r.headers.get("access-control-allow-origin") == "null"
        results.append(("file:// 源 (Origin: null) 可访问且拿到 CORS 头", ok,
                        f"{r.status_code} acao={r.headers.get('access-control-allow-origin')!r}"))

        # 2. dev server origin
        r = c.get(f"{BASE}/health", headers={"Origin": "http://localhost:5173"})
        ok = r.headers.get("access-control-allow-origin") == "http://localhost:5173"
        results.append(("dev 源 (localhost:5173) 拿到 CORS 头", ok,
                        f"acao={r.headers.get('access-control-allow-origin')!r}"))

        # 3. foreign origin gets no CORS header -> browser blocks the read
        r = c.get(f"{BASE}/health", headers={"Origin": "https://evil.example"})
        ok = "access-control-allow-origin" not in r.headers
        results.append(("外部源拿不到 CORS 头（浏览器会拦截响应）", ok,
                        f"acao={r.headers.get('access-control-allow-origin')!r}"))

        # 4. non-loopback Host -> 403 (DNS rebinding)
        r = c.get(f"{BASE}/health", headers={"Host": "evil.example"})
        ok = r.status_code == 403
        results.append(("非 loopback Host 被拒 (DNS rebinding)", ok, f"status={r.status_code}"))

    return results


def check_streaming() -> tuple[list[tuple[str, bool, str]], float, float]:
    """Drive a real tool-calling question and assert chunks arrive early."""
    results: list[tuple[str, bool, str]] = []
    payload = {"messages": [{"role": "user", "content": "现在北京时间几点了？"}],
               "stream": True}
    headers = {"Origin": "null", "Content-Type": "application/json"}

    events: list[str] = []
    first_at: float | None = None
    with httpx.Client(timeout=180, trust_env=False) as c:
        start = time.perf_counter()
        with c.stream("POST", f"{BASE}/api/chat/stream", json=payload,
                      headers=headers) as resp:
            results.append(("SSE 请求被接受", resp.status_code == 200,
                            f"status={resp.status_code}"))
            buf = ""
            for chunk in resp.iter_text():
                if first_at is None and chunk:
                    first_at = (time.perf_counter() - start) * 1000
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    for line in frame.split("\n"):
                        if line.startswith("event: "):
                            events.append(line[7:])
            total = (time.perf_counter() - start) * 1000

    # Incremental delivery: the first bytes must arrive well before the stream
    # ends, otherwise something in the stack is buffering the whole response.
    first = first_at or 0.0
    incremental = first > 0 and first < total * 0.9
    results.append(("流式未被缓冲（首字节远早于结束）", incremental,
                    f"首字节 +{first:.0f}ms / 总耗时 {total:.0f}ms"))
    results.append(("出现 tool_call 事件", "tool_call" in events,
                    " -> ".join(dict.fromkeys(events)) or "(无事件)"))
    results.append(("出现 tool_result 事件", "tool_result" in events, ""))
    return results, first, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", action="store_true", help="用 packaged backend.exe 而非 venv")
    args = ap.parse_args()

    proc = start_backend(args.exe)
    try:
        if not _wait_ready(120):
            print("后端未就绪，中止", flush=True)
            return 1

        rows = check_guards()
        stream_rows, first, total = check_streaming()
        rows += stream_rows

        print("\n--- A6 校验结果 ---", flush=True)
        failed = 0
        for name, ok, detail in rows:
            mark = "PASS" if ok else "FAIL"
            if not ok:
                failed += 1
            print(f"[{mark}] {name}" + (f"  ({detail})" if detail else ""), flush=True)

        print(f"\n首字节 {first:.0f}ms / 总耗时 {total:.0f}ms", flush=True)
        print("全部通过" if failed == 0 else f"{failed} 项未通过", flush=True)
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
