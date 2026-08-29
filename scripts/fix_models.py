import sqlite3, json

db_path = r'C:\Users\HONOR\Documents\trae work09\jarvis\server\jarvis.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Get current profiles
c.execute("SELECT value FROM settings WHERE key='model_profiles'")
row = c.fetchone()
profiles = json.loads(row[0]) if row else []
print(f"Current profiles: {[p['id'] for p in profiles]}")

# Check if agnes-flash already exists
if not any(p['id'] == 'agnes-2.0-flash' for p in profiles):
    # Add agnes-2.0-flash profile
    profiles.append({
        "id": "agnes-2.0-flash",
        "name": "Agnes 2.0 Flash",
        "model": "agnes-2.0-flash",
        "base_url": "https://api.agnes.ai",
        "api_key": "",
        "provider": "自定义",
        "is_active": True
    })

if not any(p['id'] == 'agnes-flash' for p in profiles):
    # Also add agnes-flash as alias
    profiles.append({
        "id": "agnes-flash",
        "name": "Agnes Flash",
        "model": "agnes-flash",
        "base_url": "https://api.agnes.ai",
        "api_key": "",
        "provider": "自定义",
        "is_active": True
    })

# Update profiles
c.execute("UPDATE settings SET value=? WHERE key='model_profiles'", [json.dumps(profiles, ensure_ascii=False)])
conn.commit()

print(f"Updated profiles: {[p['id'] for p in profiles]}")

conn.close()
