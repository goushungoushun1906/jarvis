"""验证打包版真的要求 per-request token。

单元测试覆盖的是中间件逻辑；这里验证的是"打进 exe 之后仍然生效"，包括
token 文件确实被创建在 Electron 会去读的那个路径上——两边路径写歪是最容易
发生、且在开发环境里完全看不出来的失败方式。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_EXE = os.path.join(SERVER_DIR, "backend-dist", "backend.exe")
DEFAULT_PORT = 18295


def token_file_path() -> str:
    appdata = os.environ.get("APPDATA")
    base = os.path.join(appdata, "JARVIS") if appdata else os.path.join(
        os.path.expanduser("~"), ".jarvis"
    )
    return os.path.join(base, "api_token.txt")


def wait_ready(base: str, timeout: int = 240) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=3, trust_env=False) as c:
                if c.get(base + "/health").status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    exe = args.exe
    if not os.path.exists(exe):
        print(f"[FAIL] exe 不存在: {exe}")
        return 1

    tpath = token_file_path()
    print(f"token 文件: {tpath}")

    env = {**os.environ, "SERVER_PORT": str(args.port)}
    proc = subprocess.Popen(
        [exe],
        cwd=os.path.dirname(exe) or ".",
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{args.port}"
    try:
        if not wait_ready(base):
            print("[FAIL] 后端未就绪")
            return 1
        print("[PASS] 后端就绪")

        results: list[tuple[str, bool, str]] = []

        with httpx.Client(timeout=30, trust_env=False) as c:
            # /health 必须无需 token，否则 Electron 判断"要不要起后端"会死锁
            r = c.get(base + "/health")
            results.append(
                ("GET /health 无需 token", r.status_code == 200, str(r.status_code))
            )

            r = c.get(base + "/api/config")
            results.append(
                ("GET /api/config 无 token 被拒", r.status_code == 401, str(r.status_code))
            )

        if not os.path.exists(tpath):
            print(f"[FAIL] token 文件未创建: {tpath}")
            return 1
        token = open(tpath, encoding="utf-8").read().strip()
        print(f"[PASS] token 文件已创建（长度 {len(token)}）")

        with httpx.Client(timeout=30, trust_env=False) as c:
            r = c.get(base + "/api/config", headers={"X-Jarvis-Token": token})
            results.append(
                ("带正确 token 可访问", r.status_code == 200, str(r.status_code))
            )
            r = c.get(base + "/api/config", headers={"X-Jarvis-Token": "wrong-token"})
            results.append(
                ("错误 token 仍被拒", r.status_code == 401, str(r.status_code))
            )

        failed = 0
        for name, ok, detail in results:
            print(f"[{'PASS' if ok else 'FAIL'}] {name} ({detail})")
            if not ok:
                failed += 1
        return 1 if failed else 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
