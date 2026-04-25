import sqlite3

conn = sqlite3.connect("database.db")
cursor = conn.cursor()

cursor.execute("ALTER TABLE events ADD COLUMN remind INTEGER DEFAULT 60")
conn.commit()
conn.close()