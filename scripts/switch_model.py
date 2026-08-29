import sqlite3

db_path = r'C:\Users\HONOR\Documents\trae work09\jarvis\server\jarvis.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Set active model back to agnes-flash
c.execute("UPDATE settings SET value='agnes-flash' WHERE key='active_model_profile'")
conn.commit()

# Verify
c.execute("SELECT value FROM settings WHERE key='active_model_profile'")
print("Active model:", c.fetchone()[0])

conn.close()
