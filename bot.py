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
import os.path

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Завантаження змінних з .env
load_dotenv()
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(admin_id) for admin_id in os.getenv('ADMIN_IDS', '').split(',') if admin_id]
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
PHONE_NUMBER = os.getenv('PHONE_NUMBER', '')
TWO_FACTOR_PASSWORD = os.getenv('TWO_FACTOR_PASSWORD', '')

# Ініціалізація Telethon клієнта
telethon_client = TelegramClient('bot_session', API_ID, API_HASH) if API_ID and API_HASH and PHONE_NUMBER else None

# Ініціалізація бази даних SQLite
def init_db():
    try:
        conn = sqlite3.connect('moderators.db')
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
        conn.commit()
        logger.info("База даних ініціалізована успішно.")
    except sqlite3.Error as e:
        logger.error(f"Помилка ініціалізації бази даних: {e}")
    finally:
        conn.close()

# Завантаження модераторів із бази даних
def load_moderators():
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM moderators')
        moderators = {row[0] for row in cursor.fetchall()}
        return moderators
    except sqlite3.Error as e:
        logger.error(f"Помилка завантаження модераторів: {e}")
        return set()
    finally:
        conn.close()

# Додавання модератора до бази даних
def add_moderator_to_db(user_id: int, username: str = None):
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO moderators (user_id, username) VALUES (?, ?)', (user_id, username))
        conn.commit()
        logger.info(f"Додано модератора до бази: user_id={user_id}, username={username}")
    except sqlite3.Error as e:
        logger.error(f"Помилка додавання модератора до бази: {e}")
    finally:
        conn.close()

# Видалення модератора з бази даних
def remove_moderator_from_db(user_id: int):
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM moderators WHERE user_id = ?', (user_id,))
        conn.commit()
        logger.info(f"Видалено модератора з бази: user_id={user_id}")
    except sqlite3.Error as e:
        logger.error(f"Помилка видалення модератора з бази: {e}")
    finally:
        conn.close()

# Перевірка, чи є користувач модератором
def is_moderator(user_id: int) -> bool:
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM moderators WHERE user_id = ?', (user_id,))
        result = cursor.fetchone() is not None
        return result
    except sqlite3.Error as e:
        logger.error(f"Помилка перевірки модератора: {e}")
        return False
    finally:
        conn.close()

# Отримання username модератора з бази даних
def get_moderator_username(user_id: int) -> str | None:
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('SELECT username FROM moderators WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Помилка отримання username модератора: {e}")
        return None
    finally:
        conn.close()

# Додавання попередження
def add_warning(user_id: int, chat_id: int) -> int:
    try:
        conn = sqlite3.connect('moderators.db')
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
        conn.close()

# Зняття попередження
def remove_warning(user_id: int, chat_id: int) -> int:
    try:
        conn = sqlite3.connect('moderators.db')
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
        conn.close()

# Додавання бана
def add_ban(user_id: int, chat_id: int, reason: str):
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO bans (user_id, chat_id, reason) VALUES (?, ?, ?)',
                       (user_id, chat_id, reason))
        conn.commit()
        logger.info(f"Додано бан: user_id={user_id}, chat_id={chat_id}, reason={reason}")
    except sqlite3.Error as e:
        logger.error(f"Помилка додавання бана: {e}")
    finally:
        conn.close()

# Зняття бана
def remove_ban(user_id: int, chat_id: int):
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM bans WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        conn.commit()
        logger.info(f"Знято бан: user_id={user_id}, chat_id={chat_id}")
    except sqlite3.Error as e:
        logger.error(f"Помилка зняття бана: {e}")
    finally:
        conn.close()

# Отримання кількості попереджень
def get_warning_count(user_id: int, chat_id: int) -> int:
    try:
        conn = sqlite3.connect('moderators.db')
        cursor = conn.cursor()
        cursor.execute('SELECT warn_count FROM warnings WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        result = cursor.fetchone()
        return result[0] if result else 0
    except sqlite3.Error as e:
        logger.error(f"Помилка отримання попереджень: {e}")
        return 0
    finally:
        conn.close()

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

# Налаштування привітання
WELCOME_MESSAGE = True  # True - увімкнути привітання, False - вимкнути

# Функція для екранування спеціальних символів у MarkdownV2
def escape_markdown_v2(text: str) -> str:
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '#', '+', '-', '=', '|', '{', '}', '.', '!']
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
            mention = f"[{escaped_name}](tg://user?id={user_id})"
            logger.info(f"Username відсутній, використовується ім'я: {mention} для user_id={user_id}")
            return mention
    except TelegramBadRequest as e:
        logger.error(f"Помилка при отриманні користувача {user_id} у чаті {chat_id}: {e}")
        return None

# Функція для безпечного видалення повідомлення
async def safe_delete_message(message: types.Message):
    try:
        await message.delete()
        logger.info(f"Видалено повідомлення: message_id={message.message_id}, chat_id={message.chat.id}")
    except TelegramBadRequest as e:
        logger.warning(f"Не вдалося видалити повідомлення {message.message_id}: {e}")

# Функція для отримання user_id і username через reply або user_id
async def get_user_data(message: types.Message, args: list) -> tuple[int, str | None] | None:
    chat_id = message.chat.id
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
        username = message.reply_to_message.from_user.username
        username = username.lstrip('@') if username else None
        logger.info(f"Отримано user_id через reply: user_id={user_id}, username={username}")
        return user_id, username
    if args and re.match(r'^\d+$', args[0]):
        try:
            user_id = int(args[0])
            try:
                chat_member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
                username = chat_member.user.username.lstrip('@') if chat_member.user.username else None
                logger.info(f"Отримано user_id через аргумент: user_id={user_id}, username={username}")
                return user_id, username
            except TelegramBadRequest as e:
                logger.error(f"Помилка при перевірці user_id {user_id} у чаті {chat_id}: {e}")
                return None
        except ValueError:
            logger.error(f"Некоректний user_id у аргументах: {args[0]}")
            return None
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
                    participants.append(f"[{escape_markdown_v2(participant.first_name)}](tg://user?id={participant.id})")
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

    user_id, username = user_data
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

    user_id, username = user_data
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
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /kick 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username = user_data
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"

    # Надсилаємо музику перед кік
    audio_path = 'kick_music.mp3'
    if os.path.exists(audio_path):
        try:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=types.FSInputFile(audio_path),
                caption=escape_markdown_v2(f"Користувач {mention} отримує кік! 🎵"),
                parse_mode="MarkdownV2"
            )
            logger.info(f"Надіслано музику перед кік для user_id={user_id} у чаті {message.chat.id}")
            await asyncio.sleep(25)  # Затримка, щоб музика встигла відобразитися
        except TelegramBadRequest as e:
            logger.error(f"Помилка при надсиланні музики для user_id={user_id}: {e}")
    else:
        logger.warning(f"Аудіофайл {audio_path} не знайдено")

    # Виконуємо кік
    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, revoke_messages=False)
        text = escape_markdown_v2(f"Користувач {mention} кікнутий з чату.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Кікнуто користувача: user_id={user_id}, username={username}, chat_id={message.chat.id}")
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
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /warn 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username = user_data
    warn_count = add_warning(user_id, message.chat.id)
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
    if warn_count >= 3:
        try:
            await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, revoke_messages=False)
            text = escape_markdown_v2(f"Користувач {mention} отримав 3/3 попередження і кікнутий з чату.")
            reply = await message.reply(text, parse_mode="MarkdownV2")
            await safe_delete_message(message)
            await asyncio.sleep(25)
            await safe_delete_message(reply)
            logger.info(f"Кікнуто за 3 попередження: user_id={user_id}, username={username}, chat_id={message.chat.id}")
        except TelegramBadRequest as e:
            logger.error(f"Помилка при кіку користувача {user_id}: {e}")
            reply = await message.reply(f"Не вдалося кікнути користувача: {e.message}")
            await safe_delete_message(message)
            await asyncio.sleep(25)
            await safe_delete_message(reply)
    else:
        text = escape_markdown_v2(f"Користувач {mention} отримав попередження {warn_count}/3.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Видано попередження: user_id={user_id}, username={username}, warn_count={warn_count}, chat_id={message.chat.id}")

@dp.message(Command('ban'))
async def ban_user(message: types.Message):
    if not has_moderator_privileges(message.from_user.id):
        reply = await message.reply("Ви не маєте прав для виконання цієї команди.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    args = message.text.split()[1:]
    reason = None
    if len(args) > 1 and re.match(r'^\d+$', args[0]):
        reason = ' '.join(args[1:]) or "Не вказано причину"
    elif message.reply_to_message and len(args) >= 1:
        reason = ' '.join(args) or "Не вказано причину"
    else:
        reply = await message.reply("Будь ласка, вкажіть user_id і причину у форматі /ban 123456789 причина або відповідайте на повідомлення користувача з /ban [причина].")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть коректний user_id у форматі /ban 123456789 причина або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username = user_data
    mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"

    # Надсилаємо музику перед бан
    audio_path = 'kick_music.mp3'
    if os.path.exists(audio_path):
        try:
            await bot.send_audio(
                chat_id=message.chat.id,
                audio=types.FSInputFile(audio_path),
                caption=escape_markdown_v2(f"Користувач {mention} отримує бан! 🎵 Причина: {reason}"),
                parse_mode="MarkdownV2"
            )
            logger.info(f"Надіслано музику перед бан для user_id={user_id} у чаті {message.chat.id}")
            await asyncio.sleep(25)  # Затримка, щоб музика встигла відобразитися
        except TelegramBadRequest as e:
            logger.error(f"Помилка при надсиланні музики для user_id={user_id}: {e}")
    else:
        logger.warning(f"Аудіофайл {audio_path} не знайдено")

    # Виконуємо бан
    try:
        await bot.ban_chat_member(chat_id=message.chat.id, user_id=user_id, revoke_messages=False)
        add_ban(user_id, message.chat.id, reason)
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
    if len(args) > 1 and re.match(r'^\d+$', args[0]) and args[1].isdigit():
        minutes = int(args[1])
    elif message.reply_to_message and len(args) == 1 and args[0].isdigit():
        minutes = int(args[0])
    else:
        reply = await message.reply("Будь ласка, вкажіть user_id і час у хвилинах у форматі /mute 123456789 60 або відповідайте на повідомлення користувача з /mute 60.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_data = await get_user_data(message, args)
    if not user_data:
        reply = await message.reply("Будь ласка, вкажіть коректний user_id у форматі /mute 123456789 60 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username = user_data
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
        mention = await get_user_mention(user_id, message.chat.id) or f"ID\\:{user_id}"
        text = escape_markdown_v2(f"Користувач {mention} отримав мут на {minutes} хвилин.")
        reply = await message.reply(text, parse_mode="MarkdownV2")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        logger.info(f"Видано мут: user_id={user_id}, username={username}, minutes={minutes}, chat_id={message.chat.id}")
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
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /unmute 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username = user_data
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
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /unwarn 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username = user_data
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
        reply = await message.reply("Будь ласка, вкажіть user_id у форматі /unban 123456789 або відповідайте на повідомлення користувача.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    user_id, username = user_data
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

    # Отримання списку учасників
    participants = await get_chat_participants(chat_id)
    if not participants:
        reply = await message.reply("Не вдалося отримати список учасників. Перевірте налаштування Telethon.")
        await safe_delete_message(message)
        await asyncio.sleep(25)
        await safe_delete_message(reply)
        return

    # Розбиваємо учасників на групи по 50
    chunk_size = 50
    participant_chunks = [participants[i:i + chunk_size] for i in range(0, len(participants), chunk_size)]
    sent_message = None

    try:
        # Надсилаємо перше повідомлення з текстом оголошення
        first_chunk = participant_chunks[0] if participant_chunks else []
        mentions = ' '.join(first_chunk)
        full_text = escape_markdown_v2(f"📢 Оголошення:\n{announcement_text}\n\n{mentions}" if mentions else f"📢 Оголошення:\n{announcement_text}")
        sent_message = await bot.send_message(
            chat_id=chat_id,
            text=full_text,
            parse_mode="MarkdownV2",
            disable_notification=False
        )
        # Закріплюємо перше повідомлення
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent_message.message_id,
            disable_notification=False
        )
        logger.info(f"Надіслано та закріплено перше оголошення в чаті {chat_id} з {len(first_chunk)} згадками")

        # Надсилаємо додаткові повідомлення з рештою згадок
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
                await asyncio.sleep(4)  # Затримка 4 секунди, щоб уникнути обмежень Telegram

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
            chat_username = f"@{chat.username}" if chat.username else f"ID:{update.chat.id}"
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

@dp.message()
async def filter_messages(message: types.Message):
    if not message.text:
        return
    message_text = message.text.lower()
    for word in FORBIDDEN_WORDS:
        if word in message_text:
            try:
                await safe_delete_message(message)
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
                mention = await get_user_mention(message.from_user.id, message.chat.id) or f"User {message.from_user.id}"
                text = escape_markdown_v2(f"Користувач {mention} отримав мут на 24 години за використання забороненого слова.")
                reply = await message.reply(text, parse_mode="MarkdownV2")
                await asyncio.sleep(25)
                await safe_delete_message(reply)
                logger.info(f"Користувач {message.from_user.id} отримав мут за слово '{word}'")
            except TelegramBadRequest as e:
                logger.error(f"Помилка при муті користувача {message.from_user.id}: {e}")
                error_text = escape_markdown_v2(f"Помилка при видачі мута для {mention}: {str(e)}")
                reply = await message.reply(error_text, parse_mode="MarkdownV2")
                await asyncio.sleep(25)
                await safe_delete_message(reply)
            break

async def main():
    init_db()
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
                logger.warning(f"Бот не є адміністратором у чаті {chat.id}. Обмежена функціональність.")
        except TelegramBadRequest as e:
            logger.error(f"Помилка перевірки прав бота: {e}")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Помилка ініціалізації бота: {e}")
    finally:
        if telethon_client and telethon_client.is_connected():
            await telethon_client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())