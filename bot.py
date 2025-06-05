import asyncio
import os
import logging
import sqlite3
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ChatPermissions, ChatMemberUpdated
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
import datetime
from telethon.sync import TelegramClient
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsSearch

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Завантаження змінних з .env
load_dotenv()
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(admin_id) for admin_id in os.getenv('ADMIN_IDS', '').split(',') if admin_id.strip()]
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
PHONE_NUMBER = os.getenv('PHONE_NUMBER', '')
TWO_FACTOR_PASSWORD = os.getenv('TWO_FACTOR_PASSWORD', '')
SESSION_PATH = os.getenv('SESSION_PATH', 'bot_session')
DB_PATH = os.getenv('DB_PATH', 'moderators.db')
AUDIO_PATH = os.getenv('AUDIO_PATH', 'QuantRP - ПРОЩАВАЙ.mp3')

# Ініціалізація Telethon клієнта
telethon_client = TelegramClient(SESSION_PATH, API_ID, API_HASH) if API_ID and API_HASH and PHONE_NUMBER else None

# Ініціалізація бази даних SQLite
def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS moderators (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                user_id INTEGER,
                chat_id INTEGER,
                warn_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER,
                chat_id INTEGER,
                reason TEXT,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS punishments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                punishment_type TEXT,
                reason TEXT,
                timestamp TIMESTAMP,
                duration_minutes INTEGER,
                moderator_id INTEGER,
                FOREIGN KEY (user_id, chat_id) REFERENCES warnings (user_id, chat_id)
            )
        ''')
        conn.commit()
        logger.info("База даних ініціалізована успішно.")
    except sqlite3.Error as e:
        logger.error(f"Помилка ініціалізації бази даних: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# Завантаження модераторів із бази даних
def load_moderators():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM moderators')
        moderators = {row[0] for row in cursor.fetchall()}
        return moderators
    except sqlite3.Error as e:
        logger.error(f"Помилка завантаження модераторів: {e}")
        return set()
    finally:
        if 'conn' in locals():
            conn.close()

# Додавання модератора до бази даних
def add_moderator_to_db(user_id: int, username: str = None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO moderators (user_id, username) VALUES (?, ?)', (user_id, username))
        conn.commit()
        logger.info(f"Додано модератора до бази: user_id={user_id}, username={username}")
    except sqlite3.Error as e:
        logger.error(f"Помилка додавання модератора до бази: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# Видалення модератора з бази даних
def remove_moderator_from_db(user_id: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM moderators WHERE user_id = ?', (user_id,))
        conn.commit()
        logger.info(f"Видалено модератора з бази: user_id={user_id}")
    except sqlite3.Error as e:
        logger.error(f"Помилка видалення модератора з бази: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# Перевірка, чи є користувач модератором
def is_moderator(user_id: int) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM moderators WHERE user_id = ?', (user_id,))
        result = cursor.fetchone() is not None
        return result
    except sqlite3.Error as e:
        logger.error(f"Помилка перевірки модератора: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

# Отримання username модератора з бази даних
def get_moderator_username(user_id: int) -> str | None:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT username FROM moderators WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Помилка отримання username модератора: {e}")
        return None
    finally:
        if 'conn' in locals():
            conn.close()

# Додавання попередження
def add_warning(user_id: int, chat_id: int) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO warnings (user_id, chat_id, warn_count)
            VALUES (?, ?, COALESCE((SELECT warn_count FROM warnings WHERE user_id = ? AND chat_id = ?), 0) + 1)
        ''', (user_id, chat_id, user_id, chat_id))
        conn.commit()
        cursor.execute('SELECT warn_count FROM warnings WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        warn_count = cursor.fetchone()[0]
        logger.info(f"Додано попередження: user_id={user_id}, chat_id={chat_id}, warn_count={warn_count}")
        return warn_count
    except sqlite3.Error as e:
        logger.error(f"Помилка додавання попередження: {e}")
        return 0
    finally:
        if 'conn' in locals():
            conn.close()

# Зняття попередження
def remove_warning(user_id: int, chat_id: int) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE warnings SET warn_count = warn_count - 1
            WHERE user_id = ? AND chat_id = ? AND warn_count > 0
        ''', (user_id, chat_id))
        if conn.total_changes == 0:
            return 0
        cursor.execute('SELECT warn_count FROM warnings WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        warn_count = cursor.fetchone()[0]
        if warn_count == 0:
            cursor.execute('DELETE FROM warnings WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        conn.commit()
        logger.info(f"Знято попередження: user_id={user_id}, chat_id={chat_id}, warn_count={warn_count}")
        return warn_count
    except sqlite3.Error as e:
        logger.error(f"Помилка зняття попередження: {e}")
        return 0
    finally:
        if 'conn' in locals():
            conn.close()

# Додавання бана
def add_ban(user_id: int, chat_id: int, reason: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO bans (user_id, chat_id, reason) VALUES (?, ?, ?)', (user_id, chat_id, reason))
        conn.commit()
        logger.info(f"Додано бан: user_id={user_id}, chat_id={chat_id}, reason={reason}")
    except sqlite3.Error as e:
        logger.error(f"Помилка додавання бана: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# Зняття бана
def remove_ban(user_id: int, chat_id: int):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM bans WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        conn.commit()
        logger.info(f"Знято бан: user_id={user_id}, chat_id={chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Помилка зняття бана: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# Отримання кількості попереджень
def get_warning_count(user_id: int, chat_id: int) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT warn_count FROM warnings WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        result = cursor.fetchone()
        return result[0] if result else 0
    except sqlite3.Error as e:
        logger.error(f"Помилка отримання попереджень: {e}")
        return 0
    finally:
        if 'conn' in locals():
            conn.close()

# Логування покарань
def log_punishment(user_id: int, chat_id: int, punishment_type: str, reason: str, duration_minutes: int | None = None, moderator_id: int | None = None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO punishments (user_id, chat_id, punishment_type, reason, timestamp, duration_minutes, moderator_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, chat_id, punishment_type, reason, datetime.datetime.now(), duration_minutes, moderator_id))
        conn.commit()
        logger.info(f"Залоговано покарання: user_id={user_id}, chat_id={chat_id}, type={punishment_type}, reason={reason}, duration={duration_minutes}, moderator_id={moderator_id}")
    except sqlite3.Error as e:
        logger.error(f"Помилка логування покарання: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# Отримання історії покарань
def get_punishments(user_id: int, chat_id: int) -> list:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT punishment_type, reason, timestamp, duration_minutes, moderator_id
            FROM punishments
            WHERE user_id = ? AND chat_id = ?
            ORDER BY timestamp DESC
        ''', (user_id, chat_id))
        punishments = cursor.fetchall()
        logger.info(f"Отримано історію покарань для user_id={user_id}, chat_id={chat_id}: {len(punishments)} записів")
        return [
            {
                "type": p[0],
                "reason": p[1],
                "timestamp": p[2],
                "duration_minutes": p[3],
                "moderator_id": p[4]
            } for p in punishments
        ]
    except sqlite3.Error as e:
        logger.error(f"Помилка отримання історії покарань: {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()

@dp.message(Command('filter'))
async def toggle_filter(message: types.Message):
    global FORBIDDEN_WORDS_FILTER
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    FORBIDDEN_WORDS_FILTER = not FORBIDDEN_WORDS_FILTER
    status = "✅ увімкнено" if FORBIDDEN_WORDS_FILTER else "❌ вимкнено"
    reply = await message.reply(f"Фільтрація заборонених слів {status}")
    await safe_delete_message(message)
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"Змінено статус фільтрації заборонених слів: {status}")


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
FORBIDDEN_WORDS_FILTER = True

# Налаштування привітання
WELCOME_MESSAGE = True  # True - увімкнути привітання, False - вимкнути

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

# Функція для створення згадки користувача
async def get_user_mention(user_id: int, chat_id: int) -> str | None:
    try:
        chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        user = chat_member.user
        if user.username:
            escaped_username = escape_markdown_v2(user.username)
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
def has_moderator_privileges(user_id: int) -> bool:
    return user_id in ADMIN_IDS or is_moderator(user_id)

# Отримання списку учасників через Telethon
async def get_chat_participants(chat_id: int) -> list:
    if not telethon_client:
        logger.error("Telethon клієнт не ініціалізований. Перевірте API_ID, API_HASH, PHONE_NUMBER.")
        return []
    try:
        async with telethon_client:
            chat = await telethon_client.get_entity(chat_id)
            participants = []
            async for participant in telethon_client.iter_participants(chat, filter=ChannelParticipantsSearch('')):
                if participant.username:
                    participants.append(f"@{participant.username}")
                elif participant.first_name:
                    participants.append(f"[{escape_markdown_v2(participant.first_name)}]")
            logger.info(f"Отримано {len(participants)} учасників для чату {chat_id}")
            return participants
    except Exception as e:
        logger.error(f"Помилка отримання учасників чату {chat_id}: {e}")
        return []

@dp.message(Command('welcome'))
async def toggle_welcome(message: types.Message):
    global WELCOME_MESSAGE
    if not has_moderator_privileges(message.from_user.id):
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

@dp.message(Command('addmoder'))
async def add_moderator(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /addmoder 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    if is_moderator(user_id):
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        reply = await message.reply(f"Користувач {escape_markdown_v2(mention)} уже є модератором.", parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    add_moderator_to_db(user_id, username)
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
    text = escape_markdown_v2(f"Користувач {mention} доданий до списку модераторів.")
    reply = await message.reply(text, parse_mode="MarkdownV2")
    await safe_delete_message(message)
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"Додано модератора: user_id={user_id}, username={username}, chat_id={message.chat.id}")

@dp.message(Command('removemoder'))
async def remove_moderator(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /removemoder 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    if not is_moderator(user_id):
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        reply = await message.reply(f"Користувач {escape_markdown_v2(mention)} не є модератором.", parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    remove_moderator_from_db(user_id)
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
    text = escape_markdown_v2(f"Користувач {mention} видалений зі списку модераторів.")
    reply = await message.reply(text, parse_mode="MarkdownV2")
    await safe_delete_message(message)
    await asyncio.sleep(25)
    await safe_delete_message(reply)
    logger.info(f"Видалено модератора: user_id={user_id}, username={username}, chat_id={message.chat.id}")

@dp.message(Command('kick'))
async def kick_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id і причину у форматі /kick 123456789 причина або відповідайте на повідомлення користувача з /kick причина.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, reason = user_data
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"

    # Надсилаємо музику перед кік
    if os.path.exists(AUDIO_PATH):
        try:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=types.FSInputFile(AUDIO_PATH),
                caption=escape_markdown_v2(f"Користувач {mention} отримує кік! 🎵 Причина: {reason}"),
                parse_mode="MarkdownV2"
            )
            logger.info(f"Надіслано музику перед кік для user_id={user_id} у чаті {message.chat.id}")
            await asyncio.sleep(25)
        except TelegramBadRequest as e:
            logger.error(f"Помилка при надсиланні музики для user_id={user_id}: {e}")
    else:
        logger.warning(f"Аудіофайл {AUDIO_PATH} не знайдено")

    # Виконуємо кік
    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, revoke_messages=False)
        log_punishment(user_id, message.chat.id, "kick", reason, moderator_id=message.from_user.id)
        text = escape_markdown_v2(f"Користувач {mention} кікнутий з чату. Причина: {reason}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Кікнуто користувача: user_id={user_id}, username={username}, reason={reason}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.error(f"Помилка при кіку користувача {user_id}: {e}")
        reply = await message.reply(f"Не вдалося кікнути користувача: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('warn'))
async def warn_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id і причину у форматі /warn 123456789 причина або відповідайте на повідомлення користувача з /warn причина.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, reason = user_data
    warn_count = add_warning(user_id, message.chat.id)
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
    log_punishment(user_id, message.chat.id, "warn", reason, moderator_id=message.from_user.id)
    if warn_count >= 3:
        try:
            await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, revoke_messages=False)
            log_punishment(user_id, message.chat.id, "kick", "3 попередження", moderator_id=message.from_user.id)
            text = escape_markdown_v2(f"Користувач {mention} отримав 3/3 попередження і кікнутий з чату. Причина: {reason}.")
            reply = await message.reply(text, parse_mode="MarkdownV2")
            await safe_delete_message(message)
            await asyncio.sleep(25)
            await safe_delete_message(reply)
            logger.info(f"Кікнуто за 3 попередження: user_id={user_id}, username={username}, reason={reason}, chat_id={message.chat.id}")
        except TelegramBadRequest as e:
            logger.error(f"Помилка при кіку користувача {user_id}: {e}")
            reply = await message.reply(f"Не вдалося кікнути користувача: {e.message}")
            await safe_delete_message(message)
            await asyncio.sleep(25)
            await safe_delete_message(reply)
    else:
        text = escape_markdown_v2(f"Користувач {mention} отримав попередження {warn_count}/3. Причина: {reason}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Видано попередження: user_id={user_id}, username={username}, warn_count={warn_count}, reason={reason}, chat_id={message.chat.id}")

@dp.message(Command('ban'))
async def ban_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id і причину у форматі /ban 123456789 причина або відповідайте на повідомлення користувача з /ban причина.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, reason = user_data
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"

    # Надсилаємо музику перед бан
    if os.path.exists(AUDIO_PATH):
        try:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=types.FSInputFile(AUDIO_PATH),
                caption=escape_markdown_v2(f"Користувач {mention} отримує бан! 🎵 Причина: {reason}"),
                parse_mode="MarkdownV2"
            )
            logger.info(f"Надіслано музику перед бан для user_id={user_id} у чаті {message.chat.id}")
            await asyncio.sleep(25)
        except TelegramBadRequest as e:
            logger.error(f"Помилка при надсиланні музики для user_id={user_id}: {e}")
    else:
        logger.warning(f"Аудіофайл {AUDIO_PATH} не знайдено")

    # Виконуємо бан
    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, revoke_messages=False)
        add_ban(user_id, message.chat.id, reason)
        log_punishment(user_id, message.chat.id, "ban", reason, moderator_id=message.from_user.id)
        text = escape_markdown_v2(f"Користувач {mention} забанений. Причина: {reason}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Забанено користувача: user_id={user_id}, username={username}, reason={reason}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.error(f"Помилка при бану користувача {user_id}: {e}")
        reply = await message.reply(f"Не вдалося забанить користувача: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('mute'))
async def mute_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    minutes = None
    reason = None
    if len(args) >= 3 and re.match(r'^\d+$', args[0]) and args[1].isdigit():
        user_id = args[0]
        minutes = int(args[1])
        reason = ' '.join(args[2:])
    elif message.reply_to_message and len(args) >= 2 and args[0].isdigit():
        user_id = None
        minutes = int(args[0])
        reason = ' '.join(args[1:])
    else:
        reply = await message.reply("Будь ласка, вкажіть user_id, час у хвилинах і причину у форматі /mute 123456789 60 причина або відповідайте на повідомлення користувача з /mute 60 причина.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_data = await get_user_data(message, args if user_id else args[1:])
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть коректний user_id, час у хвилинах і причину у форматі /mute 123456789 60 причина або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id_from_data, username, _ = user_data
    user_id = user_id_from_data if user_id_from_data else message.reply_to_message.from_user.id

    try:
        mute_until = datetime.datetime.now() + datetime.timedelta(minutes=minutes)
        await bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_polls=False,
                can_send_other_messages=False
            ),
            until_date=mute_until
        )
        log_punishment(user_id, message.chat.id, "mute", reason, duration_minutes=minutes, moderator_id=message.from_user.id)
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        text = escape_markdown_v2(f"Користувач {mention} отримав мут на {minutes} хвилин. Причина: {reason}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Видано мут: user_id={user_id}, username={username}, minutes={minutes}, reason={reason}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.error(f"Помилка при муті користувача {user_id}: {e}")
        reply = await message.reply(f"Не вдалося видати мут: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('unmute'))
async def unmute_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /unmute 123456789 Причину або відповідайте на повідомлення користувача і вкажіть причину.")
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
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        text = escape_markdown_v2(f"Знято мут із користувача {mention}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Знято мут: user_id={user_id}, username={username}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.error(f"Помилка при знятті мута користувача {user_id}: {e}")
        reply = await message.reply(f"Не вдалося зняти мут: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('unwarn'))
async def unwarn_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /unwarn 123456789 Причину або відповідайте на повідомлення користувача і вкажіть причину")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    warn_count = remove_warning(user_id, message.chat.id)
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
    if warn_count >= 0:
        text = escape_markdown_v2(f"Знято попередження з користувача {mention}. Залишилось {warn_count}/3.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Знято попередження: user_id={user_id}, username={username}, warn_count={warn_count}, chat_id={message.chat.id}")
    else:
        text = escape_markdown_v2(f"У користувача {mention} немає попереджень.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('unban'))
async def unban_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /unban 123456789 Причину або відповідайте на повідомлення користувача і вкажіть причину")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username, _ = user_data
    try:
        await bot.unban_chat_member(chat_id=message.chat.id, user_id=user_id)
        remove_ban(user_id, message.chat.id)
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        text = escape_markdown_v2(f"Знято бан із користувача {mention}.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Знято бан: user_id={user_id}, username={username}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.error(f"Помилка при знятті бана користувача {user_id}: {e}")
        reply = await message.reply(f"Не вдалося зняти бан: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('info'))
async def info_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].startswith('@'):
        reply = await message.reply("Будь ласка, вкажіть username у форматі /info @username.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    username = args[1].lstrip('@')
    try:
        async with telethon_client:
            try:
                user = await telethon_client.get_entity(username)
                user_id = user.id
                logger.info(f"Отримано user_id={user_id} для username={username}")
            except ValueError as e:
                logger.error(f"Не вдалося знайти користувача за username={username}: {e}")
                reply = await message.reply(f"Користувач @{escape_markdown_v2(username)} не знайдений.")
                await safe_delete_message(message)
                await asyncio.sleep(25)
                await safe_delete_message(reply)
                return

        punishments = get_punishments(user_id, message.chat.id)
        logger.info(f"Отримано історію покарань для user_id={user_id}, chat_id={message.chat.id}: {len(punishments)} записів")

        mention = f"@{escape_markdown_v2(username)}"
        try:
            logger.info(f"Перевірка членства в чаті: user_id={user_id}, chat_id={message.chat.id}")
            chat_member = await bot.get_chat_member(chat_id=message.chat.id, user_id=user_id)
            logger.info(f"Отримано дані учасника: user_id={user_id}, status={chat_member.status}")
            mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        except TelegramBadRequest as e:
            logger.warning(f"Користувач user_id={user_id} не є учасником чату {message.chat.id} або виникла помилка: {e}")
            mention += f" (не є учасником чату)"

        punishment_list = []
        for p in punishments:
            punishment_type = {
                "kick": "Кік",
                "ban": "Бан",
                "warn": "Попередження",
                "mute": "Мут"
            }.get(p["type"], p["type"])
            duration = f" ({p['duration_minutes']} хвилин)" if p["duration_minutes"] else ""
            moderator_id = p["moderator_id"]
            if moderator_id is None or not isinstance(moderator_id, int):
                logger.warning(f"Некоректний moderator_id={moderator_id} для покарання user_id={user_id}")
                moderator_mention = "Невідомий модератор"
            else:
                moderator_mention = await get_user_mention(moderator_id, message.chat.id) or f"ID\\:{moderator_id}"
            timestamp = datetime.datetime.strptime(p["timestamp"], '%Y-%m-%d %H:%M:%S.%f').strftime('%Y-%m-%d %H:%M')
            punishment_text = escape_markdown_v2(
                f"{punishment_type}{duration} - Причина: {p['reason']} (Видав: {moderator_mention}, {timestamp})"
            )
            punishment_list.append(punishment_text)

        if not punishment_list:
            text = escape_markdown_v2(f"Користувач {mention}\nUserID: {user_id}\nПокарань не знайдено.")
        else:
            text = escape_markdown_v2(f"Користувач {mention}\nUserID: {user_id}\n\nІсторія покарань:\n") + '\n'.join(punishment_list)
        reply = await message.reply(text, parse_mode="MarkdownV2")
        logger.info(f"Надіслано інформацію про користувача: user_id={user_id}, username={username}, chat_id={message.chat.id}")
    except Exception as e:
        logger.error(f"Загальна помилка обробки команди /info для username={username}: {e}")
        reply = await message.reply(f"Помилка при отриманні інформації про користувача @{escape_markdown_v2(username)}: {str(e)}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('ad'))
async def make_announcement(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
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

    participants = await get_chat_participants(chat_id)
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
        full_text = escape_markdown_v2(f"📢 Оголошення:\n{announcement_text}\n\n{mentions}" if mentions else f"📢 Оголошення:\n{announcement_text}")
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
        logger.info(f"Надіслано та закріплено перше оголошення в чаті {chat_id} з {len(first_chunk)} згадками")

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
                logger.info(f"Надіслано додаткове повідомлення з {len(chunk)} згадками в чаті {chat_id}")
                await asyncio.sleep(4)
        await safe_delete_message(message)
    except TelegramBadRequest as e:
        logger.error(f"Помилка при надсиланні/закріпленні оголошення в чаті {chat_id}: {e}")
        reply = await message.reply(f"Не вдалося надіслати або закріпити оголошення: {e.message}")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.chat_member()
async def welcome_new_member(update: ChatMemberUpdated):
    user = update.new_chat_member.user
    old_status = getattr(update.old_chat_member, 'status', 'none')
    new_status = update.new_chat_member.status
    logger.info(f"Отримано подію chat_member: user_id={user.id}, old_status={old_status}, new_status={new_status}, chat_id={update.chat.id}")
    if (WELCOME_MESSAGE and new_status in ["member", "restricted"] and
            (update.old_chat_member is None or old_status in ["left", "kicked"])):
        try:
            mention = await get_user_mention(user.id, update.chat.id) or f"User {user.id}"
            chat = await bot.get_chat(update.chat.id)
            chat_username = f"@{chat.username}" if chat.username else f"ID:{update.chat.first_name}"
            text = escape_markdown_v2(f"Вітаємо, {mention}! Ласкаво просимо до {chat_username}! 😊")
            await bot.send_message(
                chat_id=update.chat.id,
                text=text,
                parse_mode="MarkdownV2"
            )
            logger.info(f"Відправлено привітання для {user.id} у чаті {update.chat.id}")
        except TelegramBadRequest as e:
            logger.error(f"Помилка при відправці привітання для {user.id}: {e}")
            try:
                await bot.send_message(
                    chat_id=update.chat.id,
                    text=f"Вітаємо, user_id={user.id}! Ласкаво просимо до нашого чату! (Дебаг)",
                    parse_mode=None
                )
                logger.info(f"Відправлено дебаг-повідомлення для {user.id}")
            except Exception as debug_e:
                logger.error(f"Помилка дебаг-повідомлення для {user.id}: {debug_e}")

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
        logger.info(f"Надіслано правила для user_id={message.from_user.id}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.error(f"Помилка при надсиланні правил для user_id={message.from_user.id}: {e}")
        reply = await message.reply("Помилка при відображенні правил. Спробуйте ще раз.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message(Command('help'))
async def show_help(message: types.Message):
    is_mod = has_moderator_privileges(message.from_user.id)
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
            "❓ /help - Показати цей список команд."
        )
    else:
        help_text = (
            "📚 Список доступних команд:\n\n"
            "📜 /rules - Переглянути правила чату.\n"
            "❓ /help - Показати цей список команд."
        )

    text = escape_markdown_v2_help(help_text)
    try:
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Надіслано список команд для user_id={message.from_user.id}, chat_id={message.chat.id}, is_moderator={is_mod}")
    except TelegramBadRequest as e:
        logger.error(f"Помилка при надсиланні списку команд для user_id={message.from_user.id}: {e}")
        reply = await message.reply("Помилка при відображенні команд. Спробуйте ще раз.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)

@dp.message()
async def filter_messages(message: types.Message):
    if not FORBIDDEN_WORDS_FILTER or not message.text:  # Перевірка стану фільтра
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
                log_punishment(message.from_user.id, message.chat.id, "mute", f"Використання забороненого слова: {word}", duration_minutes=24*60, moderator_id=None)
                mention = await get_user_mention(message.from_user.id, message.chat.id) or f"User {message.from_user.id}"
                text = escape_markdown_v2(f"Користувач {mention} отримав мут на 24 години за використання забороненого слова.")
                reply = await message.reply(text, parse_mode="MarkdownV2")
                await safe_delete_message(message)
                await asyncio.sleep(25)
                await safe_delete_message(reply)
                logger.info(f"Користувач {message.from_user.id} отримав мут за слово '{word}'")
            except TelegramBadRequest as e:
                logger.error(f"Помилка при муті користувача {message.from_user.id}: {e}")
                mention = await get_user_mention(message.from_user.id, message.chat.id) or f"User {message.from_user.id}"
                error_text = escape_markdown_v2(f"Помилка при видачі мута для {mention}: {str(e)}")
                reply = await bot.send_message(message.chat.id, error_text, parse_mode="MarkdownV2")
                await safe_delete_message(message)
                await asyncio.sleep(25)
                await safe_delete_message(reply)
            break

async def main():
    init_db()

if __name__ == '__main__':
    asyncio.run(main())