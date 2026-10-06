"""Phase 17.8 dev check — end-to-end: /api/chat/stream must deliver the browsed
page to the display panel as a `tool_result` carrying `metadata.url`.

Run against a live backend:
    ./.venv/Scripts/python.exe -u scripts/dev/verify_sse_webview.py 18211
"""

from __future__ import annotations

import json
import sys
import time

import httpx

PORT = sys.argv[1] if len(sys.argv) > 1 else "18200"
URL = "http://127.0.0.1:" + PORT
PROMPT = "打开这个网址 https://app.pavo-ai.work/ 看看它是什么网站"


def main() -> int:
    # trust_env=False: the sandbox proxy intercepts loopback requests.
    with httpx.Client(timeout=180, trust_env=False) as client:
        health = client.get(f"{URL}/health")
        print(f"health: {health.status_code}")

        start = time.perf_counter()
        seen: dict[str, int] = {}
        browses: list[dict] = []

        with client.stream(
            "POST",
            f"{URL}/api/chat/stream",
            json={"messages": [{"role": "user", "content": PROMPT}], "stream": True},
        ) as resp:
            print(f"stream status={resp.status_code} "
                  f"content-type={resp.headers.get('content-type')}")
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    print(f"  [+{(time.perf_counter() - start) * 1000:7.0f} ms] [DONE]")
                    continue
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                key = next(iter(data), "?")
                seen[key] = seen.get(key, 0) + 1
                ms = (time.perf_counter() - start) * 1000

                if key == "tool_call":
                    tc = data["tool_call"]
                    print(f"  [+{ms:7.0f} ms] tool_call   {tc['tool']} "
                          f"args={json.dumps(tc['args'], ensure_ascii=False)[:70]}")
                elif key == "tool_result":
                    tr = data["tool_result"]
                    meta = tr.get("metadata") or {}
                    print(f"  [+{ms:7.0f} ms] tool_result {tr['tool']} "
                          f"success={tr['success']} metadata={json.dumps(meta, ensure_ascii=False)[:150]}")
                    if tr["tool"] == "browser_open" and meta.get("url"):
                        browses.append(meta)
                elif key == "text":
                    print(f"  [+{ms:7.0f} ms] text        {data['text'][:40]!r}")
                elif key == "error":
                    print(f"  [+{ms:7.0f} ms] ERROR       {str(data['error'])[:120]}")

        print(f"\n事件统计: {seen}")

        if not browses:
            print("!! 未观察到携带 url 的 browser_open 结果 —— 面板不会被打开")
            return 1

        meta = browses[0]
        print("\n--- 面板将显示 ---")
        print(f"  url        : {meta.get('url')}")
        print(f"  title      : {meta.get('title')}")
        print(f"  embeddable : {meta.get('embeddable')}")
        print(f"  reason     : {meta.get('embed_block_reason') or '(无，可正常嵌入)'}")
        print("\n结果: 通过 —— 展示面板可据此打开网页")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
