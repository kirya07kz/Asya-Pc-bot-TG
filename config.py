import os
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_telegram_bot_token_here")

# Список разрешенных Telegram ID (разделенных запятыми в .env)
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS = []

if ALLOWED_USERS_RAW:
    for user_id_str in ALLOWED_USERS_RAW.split(","):
        user_id_str = user_id_str.strip()
        if user_id_str.isdigit():
            ALLOWED_USERS.append(int(user_id_str))

# Путь к файлу конфигурации программ
PROGRAMS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "programs.json")

