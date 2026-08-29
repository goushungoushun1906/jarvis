import sqlite3, json, os

db_paths = [
    r'C:\Users\HONOR\Documents\trae work09\jarvis\server\jarvis.db',
    r'C:\Users\HONOR\Documents\trae work09\jarvis\release\win-unpacked\resources\backend\jarvis.db',
]

results = []
for db_path in db_paths:
    results.append(f'\n=== {db_path} ===')
    results.append(f'  exists: {os.path.exists(db_path)}')
    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        results.append(f'  size: {size} bytes')
        try:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("SELECT key, value FROM settings")
            for row in c.fetchall():
                key = row[0]
                val = row[1]
                if key in ('model_profiles', 'active_model_profile'):
                    results.append(f'  {key}: {val[:200]}')
                else:
                    results.append(f'  {key}: {str(val)[:100]}')
            conn.close()
        except Exception as e:
            results.append(f'  error: {e}')

with open(r'C:\Users\HONOR\Documents\trae work09\jarvis\server\db_check2.txt', 'w') as f:
    f.write('\n'.join(results))
