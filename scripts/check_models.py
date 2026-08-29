import sqlite3
import json

db_path = r'C:\Users\HONOR\Documents\trae work09\jarvis\server\jarvis.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()
c.execute("SELECT value FROM settings WHERE key='model_profiles'")
profiles = json.loads(c.fetchone()[0])
print(json.dumps(profiles, indent=2, ensure_ascii=False))
c.execute("SELECT value FROM settings WHERE key='active_model_profile'")
print("Active:", c.fetchone()[0])
conn.close()
