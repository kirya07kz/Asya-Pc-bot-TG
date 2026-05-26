import os
import sys
from dotenv import load_dotenv

# Определяем базовую директорию проекта
if getattr(sys, 'frozen', False):
    # Если запущено как скомпилированный EXE через PyInstaller
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Обычный запуск через интерпретатор Python
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Загружаем переменные из файла .env в базовой директории
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=env_path, override=True)

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
PROGRAMS_FILE_PATH = os.path.join(BASE_DIR, "programs.json")

