import os
import sys
import asyncio
import logging
import json
import time
from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
)
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
import pyautogui
import subprocess
import config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

try:
    import msvcrt
    has_msvcrt = True
except ImportError:
    has_msvcrt = False

lock_file = None

# Блокировка запуска дубликатов процесса бота
def acquire_lock():
    if not has_msvcrt:
        return
    global lock_file
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.lock")
    try:
        lock_file = open(lock_path, "w")
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        logger.info("Блокировка процесса успешно получена.")
    except (IOError, OSError):
        logger.warning("Бот уже запущен в другом процессе. Завершение работы текущего процесса...")
        sys.exit(0)

# Функция для защиты от спама сообщениями о запуске
def should_send_startup_notification() -> bool:
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_start.txt")
    current_time = time.time()
    
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                last_time = float(f.read().strip())
            if current_time - last_time < 300:  # 5 минут коулдауна
                logger.info("Уведомление о запуске пропущено (коулдаун 5 минут).")
                return False
        except Exception:
            pass
            
    try:
        with open(state_file, "w") as f:
            f.write(str(current_time))
    except Exception as e:
        logger.error(f"Не удалось записать время запуска в last_start.txt: {e}")
        
    return True

# Middleware для проверки авторизации пользователей
class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data: dict):
        if not event.from_user:
            return await handler(event, data)
        
        user_id = event.from_user.id
        if user_id not in config.ALLOWED_USERS:
            logger.warning(
                f"Неавторизованная попытка доступа от ID: {user_id} "
                f"(Username: @{event.from_user.username or 'unknown'})"
            )
            
            # Если это нажатие на Inline-кнопку
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ ограничен!", show_alert=True)
                return
            
            # Сообщение с подсказкой и ID пользователя
            if isinstance(event, Message):
                await event.answer(
                    f"⛔ <b>Доступ ограничен!</b>\n\n"
                    f"Ваш Telegram User ID: <code>{user_id}</code>\n"
                    f"Для управления компьютером добавьте этот ID в файл <code>.env</code> "
                    f"в переменную <code>ALLOWED_USERS</code> (через запятую, если их несколько).",
                    parse_mode="HTML"
                )
                return
            return
        
        return await handler(event, data)

router = Router()

# Загрузка списка программ из JSON
def load_programs() -> list:
    if not os.path.exists(config.PROGRAMS_FILE_PATH):
        logger.warning(f"Файл {config.PROGRAMS_FILE_PATH} не найден.")
        return []
    try:
        with open(config.PROGRAMS_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при загрузке JSON-файла программ: {e}")
        return []

# Безопасное изменение текста сообщения (игнорируем ошибку, если текст не изменился)
async def safe_edit_text(message: Message, text: str, reply_markup: InlineKeyboardMarkup = None):
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.error(f"Ошибка при редактировании сообщения: {e}")

# Постоянная Reply-клавиатура внизу экрана
def get_reply_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="🖥️ Меню управления"))
    builder.add(KeyboardButton(text="📸 Скриншот"))
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

# Главное встроенное меню
def get_main_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚙️ Система", callback_data="menu_system"),
        InlineKeyboardButton(text="🚀 Запуск программ", callback_data="menu_launch")
    )
    builder.row(
        InlineKeyboardButton(text="❌ Закрытие программ", callback_data="menu_close"),
        InlineKeyboardButton(text="📸 Скриншот экрана", callback_data="sys_screenshot")
    )
    builder.row(
        InlineKeyboardButton(text="🎵 Мультимедиа", callback_data="menu_media")
    )
    return builder.as_markup()

# Меню управления системой (Выкл, Сон, Блокировка, Диспетчер задач)
def get_system_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔌 Выкл", callback_data="sys_shutdown"),
        InlineKeyboardButton(text="💤 Сон", callback_data="sys_sleep")
    )
    builder.row(
        InlineKeyboardButton(text="🔒 Блокировка", callback_data="sys_lock"),
        InlineKeyboardButton(text="📊 Диспетчер задач", callback_data="sys_taskmgr")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад", callback_data="menu_main")
    )
    return builder.as_markup()

# Меню управления мультимедиа
def get_media_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏮️ Назад", callback_data="media_prev"),
        InlineKeyboardButton(text="⏯️ Старт/Пауза", callback_data="media_play"),
        InlineKeyboardButton(text="⏭️ Вперед", callback_data="media_next")
    )
    builder.row(
        InlineKeyboardButton(text="🔉 Тише", callback_data="media_voldown"),
        InlineKeyboardButton(text="🔇 Звук", callback_data="media_mute"),
        InlineKeyboardButton(text="🔊 Громче", callback_data="media_volumeup")
    )
    builder.row(
        InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu_main")
    )
    return builder.as_markup()

# Меню запуска программ
def get_launch_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    programs = load_programs()
    for idx, prog in enumerate(programs):
        name = prog.get("name", f"Программа {idx + 1}")
        builder.add(InlineKeyboardButton(text=f"🚀 {name}", callback_data=f"run_{idx}"))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu_main"))
    return builder.as_markup()

# Меню закрытия программ
def get_close_inline_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # Кнопка для закрытия текущего активного окна
    builder.row(InlineKeyboardButton(text="⏹️ Закрыть активное окно (Alt+F4)", callback_data="sys_close_active"))
    
    programs = load_programs()
    # Показываем только те программы, у которых указан процесс для закрытия
    valid_programs = [(idx, p) for idx, p in enumerate(programs) if p.get("process")]
    for idx, prog in valid_programs:
        name = prog.get("name")
        builder.add(InlineKeyboardButton(text=f"❌ {name}", callback_data=f"killproc_{idx}"))
    builder.adjust(1, 2)  # Первой строкой Alt+F4, затем по 2 в ряд
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="menu_main"))
    return builder.as_markup()

# Обработчик команды /start
@router.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}! Я Asya Pc Bot для управления компьютером.\n\n"
        f"Используйте кнопки меню ниже для управления.",
        reply_markup=get_reply_keyboard()
    )
    await message.answer(
        "🖥️ <b>Панель управления ПК</b>\n\nВыберите категорию:",
        reply_markup=get_main_inline_keyboard(),
        parse_mode="HTML"
    )

# Обработчик вызова главного меню через Reply-клавиатуру
@router.message(F.text == "🖥️ Меню управления")
async def show_main_menu_text(message: Message):
    await message.answer(
        "🖥️ <b>Панель управления ПК</b>\n\nВыберите категорию:",
        reply_markup=get_main_inline_keyboard(),
        parse_mode="HTML"
    )

# Быстрый скриншот через Reply-клавиатуру
@router.message(F.text == "📸 Скриншот")
async def make_screenshot_text(message: Message):
    try:
        screenshot = pyautogui.screenshot()
        from io import BytesIO
        img_buffer = BytesIO()
        screenshot.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        
        photo = BufferedInputFile(img_buffer.getvalue(), filename="screenshot.png")
        await message.answer_photo(photo, caption="📸 Текущий снимок экрана")
    except Exception as e:
        await message.answer(f"Ошибка при создании скриншота: {e}")

# Навигация: Главное меню
@router.callback_query(F.data == "menu_main")
async def callback_menu_main(callback: CallbackQuery):
    await safe_edit_text(
        callback.message,
        "🖥️ <b>Панель управления ПК</b>\n\nВыберите категорию:",
        get_main_inline_keyboard()
    )
    await callback.answer()

# Навигация: Меню системы
@router.callback_query(F.data == "menu_system")
async def callback_menu_system(callback: CallbackQuery):
    await safe_edit_text(
        callback.message,
        "⚙️ <b>Управление системой</b>\n\nВыберите действие с системой:",
        get_system_inline_keyboard()
    )
    await callback.answer()

# Навигация: Меню мультимедиа
@router.callback_query(F.data == "menu_media")
async def callback_menu_media(callback: CallbackQuery):
    await safe_edit_text(
        callback.message,
        "🎵 <b>Управление мультимедиа</b>\n\nИспользуйте кнопки ниже для управления плеерами и громкостью звука на ПК:",
        get_media_inline_keyboard()
    )
    await callback.answer()

# Навигация: Меню запуска программ
@router.callback_query(F.data == "menu_launch")
async def callback_menu_launch(callback: CallbackQuery):
    await safe_edit_text(
        callback.message,
        "🚀 <b>Запуск программ</b>\n\nВыберите программу для запуска:",
        get_launch_inline_keyboard()
    )
    await callback.answer()

# Навигация: Меню закрытия программ
@router.callback_query(F.data == "menu_close")
async def callback_menu_close(callback: CallbackQuery):
    await safe_edit_text(
        callback.message,
        "❌ <b>Закрытие программ</b>\n\nВыберите программу для принудительного закрытия или закройте активное окно:",
        get_close_inline_keyboard()
    )
    await callback.answer()

# Системное действие: Выключение
@router.callback_query(F.data == "sys_shutdown")
async def callback_shutdown(callback: CallbackQuery):
    try:
        subprocess.Popen(["shutdown", "/s", "/t", "0"])
        await callback.answer("Компьютер выключается!", show_alert=True)
    except Exception as e:
        await callback.answer(f"Ошибка выключения: {e}", show_alert=True)

# Системное действие: Сон
@router.callback_query(F.data == "sys_sleep")
async def callback_sleep(callback: CallbackQuery):
    try:
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"])
        await callback.answer("Компьютер переходит в спящий режим!")
    except Exception as e:
        await callback.answer(f"Ошибка спящего режима: {e}", show_alert=True)

# Системное действие: Блокировка
@router.callback_query(F.data == "sys_lock")
async def callback_lock(callback: CallbackQuery):
    try:
        subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
        await callback.answer("Компьютер заблокирован!")
    except Exception as e:
        await callback.answer(f"Ошибка блокировки ПК: {e}", show_alert=True)

# Системное действие: Диспетчер задач
@router.callback_query(F.data == "sys_taskmgr")
async def callback_taskmgr(callback: CallbackQuery):
    try:
        pyautogui.keyDown('ctrl')
        pyautogui.keyDown('shift')
        pyautogui.keyDown('esc')
        pyautogui.keyUp('ctrl')
        pyautogui.keyUp('shift')
        pyautogui.keyUp('esc')
        await callback.answer("Диспетчер задач успешно запущен!")
    except Exception as e:
        await callback.answer(f"Ошибка Диспетчера задач: {e}", show_alert=True)

# Системное действие: Закрыть активное окно
@router.callback_query(F.data == "sys_close_active")
async def callback_close_active(callback: CallbackQuery):
    try:
        pyautogui.hotkey('alt', 'f4')
        await callback.answer("Активное окно закрыто (Alt+F4)")
    except Exception as e:
        await callback.answer(f"Ошибка при закрытии окна: {e}", show_alert=True)

# Системное действие: Скриншот
@router.callback_query(F.data == "sys_screenshot")
async def callback_screenshot(callback: CallbackQuery):
    await callback.answer("Делаю скриншот...")
    try:
        screenshot = pyautogui.screenshot()
        from io import BytesIO
        img_buffer = BytesIO()
        screenshot.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        
        photo = BufferedInputFile(img_buffer.getvalue(), filename="screenshot.png")
        await callback.message.answer_photo(photo, caption="📸 Текущий снимок экрана")
    except Exception as e:
        await callback.message.answer(f"Ошибка при создании скриншота: {e}")

# Мультимедиа: Предыдущий трек
@router.callback_query(F.data == "media_prev")
async def callback_media_prev(callback: CallbackQuery):
    try:
        pyautogui.press('prevtrack')
        await callback.answer("⏮️ Предыдущий трек")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

# Мультимедиа: Плей / Пауза
@router.callback_query(F.data == "media_play")
async def callback_media_play(callback: CallbackQuery):
    try:
        pyautogui.press('playpause')
        await callback.answer("⏯️ Воспроизведение / Пауза")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

# Мультимедиа: Следующий трек
@router.callback_query(F.data == "media_next")
async def callback_media_next(callback: CallbackQuery):
    try:
        pyautogui.press('nexttrack')
        await callback.answer("⏭️ Следующий трек")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

# Мультимедиа: Громкость тише
@router.callback_query(F.data == "media_voldown")
async def callback_media_voldown(callback: CallbackQuery):
    try:
        pyautogui.press('volumedown')
        await callback.answer("🔉 Звук тише (-2%)")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

# Мультимедиа: Выключение звука
@router.callback_query(F.data == "media_mute")
async def callback_media_mute(callback: CallbackQuery):
    try:
        pyautogui.press('volumemute')
        await callback.answer("🔇 Вкл/Выкл звук")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

# Мультимедиа: Громкость громче
@router.callback_query(F.data == "media_volumeup")
async def callback_media_volumeup(callback: CallbackQuery):
    try:
        pyautogui.press('volumeup')
        await callback.answer("🔊 Звук громче (+2%)")
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

# Запуск программы
@router.callback_query(F.data.startswith("run_"))
async def callback_run_program(callback: CallbackQuery):
    idx = int(callback.data.split("_")[1])
    programs = load_programs()
    if idx < 0 or idx >= len(programs):
        await callback.answer("Программа не найдена в списке.", show_alert=True)
        return
        
    prog = programs[idx]
    name = prog.get("name")
    path = prog.get("path")
    process = prog.get("process")
    
    if not path:
        await callback.answer(f"Путь для {name} не настроен.", show_alert=True)
        return
        
    try:
        expanded_path = os.path.expandvars(path)
        try:
            os.startfile(expanded_path)
        except AttributeError:
            subprocess.Popen(expanded_path, shell=True)
            
        await callback.answer(f"Запуск: {name}")
        
        # Создаем Inline-кнопку для моментального закрытия этой же программы
        builder = InlineKeyboardBuilder()
        if process:
            builder.row(InlineKeyboardButton(text=f"❌ Закрыть {name}", callback_data=f"killback_{idx}"))
        else:
            builder.row(InlineKeyboardButton(text="⏹️ Закрыть активное окно (Alt+F4)", callback_data="sys_close_active_and_back"))
            
        builder.row(
            InlineKeyboardButton(text="🚀 К списку программ", callback_data="menu_launch"),
            InlineKeyboardButton(text="◀️ Назад в меню", callback_data="menu_main")
        )
        
        await safe_edit_text(
            callback.message,
            f"🚀 <b>Программа {name} запущена!</b>\n\nВы можете закрыть её прямо сейчас с помощью кнопки ниже или вернуться в меню.",
            builder.as_markup()
        )
    except Exception as e:
        logger.error(f"Ошибка при запуске {name} ({path}): {e}")
        await callback.answer(f"Ошибка запуска: {e}", show_alert=True)

# Закрытие программы
@router.callback_query(F.data.startswith("killproc_"))
async def callback_kill_program(callback: CallbackQuery):
    idx = int(callback.data.split("_")[1])
    programs = load_programs()
    if idx < 0 or idx >= len(programs):
        await callback.answer("Программа не найдена в списке.", show_alert=True)
        return
        
    prog = programs[idx]
    name = prog.get("name")
    process = prog.get("process")
    
    if not process:
        await callback.answer(f"Имя процесса для {name} не настроено.", show_alert=True)
        return
        
    try:
        success = False
        error_msg = ""
        
        # 1. Пробуем стандартный taskkill
        result = subprocess.run(
            ["taskkill", "/f", "/im", process],
            capture_output=True,
            text=True,
            shell=True
        )
        if result.returncode == 0:
            success = True
        else:
            error_msg = result.stderr.strip() or result.stdout.strip()
            
        # 2. Если не удалось (например, отказано в доступе для Task Manager), пробуем tskill
        if not success:
            proc_no_ext = process[:-4] if process.lower().endswith(".exe") else process
            result_tskill = subprocess.run(
                ["tskill", proc_no_ext],
                capture_output=True,
                text=True,
                shell=True
            )
            if result_tskill.returncode == 0:
                success = True

        if success:
            await callback.answer(f"Процесс {process} завершен.")
            await safe_edit_text(
                callback.message,
                f"❌ <b>Закрытие программ</b>\n\nПроцесс <b>{process}</b> ({name}) успешно завершен!\n\nВыберите программу для закрытия:",
                get_close_inline_keyboard()
            )
        else:
            if "не найден" in error_msg.lower() or "not found" in error_msg.lower():
                await callback.answer(f"Процесс {process} не найден (возможно, уже закрыт).", show_alert=True)
            else:
                await callback.answer(f"Ошибка при закрытии {process}: {error_msg}", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при закрытии процесса {process}: {e}")
        await callback.answer(f"Ошибка при завершении процесса: {e}", show_alert=True)

# Быстрое закрытие программы сразу после запуска и возврат к списку
@router.callback_query(F.data.startswith("killback_"))
async def callback_kill_and_back(callback: CallbackQuery):
    idx = int(callback.data.split("_")[1])
    programs = load_programs()
    if idx < 0 or idx >= len(programs):
        await callback.answer("Программа не найдена в списке.", show_alert=True)
        return
        
    prog = programs[idx]
    name = prog.get("name")
    process = prog.get("process")
    
    if not process:
        await callback.answer(f"Имя процесса для {name} не настроено.", show_alert=True)
        return
        
    try:
        success = False
        error_msg = ""
        
        # 1. Пробуем taskkill
        result = subprocess.run(
            ["taskkill", "/f", "/im", process],
            capture_output=True,
            text=True,
            shell=True
        )
        if result.returncode == 0:
            success = True
        else:
            error_msg = result.stderr.strip() or result.stdout.strip()
            
        # 2. Пробуем tskill
        if not success:
            proc_no_ext = process[:-4] if process.lower().endswith(".exe") else process
            result_tskill = subprocess.run(
                ["tskill", proc_no_ext],
                capture_output=True,
                text=True,
                shell=True
            )
            if result_tskill.returncode == 0:
                success = True
                
        if success:
            await callback.answer(f"Программа {name} закрыта.")
            # Возврат к списку запуска
            await safe_edit_text(
                callback.message,
                f"🚀 <b>Запуск программ</b>\n\nПрограмма <b>{name}</b> была успешно закрыта.\n\nВыберите программу для запуска:",
                get_launch_inline_keyboard()
            )
        else:
            if "не найден" in error_msg.lower() or "not found" in error_msg.lower():
                await callback.answer(f"Процесс {process} не найден (возможно, уже закрыт).", show_alert=True)
            else:
                await callback.answer(f"Ошибка при закрытии {process}: {error_msg}", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при закрытии в kill_and_back: {e}")
        await callback.answer(f"Ошибка: {e}", show_alert=True)

# Закрытие активного окна сразу после запуска и возврат к списку
@router.callback_query(F.data == "sys_close_active_and_back")
async def callback_close_active_and_back(callback: CallbackQuery):
    try:
        pyautogui.hotkey('alt', 'f4')
        await callback.answer("Активное окно закрыто (Alt+F4)")
        await safe_edit_text(
            callback.message,
            "🚀 <b>Запуск программ</b>\n\nАктивное окно закрыто.\n\nВыберите программу для запуска:",
            get_launch_inline_keyboard()
        )
    except Exception as e:
        await callback.answer(f"Ошибка при закрытии окна: {e}", show_alert=True)

# ==========================================
# Обработчики совместимости для старых кнопок
# ==========================================

@router.message(F.text.in_(["Основное", "Вернуться в главное меню"]))
async def compatibility_main_menu(message: Message):
    await message.answer(
        "🖥️ <b>Панель управления ПК</b>\n\nВыберите категорию:",
        reply_markup=get_main_inline_keyboard(),
        parse_mode="HTML"
    )
    await message.answer("Ваша клавиатура обновлена на новую.", reply_markup=get_reply_keyboard())

@router.message(F.text == "Программы")
async def compatibility_programs_menu(message: Message):
    await message.answer(
        "🚀 <b>Запуск программ</b>\n\nВыберите программу для запуска:",
        reply_markup=get_launch_inline_keyboard(),
        parse_mode="HTML"
    )
    await message.answer("Ваша клавиатура обновлена на новую.", reply_markup=get_reply_keyboard())

@router.message(F.text == "Скриншот")
async def compatibility_screenshot(message: Message):
    try:
        screenshot = pyautogui.screenshot()
        from io import BytesIO
        img_buffer = BytesIO()
        screenshot.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        
        photo = BufferedInputFile(img_buffer.getvalue(), filename="screenshot.png")
        await message.answer_photo(photo, caption="📸 Текущий снимок экрана")
        await message.answer("Клавиатура обновлена.", reply_markup=get_reply_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка при создании скриншота: {e}", reply_markup=get_reply_keyboard())

@router.message(F.text == "Выкл")
async def compatibility_shutdown(message: Message):
    try:
        subprocess.Popen(["shutdown", "/s", "/t", "0"])
        await message.answer("Компьютер выключается!", reply_markup=get_reply_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка выключения ПК: {e}", reply_markup=get_reply_keyboard())

@router.message(F.text == "Сон")
async def compatibility_sleep(message: Message):
    try:
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0", "1", "0"])
        await message.answer("Компьютер переходит в спящий режим!", reply_markup=get_reply_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка спящего режима: {e}", reply_markup=get_reply_keyboard())

@router.message(F.text == "Блокировка")
async def compatibility_lock(message: Message):
    try:
        subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"])
        await message.answer("Компьютер заблокирован!", reply_markup=get_reply_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка блокировки ПК: {e}", reply_markup=get_reply_keyboard())

@router.message(F.text == "Диспетчер задач")
async def compatibility_taskmgr(message: Message):
    try:
        pyautogui.keyDown('ctrl')
        pyautogui.keyDown('shift')
        pyautogui.keyDown('esc')
        pyautogui.keyUp('ctrl')
        pyautogui.keyUp('shift')
        pyautogui.keyUp('esc')
        await message.answer("Диспетчер задач успешно запущен!", reply_markup=get_reply_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка запуска Диспетчера задач: {e}", reply_markup=get_reply_keyboard())

@router.message(F.text == "Закрыть программу")
async def compatibility_close_program(message: Message):
    try:
        pyautogui.hotkey('alt', 'f4')
        await message.answer("Активное окно закрыто (Alt+F4)", reply_markup=get_reply_keyboard())
    except Exception as e:
        await message.answer(f"Ошибка при закрытии окна: {e}", reply_markup=get_reply_keyboard())

@router.message(F.text.in_([str(i) for i in range(1, 10)]))
async def compatibility_digits(message: Message):
    await message.answer(
        f"Устаревший способ запуска цифрой ({message.text}).\n"
        f"Пожалуйста, используйте кнопку <b>Запуск программ</b> в меню для выбора.",
        reply_markup=get_reply_keyboard(),
        parse_mode="HTML"
    )

# Главная функция запуска бота
async def main():
    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_telegram_bot_token_here":
        logger.critical("Критическая ошибка: Токен бота не настроен в файле .env!")
        sys.exit(1)

    # Инициализация бота и диспетчера
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    
    # Подключение Middleware проверки авторизации для сообщений и callback-кнопок
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    
    # Регистрация обработчиков
    dp.include_router(router)
    
    logger.info("Бот запущен и готов к приему сообщений...")
    
    # Отправка уведомления администраторам о запуске ПК (с защитой от спама)
    if should_send_startup_notification():
        for user_id in config.ALLOWED_USERS:
            try:
                # 1. Отправляем уведомление с Reply-клавиатурой
                await bot.send_message(
                    chat_id=user_id,
                    text="💻 <b>Компьютер успешно запущен!</b>\n\nAsya Pc Bot готов к работе.",
                    reply_markup=get_reply_keyboard(),
                    parse_mode="HTML"
                )
                # 2. Сразу отправляем встроенную (Inline) панель управления
                await bot.send_message(
                    chat_id=user_id,
                    text="🖥️ <b>Панель управления ПК</b>\n\nВыберите категорию:",
                    reply_markup=get_main_inline_keyboard(),
                    parse_mode="HTML"
                )
                logger.info(f"Отправлено уведомление о запуске ПК и меню администратору {user_id}")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление о запуске администратору {user_id}: {e}")
            
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    # Блокируем запуск дубликатов на уровне процесса
    acquire_lock()
    
    retry_interval = 10  # интервал повтора (секунд)
    max_startup_time = 180  # лимит ожидания сети (3 минуты)
    start_attempt_time = time.time()
    
    while True:
        run_start = time.time()
        try:
            asyncio.run(main())
            # Выходим из цикла, если завершено пользователем без ошибок
            break
        except KeyboardInterrupt:
            logger.info("Бот остановлен пользователем.")
            break
        except Exception as e:
            # Если бот успешно проработал больше 60 секунд, сбрасываем время начала попыток
            if time.time() - run_start > 60:
                logger.info("Сессия была стабильной (>60 сек). Сбрасываем таймер коулдауна попыток запуска.")
                start_attempt_time = time.time()
                
            elapsed = time.time() - start_attempt_time
            if elapsed < max_startup_time:
                logger.warning(
                    f"Ошибка при запуске или работе бота: {e}. "
                    f"Повторный запуск через {retry_interval} сек. "
                    f"(Попытки запуска: {int(elapsed)}/{max_startup_time} сек)..."
                )
                time.sleep(retry_interval)
            else:
                logger.critical(
                    f"Критическая ошибка: не удалось запустить бота в течение {max_startup_time} секунд. "
                    f"Последняя ошибка: {e}"
                )
                sys.exit(1)