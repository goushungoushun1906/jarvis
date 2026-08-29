import os
import sqlite3
import json
from pathlib import Path


def _load_api_key() -> str:
    """从 server/.env 读取 API Key，避免在源码中硬编码明文密钥。"""
    env_path = Path(__file__).resolve().parent.parent / "server" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("LLM_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("LLM_API_KEY", "")


db_path = r'C:\Users\HONOR\Documents\trae work09\jarvis\server\jarvis.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

API_KEY = _load_api_key()
BASE_URL = "https://apihub.agnes-ai.com/v1"
MODEL = "agnes-2.0-flash"

c.execute("SELECT value FROM settings WHERE key='model_profiles'")
row = c.fetchone()
profiles = json.loads(row[0]) if row else []
print(f"Current custom profiles: {[p['id'] for p in profiles]}")

# Start fresh: remove all existing custom profiles (they may contain stale keys/URLs)
profiles = []

# Override all built-in profiles to route to Agnes with the user's key.
# Custom profiles with the same id as built-ins override them in get_model_profiles().
override_profiles = [
    {"id": "agnes-flash", "name": "Agnes Flash", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "fast", "is_active": False},
    {"id": "agnes-15", "name": "Agnes 1.5", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "mid", "is_active": True},
    {"id": "gpt4o-mini", "name": "GPT-4o Mini -> Agnes", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "fast", "is_active": False},
    {"id": "gpt4o", "name": "GPT-4o -> Agnes", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "mid", "is_active": False},
    {"id": "deepseek", "name": "DeepSeek -> Agnes", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "deep", "is_active": False},
    {"id": "doubao", "name": "Doubao -> Agnes", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "mid", "is_active": False},
    {"id": "deepseek-r1-ollama", "name": "DeepSeek R1 Local -> Agnes", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "deep", "is_active": False},
    {"id": "claude", "name": "Claude -> Agnes", "model": MODEL, "base_url": BASE_URL, "api_key": API_KEY, "provider": "Agnes AI", "tier": "deep", "is_active": False},
]

profiles.extend(override_profiles)

c.execute("UPDATE settings SET value=? WHERE key='model_profiles'", [json.dumps(profiles, ensure_ascii=False)])

# Set active model profile
c.execute("UPDATE settings SET value=? WHERE key='active_model_profile'", ['agnes-15'])

conn.commit()

# Verify
c.execute("SELECT value FROM settings WHERE key='model_profiles'")
print(f"Updated custom profiles: {[p['id'] for p in json.loads(c.fetchone()[0])]}")
c.execute("SELECT value FROM settings WHERE key='active_model_profile'")
print("Active profile:", c.fetchone()[0])

conn.close()
print("Done - all profiles now route to Agnes")
