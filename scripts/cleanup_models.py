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
print(f"Before cleanup: {[p['id'] for p in profiles]}")

# Keep only the agnes-15 override (default AGNES-2.0)
profiles = [p for p in profiles if p['id'] == 'agnes-15']

# Ensure agnes-15 override exists and is correct
agnes_15 = next((p for p in profiles if p['id'] == 'agnes-15'), None)
if agnes_15 is None:
    profiles.append({
        "id": "agnes-15",
        "name": "Agnes 2.0",
        "model": MODEL,
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "provider": "Agnes AI",
        "tier": "mid",
        "is_active": True,
    })
else:
    agnes_15.update({
        "name": "Agnes 2.0",
        "model": MODEL,
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "provider": "Agnes AI",
        "tier": "mid",
        "is_active": True,
    })

c.execute("UPDATE settings SET value=? WHERE key='model_profiles'", [json.dumps(profiles, ensure_ascii=False)])
c.execute("UPDATE settings SET value='agnes-15' WHERE key='active_model_profile'")
conn.commit()

c.execute("SELECT value FROM settings WHERE key='model_profiles'")
print(f"After cleanup: {[p['id'] for p in json.loads(c.fetchone()[0])]}")
c.execute("SELECT value FROM settings WHERE key='active_model_profile'")
print("Active profile:", c.fetchone()[0])

conn.close()
print("Done - only Agnes 2.0 remains")
