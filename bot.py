import asyncio
import json
import os
import logging
import re
import datetime
import asyncpg
import ssl
import certifi
import redis
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ChatPermissions, ChatMemberUpdated
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsSearch, Channel, Chat
from telethon.errors import FloodWaitError
from collections import deque
from dataclasses import dataclass
from typing import Optional

# Налаштування логування
class DummyLogger:
    def info(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass

logger = DummyLogger()

# Завантаження змінних з .env
load_dotenv()
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(admin_id) for admin_id in os.getenv('ADMIN_IDS', '').split(',') if admin_id.strip()]
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
PHONE_NUMBER = os.getenv('PHONE_NUMBER', '')
TWO_FACTOR_PASSWORD = os.getenv('TWO_FACTOR_PASSWORD', '')
SESSION_PATH = os.getenv('SESSION_PATH', 'bot_session')
AUDIO_PATH = os.getenv('AUDIO_PATH', 'QuantRP - ПРОЩАВАЙ.mp3')
ALLOWED_USER_IDS = [int(uid) for uid in os.getenv('ALLOWED_USER_IDS', '').split(',') if uid]
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'quantRPmoderator_db')
DB_USER = os.getenv('DB_USER', 'neondb_owner')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_SSLMODE = os.getenv('DB_SSLMODE', 'require')
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD')

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD, decode_responses=True, ssl=True)

# Ініціалізація Telethon клієнта
telethon_client = TelegramClient(SESSION_PATH, API_ID, API_HASH) if API_ID and API_HASH and PHONE_NUMBER else None

@dataclass
class ModerationTask:
    task_type: str  # 'ban', 'kick', 'mute', 'warn'
    user_id: int
    username: Optional[str]
    reason: str
    chat_id: int
    moderator_id: int
    duration_minutes: Optional[int] = None

# Ініціалізація бази даних PostgreSQL
async def init_db():
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED

        conn = await asyncpg.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS moderators (
                user_id BIGINT PRIMARY KEY,
                username TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                user_id BIGINT,
                chat_id BIGINT,
                warn_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bans (
                user_id BIGINT,
                chat_id BIGINT,
                reason TEXT,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS punishments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                chat_id BIGINT,
                punishment_type TEXT,
                reason TEXT,
                timestamp TIMESTAMP,
                duration_minutes INTEGER,
                moderator_id BIGINT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id BIGINT PRIMARY KEY,
                filter_enabled BOOLEAN DEFAULT TRUE
            )
        ''')
        logger.info("База даних ініціалізована успішно.")
    except Exception as e:
        logger.error(f"Помилка ініціалізації бази даних: {e}")
        raise
    finally:
        if 'conn' in locals():
            await conn.close()

# Завантаження модераторів із бази даних
async def load_moderators():
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        rows = await conn.fetch('SELECT user_id FROM moderators')
        moderators = {row['user_id'] for row in rows}
        return moderators
    except Exception as e:
        logger.error(f"Помилка завантаження модераторів: {e}")
        return set()
    finally:
        if 'conn' in locals():
            await conn.close()

# Додавання модератора до бази даних
async def add_moderator_to_db(user_id: int, username: str = None):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute(
            'INSERT INTO moderators (user_id, username) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING',
            user_id, username
        )
        logger.info(f"Додано модератора до бази: user_id={user_id}, username={username}")
    except Exception as e:
        logger.error(f"Помилка додавання модератора до бази: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

# Видалення модератора з бази даних
async def remove_moderator_from_db(user_id: int):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute('DELETE FROM moderators WHERE user_id = $1', user_id)
        logger.info(f"Видалено модератора з бази: user_id={user_id}")
    except Exception as e:
        logger.error(f"Помилка видалення модератора з бази: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

# Перевірка, чи є користувач модератором
async def is_moderator(user_id: int) -> bool:
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        result = await conn.fetchval('SELECT 1 FROM moderators WHERE user_id = $1', user_id)
        return bool(result)
    except Exception as e:
        logger.error(f"Помилка перевірки модератора: {e}")
        return False
    finally:
        if 'conn' in locals():
            await conn.close()

# Отримання username модератора
async def get_moderator_username(user_id: int) -> str | None:
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        result = await conn.fetchval('SELECT username FROM moderators WHERE user_id = $1', user_id)
        return result
    except Exception as e:
        logger.error(f"Помилка отримання username модератора: {e}")
        return None
    finally:
        if 'conn' in locals():
            await conn.close()

async def upsert_chat_settings(chat_id: int, chat_title: str = None, filter_enabled: bool = True):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        if chat_title:  # оновлюємо тільки якщо є назва
            await conn.execute(
                '''
                INSERT INTO chat_settings (chat_id, filter_enabled, chat_title)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id) DO UPDATE
                    SET chat_title = EXCLUDED.chat_title,
                        filter_enabled = EXCLUDED.filter_enabled
                ''',
                chat_id, filter_enabled, chat_title
            )
        else:  # не оновлюємо chat_title, якщо None
            await conn.execute(
                '''
                INSERT INTO chat_settings (chat_id, filter_enabled)
                VALUES ($1, $2)
                ON CONFLICT (chat_id) DO UPDATE
                    SET filter_enabled = EXCLUDED.filter_enabled
                ''',
                chat_id, filter_enabled
            )
    except Exception as e:
        logger.error(f"Помилка запису chat_settings: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()


# Функція для отримання всіх груп, де є бот
async def get_bot_chats():
    bot_chats = []
    if not telethon_client:
        logger.error("Telethon клієнт не ініціалізований. Перевірте API_ID, API_HASH, PHONE_NUMBER.")
        return bot_chats

    try:
        async with telethon_client:
            if not telethon_client.is_connected():
                logger.error("Telethon клієнт не підключений. Спроба підключення...")
                try:
                    await telethon_client.connect()
                    logger.info("Telethon клієнт успішно підключений")
                except Exception as e:
                    logger.error(f"Не вдалося підключити Telethon клієнт: {e}")
                    return bot_chats

            logger.debug("Починаємо ітерацію діалогів...")
            dialog_count = 0
            bot_id = (await bot.get_me()).id
            logger.debug(f"ID бота: {bot_id}")

            async for dialog in telethon_client.iter_dialogs():
                dialog_count += 1
                dialog_id = getattr(dialog.entity, 'id', None)
                dialog_title = getattr(dialog.entity, 'title', 'N/A')
                dialog_type = type(dialog.entity).__name__
                logger.debug(f"Діалог #{dialog_count}: ID={dialog_id}, Title={dialog_title}, Type={dialog_type}")

                # Перевіряємо, чи це група або канал
                if hasattr(dialog.entity, 'id'):
                    # Правильна конвертація ID з Telethon в aiogram формат
                    if isinstance(dialog.entity, Channel):
                        # Для каналів та супергруп
                        chat_id = int(-1000000000000 - dialog.entity.id)
                    elif isinstance(dialog.entity, Chat):
                        # Для звичайних груп
                        chat_id = int(-dialog.entity.id)
                    else:
                        # Приватні чати - пропускаємо
                        continue

                    logger.debug(f"Обробка чату: aiogram_ID={chat_id}, telethon_ID={dialog.entity.id}")

                    try:
                        chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=bot_id)
                        if chat_member.status in ["administrator", "creator"]:
                            bot_chats.append(chat_id)
                            logger.info(f"Додано чат до списку: ID={chat_id}, Title={dialog_title}")
                        else:
                            logger.debug(f"Бот не є адміністратором у чаті {chat_id}: Status={chat_member.status}")

                    except TelegramBadRequest as e:
                        if "chat not found" in str(e).lower():
                            logger.debug(f"Чат {chat_id} не знайдено (можливо, бот не є учасником)")
                        else:
                            logger.error(f"Помилка перевірки прав бота у чаті {chat_id}: {e}")
                    except Exception as e:
                        logger.error(f"Невідома помилка при перевірці чату {chat_id}: {e}")

            logger.info(f"Завершено ітерацію. Оброблено {dialog_count} діалогів.")

    except FloodWaitError as e:
        logger.warning(f"FloodWaitError: Потрібно зачекати {e.seconds} секунд")
        await asyncio.sleep(e.seconds)
        return await get_bot_chats()  # Повторна спроба після затримки
    except Exception as e:
        logger.error(f"Помилка при отриманні чатів бота: {e}")

    logger.info(f"Знайдено {len(bot_chats)} чатів, де бот є адміністратором: {bot_chats}")
    return bot_chats

# Функція для перевірки, чи є користувач у чаті
async def is_user_in_chat(chat_id: int, user_id: int) -> bool:
    try:
        async with telethon_client:
            chat = await telethon_client.get_entity(chat_id)
            async for participant in telethon_client.iter_participants(chat):
                if participant.id == user_id:
                    return True
        return False
    except Exception as e:
        logger.error(f"Помилка при перевірці присутності користувача {user_id} у чаті {chat_id}: {e}")
        return False

# Додавання попередження
async def add_warning(user_id: int, chat_id: int) -> int:
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        warn_count = await conn.fetchval('''
            INSERT INTO warnings (user_id, chat_id, warn_count)
            VALUES ($1, $2, 1)
            ON CONFLICT (user_id, chat_id)
            DO UPDATE SET warn_count = warnings.warn_count + 1
            RETURNING warn_count
        ''', user_id, chat_id)
        logger.info(f"Додано попередження: user_id={user_id}, chat_id={chat_id}, warn_count={warn_count}")
        return warn_count
    except Exception as e:
        logger.error(f"Помилка додавання попередження: {e}")
        return 0
    finally:
        if 'conn' in locals():
            await conn.close()

# Зняття попередження
async def remove_warning(user_id: int, chat_id: int) -> int:
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        warn_count = await conn.fetchval('''
            UPDATE warnings
            SET warn_count = warn_count - 1
            WHERE user_id = $1 AND chat_id = $2 AND warn_count > 0
            RETURNING warn_count
        ''', user_id, chat_id)
        if warn_count is None:
            return 0
        if warn_count == 0:
            await conn.execute('DELETE FROM warnings WHERE user_id = $1 AND chat_id = $2', user_id, chat_id)
        logger.info(f"Знято попередження: user_id={user_id}, chat_id={chat_id}, warn_count={warn_count}")
        return warn_count
    except Exception as e:
        logger.error(f"Помилка зняття попередження: {e}")
        return 0
    finally:
        if 'conn' in locals():
            await conn.close()

# Додавання бана
async def add_ban(user_id: int, chat_id: int, reason: str):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute(
            'INSERT INTO bans (user_id, chat_id, reason) VALUES ($1, $2, $3) ON CONFLICT (user_id, chat_id) DO UPDATE SET reason = $3',
            user_id, chat_id, reason
        )
        logger.info(f"Додано бан: user_id={user_id}, chat_id={chat_id}, reason={reason}")
    except Exception as e:
        logger.error(f"Помилка додавання бана: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

# Зняття бана
async def remove_ban(user_id: int, chat_id: int):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute('DELETE FROM bans WHERE user_id = $1 AND chat_id = $2', user_id, chat_id)
        logger.info(f"Знято бан: user_id={user_id}, chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Помилка зняття бана: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

async def remove_mute(user_id: int, chat_id: int):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute(
            "DELETE FROM punishments WHERE user_id = $1 AND chat_id = $2 AND punishment_type = 'mute'",
            user_id, chat_id
        )
        logger.info(f"Знято мут: user_id={user_id}, chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Помилка зняття мута: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

# Отримання кількості попереджень
async def get_warning_count(user_id: int, chat_id: int) -> int:
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        result = await conn.fetchval(
            'SELECT warn_count FROM warnings WHERE user_id = $1 AND chat_id = $2', user_id, chat_id
        )
        return result if result is not None else 0
    except Exception as e:
        logger.error(f"Помилка отримання попереджень: {e}")
        return 0
    finally:
        if 'conn' in locals():
            await conn.close()

# Логування покарань
async def log_punishment(user_id: int, chat_id: int, punishment_type: str, reason: str,
                         duration_minutes: int | None = None, moderator_id: int | None = None):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute('''
            INSERT INTO punishments (user_id, chat_id, punishment_type, reason, timestamp, duration_minutes, moderator_id)
            VALUES ($1, $2, $3, $4, NOW(), $5, $6)
        ''', user_id, chat_id, punishment_type, reason, duration_minutes, moderator_id)
        logger.info(
            f"Залоговано покарання: user_id={user_id}, chat_id={chat_id}, type={punishment_type}, reason={reason}, duration={duration_minutes}, moderator_id={moderator_id}")
    except Exception as e:
        logger.error(
            f"Помилка логування покарання для user_id={user_id}, chat_id={chat_id}, type={punishment_type}: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

# Отримання історії покарань
async def get_punishments(user_id: int, chat_id: int) -> list:
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        rows = await conn.fetch('''
            SELECT punishment_type, reason, timestamp, duration_minutes, moderator_id
            FROM punishments
            WHERE user_id = $1 AND chat_id = $2
            ORDER BY timestamp DESC
        ''', user_id, chat_id)
        logger.info(f"Отримано історію покарань для user_id={user_id}, chat_id={chat_id}: {len(rows)} записів")
        return [
            {
                "type": row['punishment_type'],
                "reason": row['reason'],
                "timestamp": row['timestamp'].strftime('%Y-%m-%d %H:%M') if row['timestamp'] is not None else "невідомо",
                "duration_minutes": row['duration_minutes'],
                "moderator_id": row['moderator_id']
            } for row in rows
        ]
    except Exception as e:
        logger.error(f"Помилка отримання історії покарань: {e}")
        return []
    finally:
        if 'conn' in locals():
            await conn.close()

# Отримання статусу фільтра
async def get_filter_status(chat_id: int) -> bool:
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        result = await conn.fetchval('SELECT filter_enabled FROM chat_settings WHERE chat_id = $1', chat_id)
        return result if result is not None else True
    except Exception as e:
        logger.error(f"Помилка отримання статусу фільтра для chat_id={chat_id}: {e}")
        return True
    finally:
        if 'conn' in locals():
            await conn.close()

# Встановлення статусу фільтра
async def set_filter_status(chat_id: int, enabled: bool):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute(
            'INSERT INTO chat_settings (chat_id, filter_enabled) VALUES ($1, $2) ON CONFLICT (chat_id) DO UPDATE SET filter_enabled = $2',
            chat_id, enabled
        )
        logger.info(f"Оновлено статус фільтра для chat_id={chat_id}: {enabled}")
    except Exception as e:
        logger.error(f"Помилка збереження статусу фільтра для chat_id={chat_id}: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

# Зчитування заборонених слів із файлу
def load_forbidden_words(file_path='forbidden_words.txt'):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return {word.strip().lower() for word in f.readlines() if word.strip()}
    except FileNotFoundError:
        logger.warning(f"Файл {file_path} не знайдено. Використовується порожній список заборонених слів.")
        return set()
    except Exception as e:
        logger.error(f"Помилка зчитування заборонених слів: {e}")
        return set()

# Ініціалізація бота
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Список заборонених слів
FORBIDDEN_WORDS = load_forbidden_words()
WELCOME_MESSAGE = True

# Функція для екранування спеціальних символів у MarkdownV2
def escape_markdown_v2(text: str) -> str:
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def escape_markdown_v2_rules(text: str) -> str:
    special_chars = ['_', '[', ']', '(', ')', '~', '`', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def escape_markdown_v2_help(text: str) -> str:
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '#', '+', '-', '=', '|', '{', '}', '.', '!', '>']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def add_task_to_queue(task: ModerationTask):
    redis_client.rpush('moderation_queue', json.dumps(task.__dict__))

def get_queue_length():
    return redis_client.llen('moderation_queue')

# Функція для створення згадки користувача
async def get_user_mention(user_id: int, chat_id: int) -> str | None:
    try:
        chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        user = chat_member.user
        if user.username:
            escaped_username = escape_markdown_v2(user.username).replace('_', '\\_')
            mention = f"@{escaped_username}"
            logger.info(f"Створено згадку: {mention} для user_id={user_id}, username={user.username}")
            return mention
        else:
            escaped_name = escape_markdown_v2(user.first_name or f"User {user_id}")
            mention = f"[{escaped_name}]"
            logger.info(f"Username відсутній, використовується ім'я: {mention} для user_id={user_id}")
            return mention
    except TelegramBadRequest as e:
        logger.warning(f"Помилка при отриманні користувача {user_id} у чаті {chat_id}: {e}")
        return f"ID\\:{user_id}"

# Функція для безпечного видалення повідомлення
async def safe_delete_message(message: types.Message):
    try:
        await message.delete()
        logger.info(f"Видалено повідомлення: message_id={message.message_id}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.warning(f"Не вдалося видалити повідомлення {message.message_id}: {e}")

# Функція для отримання user_id, username і причини
async def get_user_data(message: types.Message, args: list) -> tuple[int, str | None, str] | None:
    chat_id = message.chat.id
    reason = None
    if message.reply_to_message:
        if not args:
            logger.error("Причина не вказана для команди через reply")
            return None
        user_id = message.reply_to_message.from_user.id
        username = message.reply_to_message.from_user.username
        username = username.lstrip('@') if username else None
        reason = ' '.join(args)
        logger.info(f"Отримано user_id через reply: user_id={user_id}, username={username}, reason={reason}")
        return user_id, username, reason
    if args and re.match(r'^\d+$', args[0]):
        if len(args) < 2:
            logger.error("Причина не вказана для команди з user_id")
            return None
        try:
            user_id = int(args[0])
            reason = ' '.join(args[1:])
            try:
                chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                username = chat_member.user.username.lstrip('@') if chat_member.user.username else None
                logger.info(f"Отримано user_id через аргумент: user_id={user_id}, username={username}, reason={reason}")
                return user_id, username, reason
            except TelegramBadRequest as e:
                logger.error(f"Помилка при перевірці user_id {user_id} у чаті {chat_id}: {e}")
                return None
        except ValueError:
            logger.error(f"Некоректний user_id у аргументах: {args[0]}")
            return None
    logger.error("Невірний формат команди: не вказано user_id або reply")
    return None

# Перевірка прав модератора або адміністратора
async def has_moderator_privileges(user_id: int) -> bool:
    return user_id in ADMIN_IDS or await is_moderator(user_id)

# Перевірка, чи має користувач доступ до /get_users
def is_allowed_user(user_id: int) -> bool:
    return user_id in ALLOWED_USER_IDS

# Функція для отримання всіх учасників чату
async def get_all_participants(chat_id: int) -> list:
    members = []
    try:
        async with telethon_client:
            chat = await telethon_client.get_entity(chat_id)
            if not isinstance(chat, (Channel, Chat)):
                logger.error(f"Chat {chat_id} не є групою або каналом")
                return members
            offset = 0
            limit = 200
            while True:
                try:
                    participants = await telethon_client(GetParticipantsRequest(
                        channel=chat,
                        filter=ChannelParticipantsSearch(''),
                        offset=offset,
                        limit=limit,
                        hash=0
                    ))
                    if not participants.users:
                        break
                    for user in participants.users:
                        name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")
                        username = f"@{user.username}" if user.username else ""
                        members.append(f"{name.strip()} {username}".strip())
                    offset += len(participants.users)
                except FloodWaitError as e:
                    logger.warning(f"Обмеження Telegram API, очікування {e.seconds} секунд")
                    await asyncio.sleep(e.seconds)
    except Exception as e:
        logger.error(f"Помилка при отриманні учасників для чату {chat_id}: {str(e)}")
    return members

# Обробники команд
@dp.message(Command('welcome'))
async def toggle_welcome(message: types.Message):
    global WELCOME_MESSAGE
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    WELCOME_MESSAGE = not WELCOME_MESSAGE
    status = "✅ увімкнено" if WELCOME_MESSAGE else "❌ вимкнено"
    reply = await message.reply(f"Привітання нових учасників {status}")
    await safe_delete_message(message)
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"Змінено статус привітань: {status}")

@dp.message(Command('test_admin'))
async def test_admin(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        await message.reply("Ви не маєте прав для виконання цієї команди.")
        return
    try:
        bot_id = (await bot.get_me()).id
        chat_id = message.chat.id
        chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=bot_id)
        await message.reply(f"Статус бота в чаті {chat_id}: {chat_member.status}")
    except TelegramBadRequest as e:
        await message.reply(f"Помилка перевірки статусу: {e}")

@dp.message(Command('filter'))
async def toggle_filter(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    chat_id = message.chat.id
    current_status = await get_filter_status(chat_id)
    new_status = not current_status
    await set_filter_status(chat_id, new_status)

    status = "✅ увімкнено" if new_status else "❌ вимкнено"
    reply = await message.reply(f"Фільтрація заборонених слів {status}")
    await safe_delete_message(message)
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"Змінено статус фільтрації заборонених слів для chat_id={chat_id}: {status}")

async def ensure_all_chats_in_settings():
    bot_chats = await get_bot_chats()
    for chat_id in bot_chats:
        await upsert_chat_settings(chat_id, filter_enabled=True)

@dp.message(Command('addmoder'))
async def add_moderator(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Будь ласка, вкажіть user_id у форматі /addmoder 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    if await is_moderator(user_id):
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        reply = await message.reply(f"Користувач {escape_markdown_v2(mention)} уже є модератором.",
                                    parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    await add_moderator_to_db(user_id, username)
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
    text = escape_markdown_v2(f"Користувач {mention} доданий до списку модераторів.")
    reply = await message.reply(text, parse_mode="MarkdownV2")
    await safe_delete_message(message)
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"Додано модератора: user_id={user_id}, username={username}, chat_id={message.chat.id}")

@dp.message(Command('removemoder'))
async def remove_moderator(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Будь ласка, вкажіть user_id у форматі /removemoder 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    if not await is_moderator(user_id):
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        reply = await message.reply(f"Користувач {escape_markdown_v2(mention)} не є модератором.",
                                    parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    await remove_moderator_from_db(user_id)
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
    text = escape_markdown_v2(f"Користувач {mention} видалений зі списку модераторів.")
    reply = await message.reply(text, parse_mode="MarkdownV2")
    await safe_delete_message(message)
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"Видалено модератора: user_id={user_id}, username={username}, chat_id={message.chat.id}")

@dp.message(Command('kick'))
async def cmd_kick(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Вкажіть user_id і причину у форматі /kick 123456789 причина або відповідайте на повідомлення."
        )
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    user_id, username, reason = user_data
    task = ModerationTask(
        task_type="kick",
        user_id=user_id,
        username=username,
        reason=reason,
        chat_id=message.chat.id,
        moderator_id=message.from_user.id
    )

    add_task_to_queue(task)
    queue_position = get_queue_length()
    reply = await message.reply(f"Завдання на кік додано до черги. Позиція: {queue_position}")
    await safe_delete_message(message)
    await asyncio.sleep(10)
    await safe_delete_message(reply)



@dp.message(Command('ban'))
async def cmd_ban(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Вкажіть user_id і причину у форматі /ban 123456789 причина або відповідайте на повідомлення."
        )
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    user_id, username, reason = user_data
    task = ModerationTask(
        task_type="ban",
        user_id=user_id,
        username=username,
        reason=reason,
        chat_id=message.chat.id,
        moderator_id=message.from_user.id
    )
    add_task_to_queue(task)
    queue_position = get_queue_length()
    reply = await message.reply(f"Завдання на бан додано до черги. Позиція: {queue_position}")
    await safe_delete_message(message)
    await asyncio.sleep(10)
    await safe_delete_message(reply)

@dp.message(Command('warn'))
async def cmd_warn(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Вкажіть user_id і причину у форматі /warn 123456789 причина або відповідайте на повідомлення."
        )
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    user_id, username, reason = user_data
    task = ModerationTask(
        task_type="warn",
        user_id=user_id,
        username=username,
        reason=reason,
        chat_id=message.chat.id,
        moderator_id=message.from_user.id
    )
    add_task_to_queue(task)
    await safe_delete_message(message)
    await asyncio.sleep(10)

@dp.message(Command('mute'))
async def cmd_mute(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    args = message.text.split()[1:]
    minutes = None
    reason = None
    if len(args) >= 3 and re.match(r'^\d+$', args[0]) and args[1].isdigit():
        user_id = int(args[0])
        minutes = int(args[1])
        reason = ' '.join(args[2:])
    elif message.reply_to_message and len(args) >= 2 and args[0].isdigit():
        user_id = message.reply_to_message.from_user.id
        minutes = int(args[0])
        reason = ' '.join(args[1:])
    else:
        reply = await message.reply(
            "Вкажіть user_id, час у хвилинах і причину у форматі /mute 123456789 60 причина або відповідайте на повідомлення."
        )
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    user_data = await get_user_data(message, args if user_id else args[1:])
    if not user_data:
        reply = await message.reply("Вкажіть коректний user_id, час і причину.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return
    user_id_from_data, username, _ = user_data
    user_id = user_id_from_data if user_id_from_data else user_id
    task = ModerationTask(
        task_type="mute",
        user_id=user_id,
        username=username,
        reason=reason,
        chat_id=message.chat.id,
        moderator_id=message.from_user.id,
        duration_minutes=minutes
    )
    add_task_to_queue(task)
    queue_position = get_queue_length()
    reply = await message.reply(f"Завдання на мут додано до черги. Позиція: {queue_position}")
    await safe_delete_message(message)
    await asyncio.sleep(10)
    await safe_delete_message(reply)


@dp.message(Command('unmute'))
async def unmute_user(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Будь ласка, вкажіть user_id у форматі /unmute 123456789 Причину або відповідайте на повідомлення користувача і вкажіть причину.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    try:
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True
            )
        )
        mention = f"@{username}" if username else f"ID\\:{user_id}"
        text = escape_markdown_v2(f"Знято мут із користувача {mention}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
    except TelegramBadRequest as e:
        reply = await message.reply(f"Не вдалося зняти мут: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('unwarn'))
async def unwarn_user(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Будь ласка, вкажіть user_id у форматі /unwarn 123456789 Причину або відповідайте на повідомлення користувача і вкажіть причину")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    warn_count = await remove_warning(user_id, message.chat.id)
    mention = f"@{username}" if username else f"ID\\:{user_id}"
    if warn_count >= 0:
        text = escape_markdown_v2(f"Знято попередження з користувача {mention}. Залишилось {warn_count}/3.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
    else:
        text = escape_markdown_v2(f"У користувача {mention} немає попереджень.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('unban'))
async def unban_user(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply(
            "Будь ласка, вкажіть user_id у форматі /unban 123456789 Причину або відповідайте на повідомлення користувача і вкажіть причину")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=user_id)
        await remove_ban(user_id, message.chat.id)
        mention = f"@{username}" if username else f"ID\\:{user_id}"
        text = escape_markdown_v2(f"Знято бан із користувача {mention}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
    except TelegramBadRequest as e:
        reply = await message.reply(f"Не вдалося зняти бан: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)


@dp.message(Command('info'))
async def cmd_info(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].startswith('@'):
        reply = await message.reply("Вкажіть username у форматі /info @username.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    username = args[1].lstrip('@')

    # Створюємо завдання та додаємо до черги
    task = ModerationTask(
        task_type="info",
        user_id=0,  # визначає worker
        username=username,
        reason="",
        chat_id=message.chat.id,
        moderator_id=message.from_user.id
    )
    add_task_to_queue(task)
    queue_position = get_queue_length()
    reply = await message.reply(f"Запит info про @{username} додано до черги. Позиція: {queue_position}")
    await safe_delete_message(message)
    await asyncio.sleep(10)
    await safe_delete_message(reply)

@dp.message(Command('ad'))
async def make_announcement(message: types.Message):
    if not await has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        reply = await message.reply("Будь ласка, вкажіть текст оголошення у форматі /ad <текст оголошення>.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    announcement_text = args[1]
    chat_id = message.chat.id

    participants = await get_all_participants(chat_id)
    if not participants:
        reply = await message.reply("Не вдалося отримати список учасників. Перевірте налаштування Telethon.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    chunk_size = 50
    participant_chunks = [participants[i:i + chunk_size] for i in range(0, len(participants), chunk_size)]
    sent_message = None

    try:
        first_chunk = participant_chunks[0] if participant_chunks else []
        mentions = ' '.join(first_chunk)
        full_text = escape_markdown_v2(
            f"📢 Оголошення:\n{announcement_text}\n\n{mentions}" if mentions else f"📢 Оголошення:\n{announcement_text}")
        sent_message = await bot.send_message(
            chat_id=chat_id,
            text=full_text,
            parse_mode="MarkdownV2",
            disable_notification=False
        )
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent_message.message_id,
            disable_notification=False
        )

        for chunk in participant_chunks[1:]:
            mentions = ' '.join(chunk)
            if mentions:
                full_text = escape_markdown_v2(mentions)
                await bot.send_message(
                    chat_id=chat_id,
                    text=full_text,
                    parse_mode="MarkdownV2",
                    disable_notification=True
                )
                await asyncio.sleep(4)
        await safe_delete_message(message)
    except TelegramBadRequest as e:
        reply = await message.reply(f"Не вдалося надіслати або закріпити оголошення: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('get_users'))
async def get_users(message: types.Message):
    if not is_allowed_user(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    chat_id = message.chat.id
    members = await get_all_participants(chat_id)
    if not members:
        reply = await message.reply("Не вдалося отримати учасників або список порожній.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    output = "\n".join(members)
    filename = f"chat_{chat_id}_users.txt"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(output)
        await bot.send_document(
            chat_id=message.chat.id,
            document=types.FSInputFile(filename),
            caption=escape_markdown_v2("Список учасників чату"),
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        reply = await message.reply(f"Помилка: {str(e)}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
    finally:
        if os.path.exists(filename):
            os.remove(filename)

@dp.chat_member()
async def welcome_new_member(update: ChatMemberUpdated):
    user = update.new_chat_member.user
    old_status = getattr(update.old_chat_member, 'status', 'none')
    new_status = update.new_chat_member.status
    logger.info(
        f"Отримано подію chat_member: user_id={user.id}, old_status={old_status}, new_status={new_status}, chat_id={update.chat.id}")
    if (WELCOME_MESSAGE and new_status in ["member", "restricted"] and
            (update.old_chat_member is None or old_status in ["left", "kicked"])):
        try:
            mention = f"@{user.username}" if user.username else f"ID\\:{user.id}"
            chat = await bot.get_chat(update.chat.id)
            chat_username = f"@{chat.username}" if chat.username else f"ID:{update.chat.first_name}"
            text = escape_markdown_v2(f"Вітаємо, {mention}! Ласкаво просимо до {chat_username}! 😊")
            await bot.send_message(
                chat_id=update.chat.id,
                text=text,
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            pass


@dp.message(Command('rules'))
async def show_rules(message: types.Message):
    rules_text = (
        "📜 Правила чату QUANT RP\n\n"
        "1️⃣ *Загальні положення*\n"
        "🔹 Чат створений для обговорення та розвитку проекту QUANT RP.\n"
        "🔹 Дотримуйтесь культури спілкування – поважайте один одного.\n"
        "🔹 Адміністрація залишає за собою право вносити зміни в правила та застосовувати санкції за їх порушення.\n"
        "🔹 Адміністрація ніколи не вимагає у вас паспортні данні та особисту інформацію та ніколи не буде писати вам у ТЕЛЕГРАМ!\n\n"
        "2️⃣ *Заборонено*\n"
        "🚫 Образи та неадекватна поведінка – будь-які форми хамства, токсичності, дискримінації.\n"
        "🚫 Флуд, спам, реклама – масові надсилання повідомлень, реклама сторонніх сервісів або проектів без дозволу адміністрації.\n"
        "🚫 Політика та релігія – обговорення політичних чи релігійних тем, що можуть спричинити конфлікти.\n"
        "🚫 Обговорення сторонніх серверів – рекламування або залучення користувачів на інші схожі проекти.\n"
        "🚫 Використання нецензурної лексики – груба лексика, навіть частково замаскована.\n"
        "🚫 Продаж акаунтів/валюти – заборонені будь-які угоди, пов’язані з продажем облікових записів або внутрішньоігрової валюти.\n\n"
        "3️⃣ *Рекомендації щодо спілкування*\n"
        "✅ Спілкуйтесь дружньо та конструктивно.\n"
        "✅ Якщо виникають конфлікти – звертайтесь до модераторів.\n"
        "✅ Якщо у вас є пропозиції щодо розвитку проекту – подавайте їх у відповідні теми.\n\n"
        "📌 Участь у чаті означає автоматичну згоду з цими правилами.\n\n"
        "✉️ Якщо у вас є питання – звертайтесь до адміністрації.\n"
        "Приємного спілкування та гарної гри! 🎮"
    )
    text = escape_markdown_v2_rules(rules_text)
    try:
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
    except TelegramBadRequest as e:
        reply = await message.reply("Помилка при відображенні правил. Спробуйте ще раз.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('help'))
async def show_help(message: types.Message):
    is_mod = await has_moderator_privileges(message.from_user.id)
    if is_mod:
        help_text = (
            "📚 Список доступних команд для модераторів/адміністраторів:\n\n"
            "🔧 /welcome - Увімкнути/вимкнути привітання нових учасників.(Тільки для адміністраторів)\n"
            "🔧 /filter - Увімкнути/вимкнути фільтрацію заборонених слів.(Тільки для адміністраторів)\n"
            "👮 /addmoder <user_id> - Додати модератора (через ID або відповідь).\n"
            "👮 /removemoder <user_id> - Видалити модератора (через ID або відповідь).\n"
            "🚪 /kick <user_id> <причина> - Кікнути користувача (через ID або відповідь).\n"
            "⚠️ /warn <user_id> <причина> - Видати попередження користувачу.\n"
            "🚫 /ban <user_id> <причина> - Забанити користувача.\n"
            "🔇 /mute <user_id> <хвилини> <причина> - Видати мут користувачу.\n"
            "🔊 /unmute <user_id> - Зняти мут із користувача.\n"
            "✅ /unwarn <user_id> - Зняти попередження з користувача.\n"
            "🔓 /unban <user_id> - Зняти бан із користувача.\n"
            "ℹ️ /info @username - Переглянути інформацію про користувача та його покарання.\n"
            "📢 /ad <текст> - Зробити оголошення зі згадкою всіх учасників.\n"
            "📜 /rules - Переглянути правила чату.\n"
            "📋 /get_users - Отримати список учасників чату (тільки для дозволених користувачів).\n"
            "❓ /help - Показати цей список команд."
        )
    else:
        help_text = (
            "📚 Список доступних команд:\n\n"
            "📜 /rules - Переглянути правила чату.\n"
            "📋 /get_users - Отримати список учасників чату (тільки для дозволених користувачів).\n"
            "❓ /help - Показати цей список команд."
        )

    text = escape_markdown_v2_help(help_text)
    try:
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
    except TelegramBadRequest as e:
        reply = await message.reply("Помилка при відображенні команд. Спробуйте ще раз.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

async def upsert_telegram_user(user: types.User):
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        if DB_SSLMODE == 'require':
            ssl_context.check_hostname = True
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        conn = await asyncpg.connect(
            host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
            ssl=ssl_context if DB_SSLMODE == 'require' else None
        )
        await conn.execute(
            '''
            INSERT INTO telegramuser (user_id, username, first_name, last_name, last_seen)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (user_id) DO UPDATE SET
                username = $2,
                first_name = $3,
                last_name = $4,
                last_seen = $5
            ''',
            user.id,
            user.username,
            user.first_name,
            user.last_name,
            datetime.datetime.utcnow()
        )
    except Exception as e:
        logger.error(f"Помилка запису telegramuser: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

@dp.message()
async def filter_messages(message: types.Message):
    await upsert_telegram_user(message.from_user)
    chat_id = message.chat.id
    if not await get_filter_status(chat_id) or not message.text:
        return
    message_text = message.text.lower()
    for word in FORBIDDEN_WORDS:
        if word in message_text:
            try:
                mute_until = datetime.datetime.now() + datetime.timedelta(hours=24)
                await bot.restrict_chat_member(
                    chat_id=message.chat.id,
                    user_id=message.from_user.id,
                    permissions=ChatPermissions(
                        can_send_messages=False,
                        can_send_media_messages=False,
                        can_send_polls=False,
                        can_send_other_messages=False
                    ),
                    until_date=mute_until
                )
                await log_punishment(
                    message.from_user.id, message.chat.id, "mute",
                    f"Використання забороненого слова: {word}", duration_minutes=24 * 60, moderator_id=None
                )
                mention = f"@{message.from_user.username}" if message.from_user.username else f"ID\\:{message.from_user.id}"
                text = escape_markdown_v2(
                    f"Користувач {mention} отримав мут на 24 години за використання забороненого слова.")
                reply = await message.reply(text, parse_mode="MarkdownV2")
                await safe_delete_message(message)
                await asyncio.sleep(25)
                await safe_delete_message(reply)
            except TelegramBadRequest as e:
                mention = await get_user_mention(message.from_user.id,
                                                 message.chat.id) or f"User {message.from_user.id}"
                error_text = escape_markdown_v2(f"Помилка при видачі мута для {mention}: {str(e)}")
                reply = await bot.send_message(message.chat.id, error_text, parse_mode="MarkdownV2")
                await safe_delete_message(message)
                await asyncio.sleep(25)
                await safe_delete_message(reply)
            break

async def moderation_worker():
    while True:
        raw_task = redis_client.blpop('moderation_queue', timeout=10)
        if raw_task:
            value = raw_task[1]
            try:
                task = ModerationTask(**json.loads(value))
                if task.task_type == 'mute':
                    await mute_user_action(task)
                elif task.task_type == 'ban':
                    await ban_user_action(task)
                elif task.task_type == 'kick':
                    await kick_user_action(task)
                elif task.task_type == 'warn':
                    await warn_user_action(task)
                elif task.task_type == 'info':
                    await info_user_action(task)
                elif task.task_type == 'unban':
                    await unban_user_action(task)
                elif task.task_type == 'unmute':
                    await unmute_user_action(task)
                elif task.task_type == 'unwarn':
                    await unwarn_user_action(task)
            except Exception:
                pass
        else:
            await asyncio.sleep(2)

async def ban_user_action(task: ModerationTask):
    user_id = task.user_id
    username = task.username
    reason = task.reason
    chat_id = task.chat_id
    moderator_id = task.moderator_id
    mention = f"@{username}" if username else f"ID\\:{user_id}"
    AUDIO_PATH = "path/to/music.mp3"  # Вкажи правильний шлях

    # Відтворення музики перед баном
    if os.path.exists(AUDIO_PATH):
        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=types.FSInputFile(AUDIO_PATH),
                caption=escape_markdown_v2(f"Користувач {mention} отримує бан! 🎵 Причина: {reason}"),
                parse_mode="MarkdownV2"
            )
            logger.info(f"Надіслано музику перед бан для user_id={user_id} у чаті {chat_id}")
            await asyncio.sleep(25)
        except TelegramBadRequest as e:
            logger.error(f"Помилка при надсиланні музики для user_id={user_id}: {e}")
    else:
        logger.warning(f"Аудіофайл {AUDIO_PATH} не знайдено")

    # Бан у поточному чаті
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id, revoke_messages=True)
        await add_ban(user_id, chat_id, reason)
        await log_punishment(user_id, chat_id, "ban", reason, moderator_id=moderator_id)
        text = escape_markdown_v2(f"Користувач {mention} забанений у цьому чаті. Причина: {reason}.")
        reply = await bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")
        logger.info(f"Забанено користувача: user_id={user_id}, username={username}, reason={reason}, chat_id={chat_id}")

        await asyncio.sleep(25)
        await safe_delete_message(reply)

    except TelegramBadRequest as e:
        logger.error(f"Помилка при бану користувача {user_id} у чаті {chat_id}: {e}")
        reply = await bot.send_message(
            chat_id=chat_id,
            text=escape_markdown_v2(f"Не вдалося забанити користувача у цьому чаті: {e.message}"),
            parse_mode="MarkdownV2"
        )
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    # Бан у всіх інших чатах, де є бот
    bot_chats = await get_bot_chats()
    logger.info(f"Знайдено {len(bot_chats)} чатів для бану, де є бот: {bot_chats}")

    for other_chat_id in bot_chats:
        if other_chat_id == chat_id:
            continue
        if await is_user_in_chat(other_chat_id, user_id):
            try:
                await bot.ban_chat_member(chat_id=other_chat_id, user_id=user_id, revoke_messages=True)
                await add_ban(user_id, other_chat_id, f"Бан через команду в іншому чаті: {reason}")
                await log_punishment(user_id, other_chat_id, "ban", f"Бан через команду в іншому чаті: {reason}", moderator_id=moderator_id)
                logger.info(f"Забанено користувача {user_id} в чаті {other_chat_id} за причиною: {reason}")

                # Відправка повідомлення в інший чат
                chat_mention = f"ID\\:{other_chat_id}"
                try:
                    chat = await bot.get_chat(other_chat_id)
                    chat_mention = f"@{chat.username}" if chat.username else f"{chat.title}"
                except TelegramBadRequest as e:
                    logger.warning(f"Не вдалося отримати інформацію про чат {other_chat_id}: {e}")

                text = escape_markdown_v2(f"Користувач {mention} забанений у чаті {chat_mention}. Причина: {reason}.")
                await bot.send_message(chat_id=other_chat_id, text=text, parse_mode="MarkdownV2")

                await asyncio.sleep(1)
            except TelegramBadRequest as e:
                logger.error(f"Помилка при бану користувача {user_id} у чаті {other_chat_id}: {e}")
                continue

    logger.info(f"ban_user_action: user_id={user_id}, chat_id={chat_id}")

async def kick_user_action(task: ModerationTask):
    user_id = task.user_id
    username = task.username
    reason = task.reason
    chat_id = task.chat_id
    moderator_id = task.moderator_id
    mention = f"@{username}" if username else f"ID\\:{user_id}"
    AUDIO_PATH = "path/to/music.mp3"  # Вкажи шлях до музики

    # Відтворення музики перед кік
    if os.path.exists(AUDIO_PATH):
        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=types.FSInputFile(AUDIO_PATH),
                caption=escape_markdown_v2(f"Користувач {mention} отримує кік! 🎵 Причина: {reason}"),
                parse_mode="MarkdownV2"
            )
            logger.info(f"Надіслано музику перед кік для user_id={user_id} у чаті {chat_id}")
            await asyncio.sleep(25)
        except TelegramBadRequest as e:
            logger.error(f"Помилка при надсиланні музики для user_id={user_id}: {e}")
    else:
        logger.warning(f"Аудіофайл {AUDIO_PATH} не знайдено")

    # Кік із поточного чату
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id, revoke_messages=False)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        await log_punishment(user_id, chat_id, "kick", reason, moderator_id=moderator_id)
        text = escape_markdown_v2(f"Користувач {mention} кікнутий з цього чату. Причина: {reason}.")
        reply = await bot.send_message(chat_id=chat_id, text=text, parse_mode="MarkdownV2")
        logger.info(f"Кікнуто користувача: user_id={user_id}, username={username}, reason={reason}, chat_id={chat_id}")

        await asyncio.sleep(25)
        await safe_delete_message(reply)

    except TelegramBadRequest as e:
        logger.error(f"Помилка при кіку користувача {user_id} з чату {chat_id}: {e}")
        reply = await bot.send_message(chat_id=chat_id,
                                       text=escape_markdown_v2(f"Не вдалося кікнути користувача з цього чату: {e.message}"),
                                       parse_mode="MarkdownV2")
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    # Кік з усіх інших чатів, де є бот
    bot_chats = await get_bot_chats()
    logger.info(f"Знайдено {len(bot_chats)} чатів, де є бот: {bot_chats}")

    for other_chat_id in bot_chats:
        if other_chat_id == chat_id:
            continue
        if await is_user_in_chat(other_chat_id, user_id):
            try:
                await bot.ban_chat_member(chat_id=other_chat_id, user_id=user_id, revoke_messages=False)
                await bot.unban_chat_member(chat_id=other_chat_id, user_id=user_id)
                await log_punishment(user_id, other_chat_id, "kick", f"Кік через команду в іншому чаті: {reason}", moderator_id=moderator_id)
                logger.info(f"Кікнуто користувача {user_id} з чату {other_chat_id} за причиною: {reason}")

                # Відправка повідомлення в інший чат
                chat_mention = f"ID\\:{other_chat_id}"
                try:
                    chat = await bot.get_chat(other_chat_id)
                    chat_mention = f"@{chat.username}" if chat.username else f"{chat.title}"
                except TelegramBadRequest as e:
                    logger.warning(f"Не вдалося отримати інформацію про чат {other_chat_id}: {e}")

                text = escape_markdown_v2(f"Користувач {mention} кікнутий з чату {chat_mention}. Причина: {reason}.")
                await bot.send_message(chat_id=other_chat_id, text=text, parse_mode="MarkdownV2")

                await asyncio.sleep(1)

            except TelegramBadRequest as e:
                logger.error(f"Помилка при кіку користувача {user_id} з чату {other_chat_id}: {e}")
                continue

    logger.info(f"kick_user_action: user_id={user_id}, chat_id={chat_id}")

async def mute_user_action(task: ModerationTask):
    user_id = task.user_id
    username = task.username
    reason = task.reason
    chat_id = task.chat_id
    moderator_id = task.moderator_id
    duration = task.duration_minutes or 60
    mention = f"@{username}" if username else f"ID\\:{user_id}"
    mute_until = datetime.datetime.now() + datetime.timedelta(minutes=duration)

    try:
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_polls=False,
                can_send_other_messages=False
            ),
            until_date=mute_until
        )
        await log_punishment(user_id, chat_id, "mute", reason, duration_minutes=duration, moderator_id=moderator_id)
        text = escape_markdown_v2(f"Користувач {mention} отримав мут на {duration} хвилин. Причина: {reason}.")
    except TelegramBadRequest as e:
        text = escape_markdown_v2(f"Не вдалося зам'ютити користувача: {e.message}")

    reply = await bot.send_message(chat_id, text, parse_mode="MarkdownV2")
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"mute_user_action: user_id={user_id}, duration={duration}, chat_id={chat_id}")


async def warn_user_action(task: ModerationTask):
    user_id = task.user_id
    username = task.username
    reason = task.reason
    chat_id = task.chat_id
    moderator_id = task.moderator_id
    mention = f"@{username}" if username else f"ID\\:{user_id}"

    warn_count = await add_warning(user_id, chat_id)
    await log_punishment(user_id, chat_id, "warn", reason, moderator_id=moderator_id)

    if warn_count >= 3:
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id, revoke_messages=False)
            await log_punishment(user_id, chat_id, "kick", "3 попередження", moderator_id=moderator_id)
            text = escape_markdown_v2(f"Користувач {mention} отримав 3/3 попередження і кікнутий з чату. Причина: {reason}.")
        except TelegramBadRequest as e:
            text = escape_markdown_v2(f"Не вдалося кікнути користувача: {e.message}")
    else:
        text = escape_markdown_v2(f"Користувач {mention} отримав попередження {warn_count}/3. Причина: {reason}.")
    reply = await bot.send_message(chat_id, text, parse_mode="MarkdownV2")
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"warn_user_action: user_id={user_id}, warn_count={warn_count}, chat_id={chat_id}")


async def info_user_action(task: ModerationTask):
    try:
        async with telethon_client:
            # 1. Отримати user_id по username
            try:
                user = await telethon_client.get_entity(task.username)
                user_id = user.id
                logger.info(f"Отримано user_id={user_id} для username={task.username}")
            except ValueError as e:
                reply_text = f"Користувач @{escape_markdown_v2(task.username)} не знайдений."
                await bot.send_message(task.chat_id, reply_text, parse_mode="MarkdownV2")
                return

        # 2. Історія покарань саме для цього чату
        punishments = await get_punishments(user_id, task.chat_id)
        logger.info(f"Запитано історію покарань: user_id={user_id}, chat_id={task.chat_id}, знайдено {len(punishments)} записів")

        # 3. Членство у поточному чаті
        current_chat_status = "❌ Не є учасником"
        status_map = {
            "creator": "👑 Власник",
            "administrator": "🛡️ Адміністратор",
            "member": "✅ Учасник",
            "restricted": "🚫 Обмежений",
            "left": "❌ Покинув чат",
            "kicked": "🦵 Кікнутий"
        }
        try:
            chat_member = await bot.get_chat_member(chat_id=task.chat_id, user_id=user_id)
            current_chat_status = status_map.get(chat_member.status, f"🔸 {chat_member.status}")
        except TelegramBadRequest as e:
            logger.warning(f"Користувач user_id={user_id} не є учасником чату {task.chat_id} або виникла помилка: {e}")

        # 4. Членство в інших чатах
        bot_chats = await get_bot_chats()
        logger.info(f"Знайдено {len(bot_chats)} чатів для перевірки членства")
        chat_memberships = []
        for other_chat_id in bot_chats:
            if other_chat_id == task.chat_id:
                continue
            try:
                if await is_user_in_chat(other_chat_id, user_id):
                    # Статус
                    try:
                        chat_member = await bot.get_chat_member(chat_id=other_chat_id, user_id=user_id)
                        status = status_map.get(chat_member.status, chat_member.status)
                    except:
                        status = "✅ Учасник"
                    # Назва чату
                    chat_name = f"ID: {other_chat_id}"
                    try:
                        chat_info = await bot.get_chat(other_chat_id)
                        if hasattr(chat_info, "title") and chat_info.title:
                            chat_name = chat_info.title
                        elif hasattr(chat_info, "username") and chat_info.username:
                            chat_name = f"@{chat_info.username}"
                    except:
                        pass
                    chat_memberships.append(f"• {escape_markdown_v2(chat_name)} \\- {status}")
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Помилка при перевірці членства в чаті {other_chat_id}: {e}")
                continue

        # 5. Формування історії покарань
        punishment_list = []
        for p in punishments:
            punishment_type = {
                "kick": "🦵 Кік",
                "ban": "🔨 Бан",
                "warn": "⚠️ Попередження",
                "mute": "🔇 Мут"
            }.get(p["type"], p["type"])
            duration = f" \\({p['duration_minutes']} хвилин\\)" if p['duration_minutes'] else ""
            moderator_id = p["moderator_id"]
            if moderator_id is None or not isinstance(moderator_id, int):
                moderator_mention = "Невідомий модератор"
            else:
                moderator_mention = await get_user_mention(moderator_id, task.chat_id) or f"ID: {moderator_id}"
            reason_escaped = escape_markdown_v2(p['reason'])
            moderator_escaped = escape_markdown_v2(str(moderator_mention))
            timestamp_escaped = escape_markdown_v2(p['timestamp'])
            punishment_text = (
                f"{punishment_type}{duration}\nПричина: {reason_escaped}\nВидав: {moderator_escaped}\nДата: {timestamp_escaped}"
            )
            punishment_list.append(punishment_text)

        # 6. Формування повідомлення
        escaped_username = escape_markdown_v2(task.username)
        chat_count = len(chat_memberships)
        punishment_count = len(punishment_list)
        user_info = [
            f"👤 **Інформація про користувача @{escaped_username}**",
            f"🆔 **User ID:** `{user_id}`",
            f"📍 **Статус у цьому чаті:** {current_chat_status}",
            ""
        ]
        if chat_memberships:
            user_info.extend([
                f"🌐 **Членство в інших каналах/чатах \\({chat_count}\\):**",
                *chat_memberships,
                ""
            ])
        else:
            user_info.extend([
                "🌐 **Не є учасником інших відомих каналів/чатів**",
                ""
            ])
        if punishment_list:
            user_info.extend([
                f"⚖️ **Історія покарань \\({punishment_count}\\):**",
                *punishment_list
            ])
        else:
            user_info.append("✅ **Покарань не знайдено**")

        text = '\n'.join(user_info)
        reply = await bot.send_message(task.chat_id, text, parse_mode="MarkdownV2")
        logger.info(f"Надіслано інформацію про користувача: user_id={user_id}, username={task.username}, chat_id={task.chat_id}")
        await asyncio.sleep(45)
        await safe_delete_message(reply)

    except Exception as e:
        logger.error(f"Помилка info: {e}")
        text = escape_markdown_v2(f"Помилка при отриманні info: {str(e)}")
        await bot.send_message(task.chat_id, text, parse_mode="MarkdownV2")

async def unban_user_action(task):
    try:
        await bot.unban_chat_member(chat_id=task.chat_id, user_id=task.user_id)
        await remove_ban(task.user_id, task.chat_id)
        mention = f"@{task.username}" if task.username else f"ID\\:{task.user_id}"
        text = escape_markdown_v2(f"Знято бан із користувача {mention}.")
        await bot.send_message(task.chat_id, text, parse_mode="MarkdownV2")
    except TelegramBadRequest as e:
        await bot.send_message(task.chat_id, f"Не вдалося зняти бан: {e.message}")

async def unmute_user_action(task):
    try:
        await bot.restrict_chat_member(
            chat_id=task.chat_id,
            user_id=task.user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True
            )
        )
        mention = f"@{task.username}" if task.username else f"ID\\:{task.user_id}"
        text = escape_markdown_v2(f"Знято мут із користувача {mention}.")
        await bot.send_message(task.chat_id, text, parse_mode="MarkdownV2")
        await remove_mute(task.user_id, task.chat_id)
    except TelegramBadRequest as e:
        await bot.send_message(task.chat_id, f"Не вдалося зняти мут: {e.message}")

async def unwarn_user_action(task):
    warn_count = await remove_warning(task.user_id, task.chat_id)
    mention = f"@{task.username}" if task.username else f"ID\\:{task.user_id}"
    if warn_count >= 0:
        text = escape_markdown_v2(f"Знято попередження з користувача {mention}. Залишилось {warn_count}/3.")
        await bot.send_message(task.chat_id, text, parse_mode="MarkdownV2")
    else:
        text = escape_markdown_v2(f"У користувача {mention} немає попереджень.")
        await bot.send_message(task.chat_id, text, parse_mode="MarkdownV2")

async def update_all_chat_titles(bot):
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    if DB_SSLMODE == 'require':
        ssl_context.check_hostname = True
        ssl_context.verify_mode = ssl.CERT_REQUIRED
    conn = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD,
        ssl=ssl_context if DB_SSLMODE == 'require' else None
    )

    rows = await conn.fetch("SELECT chat_id FROM chat_settings")
    for row in rows:
        chat_id = row['chat_id']
        try:
            chat = await bot.get_chat(chat_id)
            chat_title = chat.title
            if chat_title:  # Обновляем только если есть название!
                await conn.execute(
                    "UPDATE chat_settings SET chat_title = $1 WHERE chat_id = $2",
                    chat_title, chat_id
                )
        except Exception as e:
            print(f"Не удалось получить название для {chat_id}: {e}")

    await conn.close()

async def main():
    await init_db()
    try:
        if telethon_client:
            async def phone_input():
                return PHONE_NUMBER

            async def password_input():
                return TWO_FACTOR_PASSWORD if TWO_FACTOR_PASSWORD else None

            await telethon_client.start(phone=phone_input, password=password_input)
            logger.info("Telethon клієнт запущено успішно")
        me = await bot.get_me()
        logger.info(f"Бот запущений: @{me.username}")
        try:
            chat = await bot.get_chat(chat_id=-1002509289582)
            admin_status = await bot.get_chat_member(chat_id=chat.id, user_id=me.id)
            if admin_status.status in ["administrator", "creator"]:
                logger.info(f"Бот є адміністратором у чаті {chat.id}. Права: {admin_status}")
            else:
                logger.error(f"Бот не є адміністратором у чаті {chat.id}. Обмежена функціональність.")
        except TelegramBadRequest as e:
            logger.error(f"Помилка перевірки прав бота: {e}")

        await update_all_chat_titles(bot)
        await ensure_all_chats_in_settings()
        asyncio.create_task(moderation_worker())

        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критична помилка: {e}")
        raise
    finally:
        if telethon_client and telethon_client.is_connected():
            await telethon_client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())