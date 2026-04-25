import json
import math
import random
import re
import sqlite3
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


TOKEN = "8507728064:AAEk18wd1FXHQSOgfKExy6a6tgCFKDkbDBw"

EVENTS_PER_PAGE = 3
DEFAULT_EVENT_DURATION_MINUTES = 60

# =========================
# DB
# =========================
conn = sqlite3.connect("database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL
)
""")
conn.commit()


def ensure_columns():
    cursor.execute("PRAGMA table_info(events)")
    columns = [row[1] for row in cursor.fetchall()]

    if "remind" not in columns:
        cursor.execute("ALTER TABLE events ADD COLUMN remind INTEGER DEFAULT 0")
        conn.commit()

    if "repeat_rule" not in columns:
        cursor.execute("ALTER TABLE events ADD COLUMN repeat_rule TEXT DEFAULT 'none'")
        conn.commit()


ensure_columns()

# =========================
# STATE / SCHEDULER
# =========================
user_state = {}
scheduled_jobs = {}

scheduler = BackgroundScheduler()
scheduler.start()

telegram_app = None


# =========================
# HELPERS
# =========================
def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def format_dt(dt: datetime) -> str:
    return dt.strftime("%d.%m.%y %H:%M")


async def send(user_id: int, text: str, keyboard=None):
    global telegram_app

    if keyboard is None:
        keyboard = get_main_keyboard()

    await telegram_app.bot.send_message(
        chat_id=user_id,
        text=text,
        reply_markup=keyboard
    )


# =========================
# KEYBOARDS
# =========================
def make_keyboard(rows):
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False
    )


def get_main_keyboard():
    return make_keyboard([
        [KeyboardButton("➕ Добавить"), KeyboardButton("📋 Список")],
        [KeyboardButton("❌ Удалить"), KeyboardButton("🔔 Уведомления")],
        [KeyboardButton("✏️ Редактировать"), KeyboardButton("🔁 Повтор")],
        [KeyboardButton("📊 Оценки")],
    ])


def get_back_keyboard():
    return make_keyboard([
        [KeyboardButton("⬅️ Назад")]
    ])


def get_delete_keyboard(user_id: int, page: int = 0):
    events = get_events(user_id)

    if not events:
        return get_back_keyboard()

    total_pages = math.ceil(len(events) / EVENTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_index = page * EVENTS_PER_PAGE
    end_index = start_index + EVENTS_PER_PAGE
    page_events = events[start_index:end_index]

    rows = []

    for event in page_events:
        dt = datetime.fromisoformat(event["start"])
        button_text = f"🗑 {event['id']} | {event['name']} — {dt.strftime('%d.%m %H:%M')}"
        rows.append([KeyboardButton(button_text)])

    nav = []
    if page > 0:
        nav.append(KeyboardButton("⬅️ Страница удаления"))
    if page < total_pages - 1:
        nav.append(KeyboardButton("➡️ Страница удаления"))

    if nav:
        rows.append(nav)

    rows.append([KeyboardButton("⬅️ Назад")])
    return make_keyboard(rows)


def get_reminder_events_keyboard(user_id: int, page: int = 0):
    events = get_events(user_id)

    if not events:
        return get_back_keyboard()

    total_pages = math.ceil(len(events) / EVENTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_index = page * EVENTS_PER_PAGE
    end_index = start_index + EVENTS_PER_PAGE
    page_events = events[start_index:end_index]

    rows = []

    for event in page_events:
        dt = datetime.fromisoformat(event["start"])
        remind_text = "выкл" if not event["remind"] else f"{event['remind']} мин"
        button_text = f"🔔 {event['id']} | {event['name']} — {dt.strftime('%d.%m %H:%M')} | {remind_text}"
        rows.append([KeyboardButton(button_text)])

    nav = []
    if page > 0:
        nav.append(KeyboardButton("⬅️ Страница уведомлений"))
    if page < total_pages - 1:
        nav.append(KeyboardButton("➡️ Страница уведомлений"))

    if nav:
        rows.append(nav)

    rows.append([KeyboardButton("⬅️ Назад")])
    return make_keyboard(rows)


def get_reminder_options_keyboard():
    return make_keyboard([
        [KeyboardButton("🔕 Выкл"), KeyboardButton("5 мин")],
        [KeyboardButton("10 мин"), KeyboardButton("15 мин")],
        [KeyboardButton("30 мин"), KeyboardButton("60 мин")],
        [KeyboardButton("120 мин"), KeyboardButton("✍️ Свое значение")],
        [KeyboardButton("⬅️ Назад")]
    ])


def get_event_picker_keyboard(user_id: int, page: int = 0, prefix: str = "✏️"):
    events = get_events(user_id)

    if not events:
        return get_back_keyboard()

    total_pages = math.ceil(len(events) / EVENTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_index = page * EVENTS_PER_PAGE
    end_index = start_index + EVENTS_PER_PAGE
    page_events = events[start_index:end_index]

    rows = []

    for event in page_events:
        dt = datetime.fromisoformat(event["start"])
        button_text = f"{prefix} {event['id']} | {event['name']} — {dt.strftime('%d.%m %H:%M')}"
        rows.append([KeyboardButton(button_text)])

    nav = []
    if page > 0:
        nav.append(KeyboardButton(f"⬅️ Страница {prefix}"))
    if page < total_pages - 1:
        nav.append(KeyboardButton(f"➡️ Страница {prefix}"))

    if nav:
        rows.append(nav)

    rows.append([KeyboardButton("⬅️ Назад")])
    return make_keyboard(rows)


def get_edit_options_keyboard():
    return make_keyboard([
        [KeyboardButton("✏️ Название"), KeyboardButton("📅 Дата")],
        [KeyboardButton("⏰ Время")],
        [KeyboardButton("⬅️ Назад")]
    ])


def get_repeat_events_keyboard(user_id: int, page: int = 0):
    events = get_events(user_id)

    if not events:
        return get_back_keyboard()

    total_pages = math.ceil(len(events) / EVENTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_index = page * EVENTS_PER_PAGE
    end_index = start_index + EVENTS_PER_PAGE
    page_events = events[start_index:end_index]

    repeat_map = {
        "none": "выкл",
        "daily": "каждый день",
        "weekly": "каждую неделю"
    }

    rows = []

    for event in page_events:
        dt = datetime.fromisoformat(event["start"])
        repeat_text = repeat_map.get(event["repeat_rule"], "выкл")
        button_text = f"🔁 {event['id']} | {event['name']} — {dt.strftime('%d.%m %H:%M')} | {repeat_text}"
        rows.append([KeyboardButton(button_text)])

    nav = []
    if page > 0:
        nav.append(KeyboardButton("⬅️ Страница повтора"))
    if page < total_pages - 1:
        nav.append(KeyboardButton("➡️ Страница повтора"))

    if nav:
        rows.append(nav)

    rows.append([KeyboardButton("⬅️ Назад")])
    return make_keyboard(rows)


def get_repeat_options_keyboard():
    return make_keyboard([
        [KeyboardButton("🔕 Повтор выкл"), KeyboardButton("🔁 Каждый день")],
        [KeyboardButton("🔁 Каждую неделю")],
        [KeyboardButton("⬅️ Назад")]
    ])


# =========================
# FRIENDLY ERRORS
# =========================
def build_invalid_date_error():
    return (
        "Неверная дата.\n"
        "Используй формат дд.мм, дд.мм.гггг или дд.мм.гг.\n\n"
        "Примеры:\n"
        "Тренировка 13:00 11.11.26\n"
        "День рождения 13:00 11.11.2027\n"
        "11.11 Репетитор 13:00"
    )


def build_invalid_time_error():
    return (
        "Неверное время.\n"
        "Используй формат чч:мм.\n\n"
        "Примеры:\n"
        "Физика 13:00 11.11.2026\n"
        "11.11.26 13:00 Физика\n"
        "11.11.2026 Физика 13:00"
    )


# =========================
# CLEANUP / REPEAT
# =========================
def cleanup_past_non_repeating_events():
    now_iso = datetime.now().isoformat()

    cursor.execute(
        "SELECT id FROM events WHERE repeat_rule='none' AND end < ?",
        (now_iso,)
    )
    rows = cursor.fetchall()

    for row in rows:
        event_id = row[0]
        remove_scheduled_jobs_for_event(event_id)

    cursor.execute(
        "DELETE FROM events WHERE repeat_rule='none' AND end < ?",
        (now_iso,)
    )
    conn.commit()


def advance_repeating_events():
    now = datetime.now()

    cursor.execute(
        "SELECT id, user_id, name, start, end, remind, repeat_rule "
        "FROM events WHERE repeat_rule IN ('daily', 'weekly')"
    )
    rows = cursor.fetchall()

    for row in rows:
        event_id, user_id, name, start_text, end_text, remind, repeat_rule = row

        start_dt = datetime.fromisoformat(start_text)
        end_dt = datetime.fromisoformat(end_text)

        step = timedelta(days=1) if repeat_rule == "daily" else timedelta(days=7)

        changed = False
        while end_dt < now:
            start_dt += step
            end_dt += step
            changed = True

        if changed:
            cursor.execute(
                "UPDATE events SET start=?, end=? WHERE id=?",
                (start_dt.isoformat(), end_dt.isoformat(), event_id)
            )
            conn.commit()

            remove_scheduled_jobs_for_event(event_id)

            remind = remind if remind is not None else 0
            if remind > 0:
                schedule_event(event_id, user_id, name, start_dt, remind)


# =========================
# PARSER
# =========================
def extract_time(text: str):
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", text)
    if not match:
        raise ValueError(build_invalid_time_error())

    start_pos, end_pos = match.span()
    left = text[start_pos - 1] if start_pos > 0 else " "
    right = text[end_pos] if end_pos < len(text) else " "

    if left.isdigit() or right.isdigit():
        raise ValueError(build_invalid_time_error())

    hour = int(match.group(1))
    minute = int(match.group(2))
    return hour, minute, match.group(0)


def extract_date(text: str):
    now = datetime.now()

    match_full = re.search(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{2}|\d{4})(?!\d)", text)
    if match_full:
        day = int(match_full.group(1))
        month = int(match_full.group(2))
        year_raw = match_full.group(3)

        if len(year_raw) == 2:
            year = 2000 + int(year_raw)
        else:
            year = int(year_raw)

        if year < now.year:
            raise ValueError("Год должен быть текущим или будущим")

        try:
            dt = datetime(year, month, day)
        except ValueError:
            raise ValueError(build_invalid_date_error())

        return dt, match_full.group(0)

    match_short = re.search(r"(?<!\d)(\d{2})\.(\d{2})(?![\.\d])", text)
    if match_short:
        day = int(match_short.group(1))
        month = int(match_short.group(2))
        year = now.year

        try:
            dt = datetime(year, month, day)
        except ValueError:
            raise ValueError(build_invalid_date_error())

        return dt, match_short.group(0)

    raise ValueError(build_invalid_date_error())


def parse_event_input(text: str):
    source_text = normalize_spaces(text)

    if not source_text:
        raise ValueError("Пустой ввод")

    hour, minute, found_time_text = extract_time(source_text)
    date_dt, found_date_text = extract_date(source_text)

    start_dt = date_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    end_dt = start_dt + timedelta(minutes=DEFAULT_EVENT_DURATION_MINUTES)

    if start_dt < datetime.now():
        raise ValueError("Нельзя добавить событие в прошлом")

    name = source_text
    name = name.replace(found_time_text, " ", 1)
    name = name.replace(found_date_text, " ", 1)
    name = normalize_spaces(name)

    if not name:
        raise ValueError("Не удалось определить название события")

    return name, start_dt, end_dt


def parse_date_only_input(text: str):
    date_dt, _ = extract_date(normalize_spaces(text))
    return date_dt


def parse_time_only_input(text: str):
    source_text = normalize_spaces(text)
    hour, minute, _ = extract_time(source_text)
    return hour, minute


# =========================
# GRADES
# =========================
def parse_grade_token(token: str):
    token = token.strip().lower().replace("х", "x")
    if not token:
        raise ValueError

    coefficient = 1
    coef_match = re.fullmatch(r"(.+?)x([1-9]\d*)", token)

    if coef_match:
        base_part = coef_match.group(1)
        coefficient = int(coef_match.group(2))
    else:
        base_part = token

    if "/" in base_part:
        parts = base_part.split("/")
        if len(parts) != 2:
            raise ValueError

        a = int(parts[0])
        b = int(parts[1])

        if not (1 <= a <= 5 and 1 <= b <= 5):
            raise ValueError

        value = (a + b) / 2
    else:
        value = int(base_part)

        if not (1 <= value <= 5):
            raise ValueError

    return [value] * coefficient


def parse_grades_input(text: str):
    tokens = text.split()

    if not tokens:
        raise ValueError

    values = []

    for token in tokens:
        values.extend(parse_grade_token(token))

    if not values:
        raise ValueError

    return values


# =========================
# DB FUNCTIONS
# =========================
def get_events(user_id: int):
    cleanup_past_non_repeating_events()
    advance_repeating_events()

    cursor.execute(
        "SELECT id, name, start, end, remind, repeat_rule FROM events WHERE user_id=? ORDER BY start",
        (user_id,)
    )
    rows = cursor.fetchall()

    result = []

    for row in rows:
        result.append({
            "id": row[0],
            "name": row[1],
            "start": row[2],
            "end": row[3],
            "remind": row[4] if row[4] is not None else 0,
            "repeat_rule": row[5] if row[5] is not None else "none",
        })

    return result


def get_event_by_id(user_id: int, event_id: int):
    cleanup_past_non_repeating_events()
    advance_repeating_events()

    cursor.execute(
        "SELECT id, name, start, end, remind, repeat_rule FROM events WHERE user_id=? AND id=?",
        (user_id, event_id)
    )
    row = cursor.fetchone()

    if not row:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "start": row[2],
        "end": row[3],
        "remind": row[4] if row[4] is not None else 0,
        "repeat_rule": row[5] if row[5] is not None else "none",
    }


def show_events_text(user_id: int) -> str:
    events = get_events(user_id)

    if not events:
        return "📭 У тебя нет событий"

    lines = ["📅 Твои события:\n"]

    repeat_map = {
        "none": "",
        "daily": "🔁 каждый день",
        "weekly": "🔁 каждую неделю"
    }

    for i, event in enumerate(events, start=1):
        start_dt = datetime.fromisoformat(event["start"])
        line = f"{i}. {event['name']} — {format_dt(start_dt)}"

        if event["remind"]:
            line += f" | 🔔 {event['remind']} мин"
        else:
            line += " | 🔕"

        repeat_text = repeat_map.get(event.get("repeat_rule", "none"), "")
        if repeat_text:
            line += f" | {repeat_text}"

        lines.append(line)

    return "\n".join(lines)


def create_event(user_id: int, name: str, start_dt: datetime, end_dt: datetime):
    cursor.execute(
        "INSERT INTO events (user_id, name, start, end, remind, repeat_rule) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name, start_dt.isoformat(), end_dt.isoformat(), 0, "none")
    )
    conn.commit()
    return cursor.lastrowid


def delete_event_by_id(user_id: int, event_id: int) -> bool:
    cursor.execute(
        "SELECT id FROM events WHERE id=? AND user_id=?",
        (event_id, user_id)
    )
    row = cursor.fetchone()

    if not row:
        return False

    remove_scheduled_jobs_for_event(event_id)

    cursor.execute(
        "DELETE FROM events WHERE id=? AND user_id=?",
        (event_id, user_id)
    )
    conn.commit()

    return True


def set_event_reminder(user_id: int, event_id: int, minutes: int) -> bool:
    cursor.execute(
        "SELECT id, name, start FROM events WHERE id=? AND user_id=?",
        (event_id, user_id)
    )
    row = cursor.fetchone()

    if not row:
        return False

    cursor.execute(
        "UPDATE events SET remind=? WHERE id=? AND user_id=?",
        (minutes, event_id, user_id)
    )
    conn.commit()

    event_name = row[1]
    start_dt = datetime.fromisoformat(row[2])

    if minutes <= 0:
        remove_scheduled_jobs_for_event(event_id)
    else:
        schedule_event(event_id, user_id, event_name, start_dt, minutes)

    return True


def set_event_repeat(user_id: int, event_id: int, repeat_rule: str) -> bool:
    cursor.execute(
        "SELECT id FROM events WHERE id=? AND user_id=?",
        (event_id, user_id)
    )
    row = cursor.fetchone()

    if not row:
        return False

    cursor.execute(
        "UPDATE events SET repeat_rule=? WHERE id=? AND user_id=?",
        (repeat_rule, event_id, user_id)
    )
    conn.commit()

    advance_repeating_events()
    restore_jobs_from_db()

    return True


def update_event_name(user_id: int, event_id: int, new_name: str) -> bool:
    cursor.execute(
        "SELECT id FROM events WHERE id=? AND user_id=?",
        (event_id, user_id)
    )
    row = cursor.fetchone()

    if not row:
        return False

    cursor.execute(
        "UPDATE events SET name=? WHERE id=? AND user_id=?",
        (new_name, event_id, user_id)
    )
    conn.commit()

    return True


def update_event_date(user_id: int, event_id: int, new_date: datetime) -> bool:
    event_data = get_event_by_id(user_id, event_id)

    if not event_data:
        return False

    old_start = datetime.fromisoformat(event_data["start"])
    old_end = datetime.fromisoformat(event_data["end"])
    duration = old_end - old_start

    new_start = new_date.replace(
        hour=old_start.hour,
        minute=old_start.minute,
        second=0,
        microsecond=0
    )
    new_end = new_start + duration

    if new_start < datetime.now():
        raise ValueError("Нельзя поставить событие в прошлом")

    cursor.execute(
        "UPDATE events SET start=?, end=? WHERE id=? AND user_id=?",
        (new_start.isoformat(), new_end.isoformat(), event_id, user_id)
    )
    conn.commit()

    if event_data["remind"] > 0:
        schedule_event(event_id, user_id, event_data["name"], new_start, event_data["remind"])

    return True


def update_event_time(user_id: int, event_id: int, hour: int, minute: int) -> bool:
    event_data = get_event_by_id(user_id, event_id)

    if not event_data:
        return False

    old_start = datetime.fromisoformat(event_data["start"])
    old_end = datetime.fromisoformat(event_data["end"])
    duration = old_end - old_start

    new_start = old_start.replace(hour=hour, minute=minute, second=0, microsecond=0)
    new_end = new_start + duration

    if new_start < datetime.now():
        raise ValueError("Нельзя поставить событие в прошлом")

    cursor.execute(
        "UPDATE events SET start=?, end=? WHERE id=? AND user_id=?",
        (new_start.isoformat(), new_end.isoformat(), event_id, user_id)
    )
    conn.commit()

    if event_data["remind"] > 0:
        schedule_event(event_id, user_id, event_data["name"], new_start, event_data["remind"])

    return True


# =========================
# REMINDERS
# =========================
def remove_scheduled_jobs_for_event(event_id: int):
    job_ids = scheduled_jobs.get(event_id, [])

    for job_id in job_ids:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

    scheduled_jobs.pop(event_id, None)


async def send_reminder_async(user_id: int, name: str, start_text: str):
    await telegram_app.bot.send_message(
        chat_id=user_id,
        text=f"⏰ Напоминание: {name}\nНачало: {start_text}",
        reply_markup=get_main_keyboard()
    )


def send_reminder(user_id: int, name: str, start_text: str):
    telegram_app.create_task(send_reminder_async(user_id, name, start_text))


def schedule_event(event_id: int, user_id: int, name: str, start_dt: datetime, remind_minutes: int):
    remove_scheduled_jobs_for_event(event_id)

    if remind_minutes <= 0:
        return

    remind_time = start_dt - timedelta(minutes=remind_minutes)

    if remind_time <= datetime.now():
        return

    job_id = f"event_{event_id}_remind"

    scheduler.add_job(
        send_reminder,
        trigger="date",
        run_date=remind_time,
        args=[user_id, name, format_dt(start_dt)],
        id=job_id,
        replace_existing=True
    )

    scheduled_jobs[event_id] = [job_id]


def restore_jobs_from_db():
    cleanup_past_non_repeating_events()
    advance_repeating_events()

    for event_id in list(scheduled_jobs.keys()):
        remove_scheduled_jobs_for_event(event_id)

    cursor.execute("SELECT id, user_id, name, start, remind FROM events")
    rows = cursor.fetchall()

    for row in rows:
        event_id, user_id, name, start_text, remind = row
        remind = remind if remind is not None else 0

        if remind <= 0:
            continue

        start_dt = datetime.fromisoformat(start_text)

        if start_dt > datetime.now():
            schedule_event(event_id, user_id, name, start_dt, remind)


# =========================
# TEXT HELPERS
# =========================
def extract_id_from_button(text: str):
    match = re.search(r"(\d+)\s*\|", text)
    if not match:
        return None
    return int(match.group(1))


# =========================
# HANDLERS
# =========================
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state.pop(user_id, None)

    await send(
        user_id,
        "👋 Привет! Я твой планировщик задач 📅\n\n"
        "Я умею:\n"
        "• ➕ Добавлять события\n"
        "• 📋 Показывать список событий\n"
        "• ❌ Удалять события\n"
        "• 🔔 Напоминать о событиях\n"
        "• 🔁 Повторять события каждый день или неделю\n"
        "• ✏️ Редактировать события\n"
        "• 📊 Считать средний балл\n\n"
        "📌 Как добавить событие:\n"
        "Напиши одной строкой, например:\n"
        "Тренировка 13:00 11.11\n"
        "Репетитор 18:00 12.12.2026\n\n"
        "📅 Форматы:\n"
        "• Дата: дд.мм, дд.мм.гггг или дд.мм.гг\n"
        "• Время: 13:00\n\n"
        "💡 Если не указать год — возьмётся текущий\n\n"
        "Понадобится помощь — пиши Помощь или /help\n\n"
        "👇 Выбери действие:",
        keyboard=get_main_keyboard()
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    await send(
        user_id,
        "📖 Инструкция:\n\n"
        "➕ Добавление:\n"
        "Тренировка 13:00 11.11\n"
        "11.11.26 13:00 Репетитор\n"
        "Программирование 18:00 12.12.2026\n\n"
        "📋 Посмотреть список — кнопка «Список»\n"
        "❌ Удалить событие — кнопка «Удалить»\n"
        "🔔 Установить напоминания — кнопка «Уведомления»\n"
        "🔁 Настроить повтор — кнопка «Повтор»\n\n"
        "📊 Оценки:\n"
        "Пример: 5 4 3 5\n"
        "С коэффициентами: 5x3 4x2\n"
        "В одной клетке: 3/4 4/5x3\n\n"
        "⬅️ Назад — всегда возвращает в меню",
        keyboard=get_main_keyboard()
    )


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    cleanup_past_non_repeating_events()
    advance_repeating_events()

    state = user_state.get(user_id)

    # ========= BACK =========
    if text == "⬅️ Назад":
        user_state.pop(user_id, None)
        await send(user_id, "Главное меню", keyboard=get_main_keyboard())
        return

    # ========= HELP =========
    if text.lower() in {"помощь", "help", "/help"}:
        await help_handler(update, context)
        return

    # ========= LIST =========
    if text == "📋 Список":
        user_state.pop(user_id, None)
        await send(user_id, show_events_text(user_id), keyboard=get_main_keyboard())
        return

    # ========= GRADES =========
    if text == "📊 Оценки":
        user_state[user_id] = {"action": "grades"}

        await send(
            user_id,
            "Введи оценки через пробел.\n\n"
            "Примеры:\n"
            "5 4 3\n"
            "5x3 4 3\n"
            "5х3 4 3\n"
            "3/4 5\n"
            "3/4x3 5\n"
            "4/5х3 3",
            keyboard=get_back_keyboard()
        )
        return

    if state and state.get("action") == "grades":
        try:
            values = parse_grades_input(text)
            avg = sum(values) / len(values)

            user_state.pop(user_id, None)
            await send(user_id, f"📊 Средний балл: {avg:.2f}", keyboard=get_main_keyboard())

        except Exception:
            await send(
                user_id,
                "❌ Неправильный ввод.\n\n"
                "Примеры:\n"
                "5 4 3\n"
                "5x3 4 3\n"
                "5х3 4 3\n"
                "3/4 5\n"
                "3/4x3 5\n"
                "4/5х3 3",
                keyboard=get_back_keyboard()
            )
        return

    # ========= ADD =========
    if text == "➕ Добавить":
        user_state[user_id] = {"action": "add"}

        await send(
            user_id,
            "Введи событие одной строкой.\n\n"
            "Формат даты: дд.мм, дд.мм.гггг или дд.мм.гг\n\n"
            "Примеры:\n"
            "Поездка 13:00 11.11.2027\n"
            "11.11 Тренировка 13:00\n"
            "11.11.26 Репетитор 13:00",
            keyboard=get_back_keyboard()
        )
        return

    if state and state.get("action") == "add":
        try:
            name, start_dt, end_dt = parse_event_input(text)
            create_event(user_id=user_id, name=name, start_dt=start_dt, end_dt=end_dt)

            user_state.pop(user_id, None)

            await send(
                user_id,
                "✅ Событие добавлено!\n\n" + show_events_text(user_id),
                keyboard=get_main_keyboard()
            )

        except Exception as e:
            await send(
                user_id,
                f"❌ {str(e)}\n\n"
                "Попробуй ещё раз.\n"
                "Примеры:\n"
                "Репетитор 13:00 11.11\n"
                "11.11.2026 Тренировка 13:00\n"
                "Встреча 11.11.27 13:00",
                keyboard=get_back_keyboard()
            )
        return

    # ========= DELETE =========
    if text == "❌ Удалить":
        user_state[user_id] = {"action": "delete_menu", "page": 0}

        if not get_events(user_id):
            await send(user_id, "📭 У тебя нет событий", keyboard=get_back_keyboard())
        else:
            await send(
                user_id,
                "Выбери событие для удаления:",
                keyboard=get_delete_keyboard(user_id, page=0)
            )
        return

    if text in {"⬅️ Страница удаления", "➡️ Страница удаления"}:
        state = user_state.get(user_id, {"page": 0})
        page = state.get("page", 0)

        if text.startswith("⬅️"):
            page -= 1
        else:
            page += 1

        user_state[user_id] = {"action": "delete_menu", "page": page}

        await send(
            user_id,
            f"Выбери событие для удаления:\nСтраница {page + 1}",
            keyboard=get_delete_keyboard(user_id, page=page)
        )
        return

    if text.startswith("🗑 "):
        event_id = extract_id_from_button(text)

        if event_id is None:
            await send(user_id, "❌ Не удалось определить событие.", keyboard=get_main_keyboard())
            return

        deleted = delete_event_by_id(user_id, event_id)
        user_state.pop(user_id, None)

        if deleted:
            await send(user_id, "🗑 Событие удалено.", keyboard=get_main_keyboard())
        else:
            await send(user_id, "❌ Не удалось удалить событие.", keyboard=get_main_keyboard())
        return

    # ========= REMINDERS =========
    if text == "🔔 Уведомления":
        user_state[user_id] = {"action": "remind_menu", "page": 0}

        if not get_events(user_id):
            await send(user_id, "📭 У тебя нет событий", keyboard=get_back_keyboard())
        else:
            await send(
                user_id,
                "Выбери событие для настройки уведомления:",
                keyboard=get_reminder_events_keyboard(user_id, page=0)
            )
        return

    if text in {"⬅️ Страница уведомлений", "➡️ Страница уведомлений"}:
        state = user_state.get(user_id, {"page": 0})
        page = state.get("page", 0)

        if text.startswith("⬅️"):
            page -= 1
        else:
            page += 1

        user_state[user_id] = {"action": "remind_menu", "page": page}

        await send(
            user_id,
            f"Выбери событие для настройки уведомления:\nСтраница {page + 1}",
            keyboard=get_reminder_events_keyboard(user_id, page=page)
        )
        return

    if text.startswith("🔔 "):
        event_id = extract_id_from_button(text)

        if event_id is None:
            await send(user_id, "❌ Не удалось определить событие.", keyboard=get_main_keyboard())
            return

        event_data = get_event_by_id(user_id, event_id)

        if not event_data:
            await send(user_id, "❌ Событие не найдено.", keyboard=get_main_keyboard())
            return

        start_dt = datetime.fromisoformat(event_data["start"])
        current = "выключено" if not event_data["remind"] else f"{event_data['remind']} мин"

        user_state[user_id] = {"action": "remind_options", "event_id": event_id}

        await send(
            user_id,
            f"Событие: {event_data['name']}\n"
            f"Дата: {format_dt(start_dt)}\n"
            f"Сейчас уведомление: {current}\n\n"
            f"Выбери, за сколько минут напоминать:",
            keyboard=get_reminder_options_keyboard()
        )
        return

    if state and state.get("action") == "remind_options":
        event_id = state["event_id"]

        reminder_map = {
            "🔕 Выкл": 0,
            "5 мин": 5,
            "10 мин": 10,
            "15 мин": 15,
            "30 мин": 30,
            "60 мин": 60,
            "120 мин": 120,
        }

        if text == "✍️ Свое значение":
            user_state[user_id] = {"action": "remind_custom", "event_id": event_id}
            await send(
                user_id,
                "Введи количество минут целым неотрицательным числом.\nНапример: 25",
                keyboard=get_back_keyboard()
            )
            return

        if text in reminder_map:
            minutes = reminder_map[text]
            ok = set_event_reminder(user_id, event_id, minutes)
            user_state.pop(user_id, None)

            if ok:
                if minutes == 0:
                    await send(user_id, "🔕 Уведомление выключено.", keyboard=get_main_keyboard())
                else:
                    await send(user_id, f"🔔 Уведомление установлено за {minutes} мин.", keyboard=get_main_keyboard())
            else:
                await send(user_id, "❌ Не удалось изменить уведомление.", keyboard=get_main_keyboard())
            return

    if state and state.get("action") == "remind_custom":
        token = text.strip()

        if not re.fullmatch(r"\d+", token):
            await send(
                user_id,
                "❌ Неправильный ввод.\nВведи целое неотрицательное число.\nНапример: 25",
                keyboard=get_back_keyboard()
            )
            return

        minutes = int(token)
        event_id = state["event_id"]
        ok = set_event_reminder(user_id, event_id, minutes)
        user_state.pop(user_id, None)

        if not ok:
            await send(user_id, "❌ Не удалось изменить уведомление.", keyboard=get_main_keyboard())
        else:
            if minutes == 0:
                await send(user_id, "🔕 Уведомление выключено.", keyboard=get_main_keyboard())
            else:
                await send(user_id, f"🔔 Уведомление установлено за {minutes} мин.", keyboard=get_main_keyboard())
        return

    # ========= EDIT =========
    if text == "✏️ Редактировать":
        user_state[user_id] = {"action": "edit_menu", "page": 0}

        if not get_events(user_id):
            await send(user_id, "📭 У тебя нет событий", keyboard=get_back_keyboard())
        else:
            await send(
                user_id,
                "Выбери событие для редактирования:",
                keyboard=get_event_picker_keyboard(user_id, page=0, prefix="✏️")
            )
        return

    if text in {"⬅️ Страница ✏️", "➡️ Страница ✏️"}:
        state = user_state.get(user_id, {"page": 0})
        page = state.get("page", 0)

        if text.startswith("⬅️"):
            page -= 1
        else:
            page += 1

        user_state[user_id] = {"action": "edit_menu", "page": page}

        await send(
            user_id,
            f"Выбери событие для редактирования:\nСтраница {page + 1}",
            keyboard=get_event_picker_keyboard(user_id, page=page, prefix="✏️")
        )
        return

    if text.startswith("✏️ ") and "|" in text:
        event_id = extract_id_from_button(text)

        if event_id is None:
            await send(user_id, "❌ Не удалось определить событие.", keyboard=get_main_keyboard())
            return

        event_data = get_event_by_id(user_id, event_id)

        if not event_data:
            await send(user_id, "❌ Событие не найдено.", keyboard=get_main_keyboard())
            return

        user_state[user_id] = {"action": "edit_options", "event_id": event_id}

        await send(
            user_id,
            f"Событие: {event_data['name']}\n"
            f"Дата: {format_dt(datetime.fromisoformat(event_data['start']))}\n\n"
            "Что изменить?",
            keyboard=get_edit_options_keyboard()
        )
        return

    if state and state.get("action") == "edit_options":
        event_id = state["event_id"]

        if text == "✏️ Название":
            user_state[user_id] = {"action": "edit_wait_name", "event_id": event_id}
            await send(user_id, "Введи новое название:", keyboard=get_back_keyboard())
            return

        if text == "📅 Дата":
            user_state[user_id] = {"action": "edit_wait_date", "event_id": event_id}
            await send(
                user_id,
                "Введи новую дату в формате дд.мм, дд.мм.гг или дд.мм.гггг\n"
                "Примеры:\n"
                "11.11\n"
                "11.11.26\n"
                "11.11.2026",
                keyboard=get_back_keyboard()
            )
            return

        if text == "⏰ Время":
            user_state[user_id] = {"action": "edit_wait_time", "event_id": event_id}
            await send(
                user_id,
                "Введи новое время.\nПример:\n18:00",
                keyboard=get_back_keyboard()
            )
            return

    if state and state.get("action") == "edit_wait_name":
        event_id = state["event_id"]
        new_name = normalize_spaces(text)

        if not new_name:
            await send(user_id, "❌ Название не может быть пустым.", keyboard=get_back_keyboard())
            return

        ok = update_event_name(user_id, event_id, new_name)
        user_state.pop(user_id, None)

        if ok:
            await send(user_id, "✅ Название обновлено.", keyboard=get_main_keyboard())
        else:
            await send(user_id, "❌ Не удалось обновить событие.", keyboard=get_main_keyboard())
        return

    if state and state.get("action") == "edit_wait_date":
        event_id = state["event_id"]

        try:
            new_date = parse_date_only_input(text)
            ok = update_event_date(user_id, event_id, new_date)
            user_state.pop(user_id, None)

            if ok:
                await send(user_id, "✅ Дата обновлена.", keyboard=get_main_keyboard())
            else:
                await send(user_id, "❌ Не удалось обновить событие.", keyboard=get_main_keyboard())

        except Exception as e:
            await send(user_id, f"❌ {str(e)}", keyboard=get_back_keyboard())
        return

    if state and state.get("action") == "edit_wait_time":
        event_id = state["event_id"]

        try:
            hour, minute = parse_time_only_input(text)
            ok = update_event_time(user_id, event_id, hour, minute)
            user_state.pop(user_id, None)

            if ok:
                await send(user_id, "✅ Время обновлено.", keyboard=get_main_keyboard())
            else:
                await send(user_id, "❌ Не удалось обновить событие.", keyboard=get_main_keyboard())

        except Exception as e:
            await send(user_id, f"❌ {str(e)}", keyboard=get_back_keyboard())
        return

    # ========= REPEAT =========
    if text == "🔁 Повтор":
        user_state[user_id] = {"action": "repeat_menu", "page": 0}

        if not get_events(user_id):
            await send(user_id, "📭 У тебя нет событий", keyboard=get_back_keyboard())
        else:
            await send(
                user_id,
                "Выбери событие для настройки повтора:",
                keyboard=get_repeat_events_keyboard(user_id, page=0)
            )
        return

    if text in {"⬅️ Страница повтора", "➡️ Страница повтора"}:
        state = user_state.get(user_id, {"page": 0})
        page = state.get("page", 0)

        if text.startswith("⬅️"):
            page -= 1
        else:
            page += 1

        user_state[user_id] = {"action": "repeat_menu", "page": page}

        await send(
            user_id,
            f"Выбери событие для настройки повтора:\nСтраница {page + 1}",
            keyboard=get_repeat_events_keyboard(user_id, page=page)
        )
        return

    if text.startswith("🔁 ") and "|" in text:
        event_id = extract_id_from_button(text)

        if event_id is None:
            await send(user_id, "❌ Не удалось определить событие.", keyboard=get_main_keyboard())
            return

        event_data = get_event_by_id(user_id, event_id)

        if not event_data:
            await send(user_id, "❌ Событие не найдено.", keyboard=get_main_keyboard())
            return

        start_dt = datetime.fromisoformat(event_data["start"])

        repeat_map = {
            "none": "выключен",
            "daily": "каждый день",
            "weekly": "каждую неделю"
        }

        current_repeat = repeat_map.get(event_data["repeat_rule"], "выключен")

        user_state[user_id] = {"action": "repeat_options", "event_id": event_id}

        await send(
            user_id,
            f"Событие: {event_data['name']}\n"
            f"Дата: {format_dt(start_dt)}\n"
            f"Сейчас повтор: {current_repeat}\n\n"
            "Выбери вариант:",
            keyboard=get_repeat_options_keyboard()
        )
        return

    if state and state.get("action") == "repeat_options":
        event_id = state["event_id"]

        repeat_map = {
            "🔕 Повтор выкл": "none",
            "🔁 Каждый день": "daily",
            "🔁 Каждую неделю": "weekly",
        }

        if text in repeat_map:
            rule = repeat_map[text]
            ok = set_event_repeat(user_id, event_id, rule)
            user_state.pop(user_id, None)

            if ok:
                text_map = {
                    "none": "🔕 Повтор выключен.",
                    "daily": "🔁 Повтор установлен: каждый день.",
                    "weekly": "🔁 Повтор установлен: каждую неделю."
                }

                await send(user_id, text_map.get(rule, "✅ Повтор обновлён."), keyboard=get_main_keyboard())
            else:
                await send(user_id, "❌ Не удалось изменить повтор.", keyboard=get_main_keyboard())
            return

    # ========= DEFAULT =========
    await send(user_id, "Выбери действие в меню:", keyboard=get_main_keyboard())


# =========================
# START APP
# =========================
def main():
    global telegram_app

    cleanup_past_non_repeating_events()
    advance_repeating_events()

    app = Application.builder().token(TOKEN).build()
    telegram_app = app

    restore_jobs_from_db()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("Telegram бот запущен...")

    app.run_polling()


if __name__ == "__main__":
    main()