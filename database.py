import sqlite3
from config import TOPICS

DB_PATH = 'bot_settings.db'


def init_db():
    """Создаёт таблицы при первом запуске."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Расписания публикаций
    c.execute('''
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            topic TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 2
        )
    ''')

    # История опубликованных ссылок (защита от дублей навсегда)
    c.execute('''
        CREATE TABLE IF NOT EXISTS posted (
            link TEXT PRIMARY KEY,
            published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


# ---------- РАСПИСАНИЯ ----------

def get_all_schedules():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, time, topic, count FROM schedules ORDER BY time')
    rows = c.fetchall()
    conn.close()
    return rows


def add_schedule(time_str, topic='gaming', count=2):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO schedules (time, topic, count) VALUES (?, ?, ?)',
              (time_str, topic, count))
    conn.commit()
    conn.close()


def update_schedule(sid, time_str=None, topic=None, count=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if time_str is not None:
        c.execute('UPDATE schedules SET time=? WHERE id=?', (time_str, sid))
    if topic is not None:
        c.execute('UPDATE schedules SET topic=? WHERE id=?', (topic, sid))
    if count is not None:
        c.execute('UPDATE schedules SET count=? WHERE id=?', (count, sid))
    conn.commit()
    conn.close()


def delete_schedule(sid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM schedules WHERE id=?', (sid,))
    conn.commit()
    conn.close()


def get_schedule(sid):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, time, topic, count FROM schedules WHERE id=?', (sid,))
    row = c.fetchone()
    conn.close()
    return row


# ---------- ИСТОРИЯ ПУБЛИКАЦИЙ ----------

def is_posted(link):
    """Проверяет, публиковалась ли ссылка раньше."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT 1 FROM posted WHERE link=?', (link,))
    res = c.fetchone()
    conn.close()
    return res is not None


def mark_posted(link):
    """Записывает ссылку как опубликованную."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT OR IGNORE INTO posted (link) VALUES (?)', (link,))
        conn.commit()
    finally:
        conn.close()


def cleanup_old_posts(days=30):
    """Удаляет из истории ссылки старше N дней (чтобы БД не пухла)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM posted WHERE published_at < datetime('now', ?)",
              (f'-{days} days',))
    conn.commit()
    conn.close()