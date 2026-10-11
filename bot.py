import re
import time
import sys
import os
import threading
import logging
from collections import deque
from datetime import datetime, timedelta, timezone

import feedparser
import telebot
from telebot import types
import schedule as sch

from config import (
    BOT_TOKEN, CHANNEL_ID, ADMIN_USERNAME, TOPICS,
    MAX_NEWS_AGE_HOURS, MAX_LOOKBACK_ENTRIES
)
from database import (
    init_db, get_all_schedules, add_schedule, update_schedule,
    delete_schedule, get_schedule,
    is_posted, mark_posted, cleanup_old_posts
)

# ---------- ЛОГИРОВАНИЕ ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# ---------- БУФЕР ЛОГОВ ДЛЯ /console ----------
LOG_BUFFER = deque(maxlen=200)  # храним 200 строк, показываем 15


class BufferHandler(logging.Handler):
    """Пишет логи в память, чтобы показывать их в админке."""
    def emit(self, record):
        try:
            LOG_BUFFER.append(self.format(record))
        except Exception:
            pass


_buffer_handler = BufferHandler()
_buffer_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
)
logging.getLogger().addHandler(_buffer_handler)


def get_last_logs(n=15):
    lines = list(LOG_BUFFER)[-n:]
    if not lines:
        return '(пусто)'
    return '\n'.join(lines)


# ---------- ЗАПАСНОЙ ADMIN_ID ----------
# Если username не установлен — можно разрешить по ID.
# Узнать ID: @userinfobot. По умолчанию — None.
ADMIN_ID = 8277048733

bot = telebot.TeleBot(BOT_TOKEN)

# Быстрые варианты для мастера
QUICK_TIMES = ['09:00', '12:00', '15:00', '18:00', '21:00']
QUICK_COUNTS = [1, 2, 3, 5, 0]


# ============================================================
#  УТИЛИТЫ
# ============================================================

def is_admin(message):
    """Проверяет, что автор сообщения — админ.
       Сначала по ID (если задан), потом по username.
    """
    try:
        if not message or not getattr(message, 'from_user', None):
            return False

        user = message.from_user

        # 1. Проверка по ID — приоритетная
        admin_id = ADMIN_ID
        if not admin_id:
            try:
                from config import ADMIN_ID as _cfg_id
                admin_id = _cfg_id
            except Exception:
                admin_id = None

        if admin_id and user.id == admin_id:
            log.info(f'✅ Доступ разрешён по ID: {user.id}')
            return True

        # 2. Проверка по username
        if user.username and user.username.lower() == ADMIN_USERNAME.lower():
            log.info(f'✅ Доступ разрешён по username: @{user.username}')
            return True

        # 3. Отказ
        log.warning(
            f'⛔ Отказано: @{user.username or "нет username"} '
            f'(id={user.id}) — не совпадает ни с ADMIN_ID, ни с {ADMIN_USERNAME}'
        )
        return False
    except Exception as ex:
        log.error(f'is_admin error: {ex}')
        return False


def clean_html(raw):
    """Убирает HTML-теги и обрезает текст."""
    text = re.sub('<.*?>', '', raw or '')
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:300] + '...' if len(text) > 300 else text


def safe_edit(call, text, kb=None):
    """Безопасное редактирование (игнорит 'message is not modified')."""
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=kb
        )
    except Exception as ex:
        if 'not modified' not in str(ex).lower():
            log.error(f'safe_edit: {ex}')


# ============================================================
#  СБОР НОВОСТЕЙ
# ============================================================

def fetch_news(topic_key):
    feeds = TOPICS[topic_key]['feeds']
    now = datetime.now(timezone.utc)
    news = []

    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:MAX_LOOKBACK_ENTRIES]:
                link = e.get('link')
                title = e.get('title')
                if not link or not title:
                    continue

                pub_date = None
                for field in ('published_parsed', 'updated_parsed'):
                    val = e.get(field)
                    if val:
                        try:
                            pub_date = datetime(*val[:6], tzinfo=timezone.utc)
                            break
                        except Exception:
                            pass

                if pub_date and (now - pub_date) > timedelta(hours=MAX_NEWS_AGE_HOURS):
                    continue

                news.append({
                    'title': title,
                    'link': link,
                    'summary': e.get('summary', ''),
                    'date': pub_date
                })
        except Exception as ex:
            log.error(f'RSS ERROR {url}: {ex}')

    news.sort(
        key=lambda x: x['date'] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )
    return news


def fetch_all_news_ignore_age():
    all_news = []
    for topic_key in TOPICS.keys():
        for url in TOPICS[topic_key]['feeds']:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:MAX_LOOKBACK_ENTRIES]:
                    link = e.get('link')
                    title = e.get('title')
                    if not link or not title:
                        continue

                    pub_date = None
                    for field in ('published_parsed', 'updated_parsed'):
                        val = e.get(field)
                        if val:
                            try:
                                pub_date = datetime(*val[:6], tzinfo=timezone.utc)
                                break
                            except Exception:
                                pass

                    all_news.append({
                        'title': title,
                        'link': link,
                        'summary': e.get('summary', ''),
                        'date': pub_date,
                        'topic': topic_key
                    })
            except Exception as ex:
                log.error(f'RSS ERROR {url}: {ex}')

    all_news.sort(
        key=lambda x: x['date'] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )
    return all_news


# ============================================================
#  ПУБЛИКАЦИЯ
# ============================================================

def publish(topic_key, count):
    if count <= 0:
        log.info(f'Слот пропущен (count=0), тема: {topic_key}')
        return

    log.info(f'Публикация: тема={topic_key}, нужно={count}')
    news = fetch_news(topic_key)

    published = 0
    skipped_dup = 0

    for n in news:
        if published >= count:
            break
        if is_posted(n['link']):
            skipped_dup += 1
            continue

        text = (f"📰 *{n['title']}*\n\n"
                f"{clean_html(n['summary'])}\n\n"
                f"🔗 [Читать полностью]({n['link']})")
        try:
            bot.send_message(CHANNEL_ID, text,
                             parse_mode='Markdown',
                             disable_web_page_preview=False)
            mark_posted(n['link'])
            published += 1
            log.info(f'✅ Опубликовано: {n["title"][:60]}')
            time.sleep(2)
        except Exception as ex:
            log.error(f'SEND ERROR: {ex}')

    if published == 0:
        log.warning(f'⚠️ Новых новостей нет ({topic_key}). Дублей: {skipped_dup}')
    elif published < count:
        log.warning(f'⚠️ Опубликовано {published} из {count}')


# ============================================================
#  ПЛАНИРОВЩИК
# ============================================================

def reload_schedules():
    sch.clear()
    for sid, time_str, topic, count in get_all_schedules():
        sch.every().day.at(time_str).do(publish, topic_key=topic, count=count)
        log.info(f'[SCHEDULE] {time_str} | {topic} | {count}')


def scheduler_loop():
    while True:
        try:
            sch.run_pending()
        except Exception as ex:
            log.error(f'SCHEDULER ERROR: {ex}')
        time.sleep(20)


def cleanup_loop():
    while True:
        try:
            cleanup_old_posts(days=30)
        except Exception as ex:
            log.error(f'CLEANUP ERROR: {ex}')
        time.sleep(24 * 60 * 60)


# ============================================================
#  КЛАВИАТУРЫ
# ============================================================

def main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton('📋 Расписания', callback_data='list'),
        types.InlineKeyboardButton('➕ Добавить', callback_data='add'),
    )
    kb.add(
        types.InlineKeyboardButton('⚙️ Прочее', callback_data='other'),
    )
    return kb


def back_button():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton('⬅️ Назад', callback_data='menu'))
    return kb


def topics_kb(prefix):
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for key, val in TOPICS.items():
        buttons.append(types.InlineKeyboardButton(
            val['name'], callback_data=f'{prefix}_{key}'
        ))
    kb.add(*buttons)
    return kb


# ============================================================
#  КОМАНДЫ
# ============================================================

@bot.message_handler(commands=['start', 'admin'])
def cmd_start(message):
    if not is_admin(message):
        bot.reply_to(message, '⛔ Доступ запрещен.')
        return
    bot.send_message(
        message.chat.id,
        '🛠 *Админ-консоль новостного бота*\n\n'
        '• /admin — открыть меню\n'
        '• /news — опубликовать самую свежую новость прямо сейчас',
        parse_mode='Markdown',
        reply_markup=main_menu()
    )


@bot.message_handler(commands=['news'])
def cmd_news(message):
    if not is_admin(message):
        bot.reply_to(message, '⛔ Доступ запрещен.')
        return

    bot.reply_to(message, '🔎 Ищу самую свежую новость...')
    try:
        all_news = fetch_all_news_ignore_age()
    except Exception as ex:
        log.error(f'cmd_news fetch: {ex}')
        bot.send_message(message.chat.id, f'❌ Ошибка поиска: {ex}')
        return

    if not all_news:
        bot.send_message(message.chat.id, '❌ Не удалось найти ни одной новости.')
        return

    n = all_news[0]
    topic_name = TOPICS.get(n['topic'], {}).get('name', n['topic'])
    text = (f"📰 *{n['title']}*\n\n"
            f"{clean_html(n['summary'])}\n\n"
            f"🔗 [Читать полностью]({n['link']})\n\n"
            f"_Тема: {topic_name}_")
    try:
        bot.send_message(CHANNEL_ID, text,
                         parse_mode='Markdown',
                         disable_web_page_preview=False)
        bot.send_message(message.chat.id,
                         f'✅ Опубликовано в канал.\n\n_{n["title"]}_',
                         parse_mode='Markdown')
        log.info(f'/news → опубликовано: {n["title"][:60]}')
    except Exception as ex:
        bot.send_message(message.chat.id, f'❌ Ошибка публикации: {ex}')
        log.error(f'cmd_news send: {ex}')


# ============================================================
#  МЕНЮ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == 'menu')
def cb_menu(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return
    safe_edit(call, '🛠 *Админ-консоль*\n\nВыбери действие:', main_menu())


# ============================================================
#  СПИСОК РАСПИСАНИЙ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == 'list')
def cb_list(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    rows = get_all_schedules()
    kb = types.InlineKeyboardMarkup(row_width=1)

    if not rows:
        text = '📭 Пока нет ни одного расписания.'
    else:
        text = '📋 *Текущие расписания:*\n\n'
        for sid, t, topic, cnt in rows:
            topic_name = TOPICS.get(topic, {}).get('name', topic)
            cnt_label = f'{cnt}' if cnt > 0 else '⏸ пауза'
            text += f'#{sid}: ⏰ {t} | {topic_name} | 📨 {cnt_label}\n'
            kb.add(types.InlineKeyboardButton(
                f'✏️ #{sid} ({t}, {topic_name}, {cnt_label})',
                callback_data=f'edit_{sid}'
            ))

    kb.add(types.InlineKeyboardButton('⬅️ Назад', callback_data='menu'))
    safe_edit(call, text, kb)


# ============================================================
#  ДОБАВЛЕНИЕ — ШАГ 1: ВЫБОР ТЕМЫ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == 'add')
def cb_add(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    kb = topics_kb('newtopic')
    kb.add(types.InlineKeyboardButton('⬅️ Назад', callback_data='menu'))
    safe_edit(call, '➕ *Добавление расписания*\n\nШаг 1/3. Выбери *тему*:', kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith('newtopic_'))
def cb_newtopic(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    topic = call.data.split('_', 1)[1]
    if topic not in TOPICS:
        bot.answer_callback_query(call.id, 'Неизвестная тема', show_alert=True)
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    for t in QUICK_TIMES:
        kb.add(types.InlineKeyboardButton(t, callback_data=f'newtime_{topic}_{t}'))
    kb.add(types.InlineKeyboardButton('✏️ Своё время',
                                      callback_data=f'newtimecustom_{topic}'))
    kb.add(types.InlineKeyboardButton('⬅️ Отмена', callback_data='menu'))

    safe_edit(
        call,
        f'➕ *Добавление*\n\n'
        f'Тема: {TOPICS[topic]["name"]}\n'
        f'Шаг 2/3. Выбери *время* публикации:',
        kb
    )


# ============================================================
#  ДОБАВЛЕНИЕ — ШАГ 2 → 3
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith('newtime_'))
def cb_newtime(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    _, topic, time_str = call.data.split('_', 2)
    show_count_step(call, topic, time_str)


@bot.callback_query_handler(func=lambda c: c.data.startswith('newtimecustom_'))
def cb_newtimecustom(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    topic = call.data.split('_', 1)[1]
    msg = bot.send_message(
        call.message.chat.id,
        '✏️ Введи *своё время* в формате ЧЧ:ММ (например, `07:30`):',
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, save_custom_time, topic)


def save_custom_time(message, topic):
    if not is_admin(message):
        return
    t = message.text.strip()
    if not re.match(r'^\d{2}:\d{2}$', t):
        bot.reply_to(message, '❌ Неверный формат. Нужно ЧЧ:ММ (например, 07:30)')
        return

    kb = types.InlineKeyboardMarkup(row_width=3)
    for c in QUICK_COUNTS:
        label = '⏸ 0 (пауза)' if c == 0 else str(c)
        kb.add(types.InlineKeyboardButton(
            label, callback_data=f'newcount_{topic}_{t}_{c}'
        ))
    kb.add(types.InlineKeyboardButton(
        '✏️ Своё число', callback_data=f'newcountcustom_{topic}_{t}'
    ))
    kb.add(types.InlineKeyboardButton('⬅️ Отмена', callback_data='menu'))

    bot.send_message(
        message.chat.id,
        f'➕ *Добавление*\n\n'
        f'Тема: {TOPICS[topic]["name"]}\n'
        f'Время: {t}\n'
        f'Шаг 3/3. Выбери *количество* новостей (0–10):',
        parse_mode='Markdown',
        reply_markup=kb
    )


def show_count_step(call, topic, time_str):
    kb = types.InlineKeyboardMarkup(row_width=3)
    for c in QUICK_COUNTS:
        label = '⏸ 0 (пауза)' if c == 0 else str(c)
        kb.add(types.InlineKeyboardButton(
            label, callback_data=f'newcount_{topic}_{time_str}_{c}'
        ))
    kb.add(types.InlineKeyboardButton(
        '✏️ Своё число', callback_data=f'newcountcustom_{topic}_{time_str}'
    ))
    kb.add(types.InlineKeyboardButton('⬅️ Отмена', callback_data='menu'))

    safe_edit(
        call,
        f'➕ *Добавление*\n\n'
        f'Тема: {TOPICS[topic]["name"]}\n'
        f'Время: {time_str}\n'
        f'Шаг 3/3. Выбери *количество* новостей (0–10):',
        kb
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith('newcount_'))
def cb_newcount(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    parts = call.data.split('_')
    topic = parts[1]
    time_str = parts[2]
    count = int(parts[3])
    finish_add(call, topic, time_str, count)


@bot.callback_query_handler(func=lambda c: c.data.startswith('newcountcustom_'))
def cb_newcountcustom(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    _, topic, time_str = call.data.split('_', 2)
    msg = bot.send_message(call.message.chat.id, '✏️ Введи число от 0 до 10:')
    bot.register_next_step_handler(msg, save_custom_count, topic, time_str)


def save_custom_count(message, topic, time_str):
    if not is_admin(message):
        return
    try:
        cnt = int(message.text.strip())
        if not (0 <= cnt <= 10):
            raise ValueError
    except ValueError:
        bot.reply_to(message, '❌ Число от 0 до 10')
        return

    add_schedule(time_str, topic, cnt)
    reload_schedules()
    bot.send_message(
        message.chat.id,
        f'✅ *Добавлено*\n\n'
        f'Тема: {TOPICS[topic]["name"]}\n'
        f'Время: {time_str}\n'
        f'Количество: {cnt}',
        parse_mode='Markdown',
        reply_markup=main_menu()
    )


def finish_add(call, topic, time_str, count):
    add_schedule(time_str, topic, count)
    reload_schedules()
    safe_edit(
        call,
        f'✅ *Добавлено*\n\n'
        f'Тема: {TOPICS[topic]["name"]}\n'
        f'Время: {time_str}\n'
        f'Количество: {count}',
        main_menu()
    )


# ============================================================
#  ПРОЧЕЕ — РЕСТАРТ И КОНСОЛЬ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data == 'other')
def cb_other(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton('🔄 Рестарт', callback_data='restart'),
        types.InlineKeyboardButton('📟 Консоль', callback_data='console'),
    )
    kb.add(types.InlineKeyboardButton('⬅️ Назад', callback_data='menu'))
    safe_edit(call, '⚙️ *Прочее*\n\nВыбери действие:', kb)


@bot.callback_query_handler(func=lambda c: c.data == 'restart')
def cb_restart(call):
    bot.answer_callback_query(call.id, 'Перезапуск...')
    if not is_admin(call.message):
        return

    try:
        safe_edit(call, '🔄 *Перезапуск бота...*\n\nПодожди 5–10 секунд.',
                  back_button())
    except Exception:
        pass

    log.info('🔄 Получена команда рестарта от админа')

    def do_restart():
        time.sleep(2)
        try:
            log.info('♻️ Завершаю процесс и запускаю заново...')
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as ex:
            log.critical(f'RESTART FAILED: {ex}')

    threading.Thread(target=do_restart, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == 'console')
def cb_console(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    logs = get_last_logs(15)

    if len(logs) > 3500:
        logs = logs[-3500:]

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton('🔄 Обновить', callback_data='console'),
        types.InlineKeyboardButton('⬅️ Назад', callback_data='other'),
    )

    try:
        safe_edit(call, f'📟 *Последние 15 строк:*\n\n```\n{logs}\n```', kb)
    except Exception as ex:
        log.error(f'cb_console: {ex}')
        bot.send_message(call.message.chat.id,
                         f'📟 Последние 15 строк:\n\n{logs}',
                         reply_markup=kb)


# ============================================================
#  РЕДАКТИРОВАНИЕ
# ============================================================

@bot.callback_query_handler(func=lambda c: c.data.startswith('edit_'))
def cb_edit(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    sid = int(call.data.split('_')[1])
    row = get_schedule(sid)
    if not row:
        bot.answer_callback_query(call.id, 'Не найдено', show_alert=True)
        return

    _, t, topic, cnt = row
    topic_name = TOPICS.get(topic, {}).get('name', topic)
    cnt_label = f'{cnt}' if cnt > 0 else '⏸ пауза'

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(f'⏰ Время: {t}', callback_data=f'edittime_{sid}'),
        types.InlineKeyboardButton(f'📚 Тема: {topic_name}',
                                   callback_data=f'edittopic_{sid}'),
        types.InlineKeyboardButton(f'📨 Кол-во: {cnt_label}',
                                   callback_data=f'editcount_{sid}'),
        types.InlineKeyboardButton('🗑 Удалить', callback_data=f'del_{sid}'),
        types.InlineKeyboardButton('⬅️ Назад', callback_data='list'),
    )
    safe_edit(call, f'✏️ *Редактирование #{sid}*\n\nЧто изменить?', kb)


# ---------- ВРЕМЯ ----------

@bot.callback_query_handler(func=lambda c: c.data.startswith('edittime_'))
def cb_edittime(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    sid = int(call.data.split('_')[1])
    kb = types.InlineKeyboardMarkup(row_width=2)
    for t in QUICK_TIMES:
        kb.add(types.InlineKeyboardButton(t, callback_data=f'savetime_{sid}_{t}'))
    kb.add(types.InlineKeyboardButton('✏️ Своё время',
                                      callback_data=f'savetimecustom_{sid}'))
    kb.add(types.InlineKeyboardButton('⬅️ Назад', callback_data=f'edit_{sid}'))
    safe_edit(call, f'⏰ Выбери новое время для #{sid}:', kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith('savetime_'))
def cb_savetime(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    _, sid, t = call.data.split('_', 2)
    update_schedule(int(sid), time_str=t)
    reload_schedules()
    safe_edit(call, f'✅ Время обновлено: *{t}*', back_button())


@bot.callback_query_handler(func=lambda c: c.data.startswith('savetimecustom_'))
def cb_savetimecustom(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    sid = int(call.data.split('_')[1])
    msg = bot.send_message(call.message.chat.id,
                           '✏️ Введи время в формате ЧЧ:ММ (например, `07:30`):',
                           parse_mode='Markdown')
    bot.register_next_step_handler(msg, save_time_input, sid)


def save_time_input(message, sid):
    if not is_admin(message):
        return
    t = message.text.strip()
    if not re.match(r'^\d{2}:\d{2}$', t):
        bot.reply_to(message, '❌ Неверный формат. Нужно ЧЧ:ММ')
        return
    update_schedule(sid, time_str=t)
    reload_schedules()
    bot.send_message(message.chat.id, f'✅ Время обновлено: *{t}*',
                     parse_mode='Markdown', reply_markup=main_menu())


# ---------- КОЛИЧЕСТВО ----------

@bot.callback_query_handler(func=lambda c: c.data.startswith('editcount_'))
def cb_editcount(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    sid = int(call.data.split('_')[1])
    kb = types.InlineKeyboardMarkup(row_width=3)
    for c in QUICK_COUNTS:
        label = '⏸ 0 (пауза)' if c == 0 else str(c)
        kb.add(types.InlineKeyboardButton(label,
                                          callback_data=f'savecount_{sid}_{c}'))
    kb.add(types.InlineKeyboardButton('✏️ Своё число',
                                      callback_data=f'savecountcustom_{sid}'))
    kb.add(types.InlineKeyboardButton('⬅️ Назад', callback_data=f'edit_{sid}'))
    safe_edit(call, f'📨 Выбери новое количество для #{sid} (0–10):', kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith('savecount_'))
def cb_savecount(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    _, sid, cnt = call.data.split('_')
    update_schedule(int(sid), count=int(cnt))
    reload_schedules()
    label = 'пауза ⏸' if int(cnt) == 0 else cnt
    safe_edit(call, f'✅ Количество обновлено: *{label}*', back_button())


@bot.callback_query_handler(func=lambda c: c.data.startswith('savecountcustom_'))
def cb_savecountcustom(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    sid = int(call.data.split('_')[1])
    msg = bot.send_message(call.message.chat.id, '✏️ Введи число от 0 до 10:')
    bot.register_next_step_handler(msg, save_count_input, sid)


def save_count_input(message, sid):
    if not is_admin(message):
        return
    try:
        cnt = int(message.text.strip())
        if not (0 <= cnt <= 10):
            raise ValueError
    except ValueError:
        bot.reply_to(message, '❌ Число от 0 до 10')
        return
    update_schedule(sid, count=cnt)
    reload_schedules()
    label = 'пауза ⏸' if cnt == 0 else cnt
    bot.send_message(message.chat.id, f'✅ Количество обновлено: *{label}*',
                     parse_mode='Markdown', reply_markup=main_menu())


# ---------- ТЕМА ----------

@bot.callback_query_handler(func=lambda c: c.data.startswith('edittopic_'))
def cb_edittopic(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    sid = int(call.data.split('_')[1])
    kb = topics_kb(f'savetopic_{sid}')
    kb.add(types.InlineKeyboardButton('⬅️ Назад', callback_data=f'edit_{sid}'))
    safe_edit(call, f'📚 Выбери новую тему для #{sid}:', kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith('savetopic_'))
def cb_savetopic(call):
    bot.answer_callback_query(call.id)
    if not is_admin(call.message):
        return

    parts = call.data.split('_')
    sid = int(parts[1])
    topic = parts[2]
    if topic not in TOPICS:
        bot.answer_callback_query(call.id, 'Неизвестная тема', show_alert=True)
        return
    update_schedule(sid, topic=topic)
    reload_schedules()
    safe_edit(call, f'✅ Тема обновлена: {TOPICS[topic]["name"]}', back_button())


# ---------- УДАЛЕНИЕ ----------

@bot.callback_query_handler(func=lambda c: c.data.startswith('del_'))
def cb_del(call):
    bot.answer_callback_query(call.id, 'Удалено')
    if not is_admin(call.message):
        return

    sid = int(call.data.split('_')[1])
    delete_schedule(sid)
    reload_schedules()
    cb_list(call)


# ============================================================
#  ЗАПУСК
# ============================================================

if __name__ == '__main__':
    try:
        init_db()
        reload_schedules()

        threading.Thread(target=scheduler_loop, daemon=True).start()
        threading.Thread(target=cleanup_loop, daemon=True).start()

        log.info('🚀 Бот запущен...')
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    except Exception as ex:
        log.critical(f'FATAL: {ex}')