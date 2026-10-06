"""Phase 17.3 端到端验证：连接真实后端，打印带时间戳的 SSE 帧。

前置：后端已启动（默认 http://127.0.0.1:18211，可用 --port 覆盖）

用法：
    cd server && ./.venv/Scripts/python.exe scripts/dev/verify_sse_endpoint.py 18211
"""

from __future__ import annotations

import json
import sys
import time

import httpx

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18211
BASE = f"http://127.0.0.1:{PORT}"

PAYLOAD = {
    "messages": [
        {"role": "user", "content": "先说一句你打算怎么做，然后用计算器算 (1234 * 5678) + 90，告诉我结果。"}
    ],
    "stream": True,
}


def main() -> None:
    start = time.perf_counter()
    buffer = ""

    # trust_env=False: never route a loopback request through the system proxy
    with httpx.Client(timeout=120, trust_env=False) as client:
        with client.stream("POST", f"{BASE}/api/chat/stream", json=PAYLOAD) as resp:
            print(f"HTTP {resp.status_code}  content-type={resp.headers.get('content-type')}")
            print(f"x-accel-buffering={resp.headers.get('x-accel-buffering')}")
            print("-" * 72)

            for chunk in resp.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    elapsed = (time.perf_counter() - start) * 1000
                    event_name = None
                    data = None
                    for line in frame.split("\n"):
                        if line.startswith("event: "):
                            event_name = line[7:]
                        elif line.startswith("data: "):
                            data = line[6:]
                    if data is None:
                        continue
                    if data == "[DONE]":
                        print(f"  [done  ] +{elapsed:7.0f} ms  [DONE]")
                        continue
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        print(f"  [raw   ] +{elapsed:7.0f} ms  {data!r}")
                        continue
                    if "content" in payload:
                        print(f"  [text  ] +{elapsed:7.0f} ms  {payload['content']!r}")
                    elif "tool_call" in payload:
                        tc = payload["tool_call"]
                        print(
                            f"  [call  ] +{elapsed:7.0f} ms  {tc['tool']} args={tc['args']} "
                            f"status={tc['status']} requires_approval={tc['requires_approval']}"
                        )
                    elif "tool_result" in payload:
                        tr = payload["tool_result"]
                        print(
                            f"  [result] +{elapsed:7.0f} ms  {tr['tool']} ok={tr['success']} "
                            f"{tr.get('duration_ms')}ms  {tr['result'][:80]!r}"
                        )
                    elif "error" in payload:
                        print(f"  [ERROR ] +{elapsed:7.0f} ms  {payload['error']}")
                    else:
                        print(f"  [frame ] +{elapsed:7.0f} ms  event={event_name} {payload}")

    total = (time.perf_counter() - start) * 1000
    print("-" * 72)
    print(f"总计 {total:.0f} ms")


if __name__ == "__main__":
    main()
