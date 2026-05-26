import os
import sys
import json
import asyncio
import threading
import logging
import importlib
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image
import pystray
from pystray import MenuItem as item, Menu

# Настройка системного пути
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config
import bot as importbot

# =====================================================================
# ЦВЕТОВАЯ ПАЛИТРА MATERIAL DESIGN 3 (DARK THEME)
# =====================================================================
M3_BG = "#141218"                  # Основной фон приложения (Surface/Background)
M3_SURFACE_LOW = "#1d1b20"         # Фон бокового меню (Surface Container Low)
M3_SURFACE_CONTAINER = "#211f26"   # Фон карточек и полей ввода (Surface Container)
M3_SURFACE_HIGH = "#2b2930"        # Цвет для ховера и выделения (Surface Container High)
M3_PRIMARY = "#d0bcff"             # Акцентный цвет кнопок / активной вкладки (Primary Light Purple)
M3_ON_PRIMARY = "#381e72"          # Текст на активных элементах (On Primary Dark Purple)
M3_PRIMARY_CONTAINER = "#4f378b"   # Контейнер для второстепенных кнопок
M3_ON_PRIMARY_CONTAINER = "#eaddff"# Текст на второстепенных кнопках
M3_TEXT = "#e6e1e5"                # Цвет основного текста (On Surface)
M3_TEXT_MUTED = "#938f99"          # Цвет подсказок и неактивного текста (Outline)
M3_GREEN = "#b5f2b8"               # Цвет статуса "Запущен" (Success)
M3_ON_GREEN = "#003917"            # Текст на зеленом фоне
M3_RED = "#ffb4ab"                 # Цвет статуса "Остановлен" / ошибки (Error)
M3_ON_RED = "#690005"              # Текст на красном фоне

# Установка базовой темы CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")  # Базовый синий переопределяется нашими цветами

# Блокировка повторного запуска GUI
try:
    import msvcrt
    has_msvcrt = True
except ImportError:
    has_msvcrt = False

gui_lock_file = None
def acquire_gui_lock() -> bool:
    global gui_lock_file
    if not has_msvcrt:
        return True
    lock_path = os.path.join(config.BASE_DIR, "gui.lock")
    try:
        gui_lock_file = open(lock_path, "w")
        msvcrt.locking(gui_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except (IOError, OSError):
        return False

# Кастомный обработчик логирования для вывода в текстовое поле GUI
class GuiLogHandler(logging.Handler):
    def __init__(self, textbox):
        super().__init__()
        self.textbox = textbox
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))

    def emit(self, record):
        msg = self.format(record)
        def append():
            try:
                self.textbox.configure(state="normal")
                self.textbox.insert("end", msg + "\n")
                self.textbox.see("end")
                self.textbox.configure(state="disabled")
            except Exception:
                pass
        self.textbox.after(0, append)

# Класс для фонового запуска бота в asyncio loop
class BotRunner:
    def __init__(self, log_callback, status_callback, restart_callback, quit_callback):
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.restart_callback = restart_callback
        self.quit_callback = quit_callback
        self.loop = None
        self.thread = None
        self.bot = None
        self.dp = None
        self.is_running = False

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def _run_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.status_callback("RUNNING")
        
        exit_code = None
        try:
            self.loop.run_until_complete(self._main_async())
        except SystemExit as se:
            exit_code = se.code
        except Exception as e:
            self.log_callback(f"Ошибка в работе бота: {e}\n")
        finally:
            self.status_callback("STOPPED")
            self.is_running = False
            
            # Обработка команд завершения от Telegram
            if exit_code == 42:
                self.log_callback("Получен запрос на перезапуск бота из Telegram. Перезапускаю...\n")
                self.restart_callback()
            elif exit_code == 0:
                self.log_callback("Получен запрос на выключение бота из Telegram. Завершаю работу...\n")
                self.quit_callback()

    async def _main_async(self):
        # Перезагружаем конфигурацию, чтобы обновить переменные из .env
        importlib.reload(config)
        
        if not config.BOT_TOKEN or config.BOT_TOKEN == "your_telegram_bot_token_here" or not config.BOT_TOKEN.strip():
            self.log_callback("Критическая ошибка: Токен бота не настроен в .env!\n")
            raise Exception("Токен бота не настроен!")

        if not config.ALLOWED_USERS:
            self.log_callback("Предупреждение: Список ALLOWED_USERS пуст! Бот не примет команды.\n")

        self.bot = importbot.Bot(token=config.BOT_TOKEN)
        self.dp = importbot.Dispatcher()
        
        # Подключаем Middleware и маршрутизаторы
        self.dp.message.middleware(importbot.AuthMiddleware())
        self.dp.callback_query.middleware(importbot.AuthMiddleware())
        self.dp.include_router(importbot.router)
        
        self.log_callback("Запуск опроса бота (aiogram polling)...\n")
        
        # Отправка уведомления о запуске (с защитой от спама)
        if importbot.should_send_startup_notification():
            for user_id in config.ALLOWED_USERS:
                try:
                    await self.bot.send_message(
                        chat_id=user_id,
                        text="💻 <b>Компьютер успешно запущен!</b>\n\nAsya Pc Bot готов к работе.",
                        reply_markup=importbot.get_reply_keyboard(),
                        parse_mode="HTML"
                    )
                    await self.bot.send_message(
                        chat_id=user_id,
                        text="🖥️ <b>Панель управления ПК</b>\n\nВыберите категорию:",
                        reply_markup=importbot.get_main_inline_keyboard(),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    self.log_callback(f"Не удалось отправить уведомление о старте пользователю {user_id}: {e}\n")

        try:
            await self.dp.start_polling(self.bot)
        finally:
            await self.bot.session.close()

    def stop(self):
        if not self.is_running or not self.loop:
            return
        self.log_callback("Запрос на остановку бота...\n")
        
        async def shutdown():
            if self.dp:
                await self.dp.stop_polling()
            if self.bot:
                await self.bot.session.close()
                
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop)

# Главный класс приложения GUI
class AsyaPcBotApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Asya PC Bot [Остановлен]")
        self.geometry("800x580")
        self.resizable(False, False)
        self.configure(fg_color=M3_BG)
        
        self.icon_path = os.path.join(config.BASE_DIR, "icon.ico")
        if os.path.exists(self.icon_path):
            self.iconbitmap(self.icon_path)
            
        self.bot_runner = BotRunner(self.write_log, self.update_bot_status, self.restart_bot, self.quit_app)
        self.tray_icon = None
        self.tray_thread = None
        self.is_quitting = False
        
        self.setup_ui()
        self.setup_tray()
        
        # Подгружаем настройки на экран
        self.load_settings_to_ui()
        self.refresh_programs_list()
        
        # Перехват логов
        gui_log_handler = GuiLogHandler(self.textbox_logs)
        logging.getLogger().addHandler(gui_log_handler)
        
        self.protocol("WM_DELETE_WINDOW", self.on_close_event)
        
        # Поддержка запуска в свернутом виде (для автозагрузки)
        if "--minimized" in sys.argv or "-m" in sys.argv:
            self.withdraw()
            self.write_log("Запущено в свернутом режиме (в трее).\n")
            
        # Автозапуск бота при старте приложения
        self.after(500, self.auto_start_bot_on_launch)

    def setup_ui(self):
        # -------------------------------------------------------------
        # Боковая панель навигации (Navigation Rail) в стиле Material 3
        # -------------------------------------------------------------
        self.frame_rail = ctk.CTkFrame(self, width=200, fg_color=M3_SURFACE_LOW, corner_radius=0)
        self.frame_rail.pack(side="left", fill="y")
        self.frame_rail.pack_propagate(False)
        
        # Заголовок приложения
        self.lbl_logo = ctk.CTkLabel(
            self.frame_rail, 
            text="Asya PC Bot", 
            text_color=M3_PRIMARY, 
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold")
        )
        self.lbl_logo.pack(padx=15, pady=(30, 5))
        
        self.lbl_version = ctk.CTkLabel(
            self.frame_rail, 
            text="Версия 2.0", 
            text_color=M3_TEXT_MUTED, 
            font=ctk.CTkFont(family="Segoe UI", size=11, slant="italic")
        )
        self.lbl_version.pack(padx=15, pady=(0, 30))
        
        # Навигационные кнопки
        self.nav_buttons = {}
        
        self.btn_nav_settings = ctk.CTkButton(
            self.frame_rail, text="⚙️  Настройки", height=45, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=lambda: self.switch_tab("settings")
        )
        self.btn_nav_settings.pack(fill="x", padx=12, pady=8)
        self.nav_buttons["settings"] = self.btn_nav_settings
        
        self.btn_nav_programs = ctk.CTkButton(
            self.frame_rail, text="🚀  Программы", height=45, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=lambda: self.switch_tab("programs")
        )
        self.btn_nav_programs.pack(fill="x", padx=12, pady=8)
        self.nav_buttons["programs"] = self.btn_nav_programs
        
        self.btn_nav_logs = ctk.CTkButton(
            self.frame_rail, text="📊  Консоль логов", height=45, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=lambda: self.switch_tab("logs")
        )
        self.btn_nav_logs.pack(fill="x", padx=12, pady=8)
        self.nav_buttons["logs"] = self.btn_nav_logs
        
        # -------------------------------------------------------------
        # Правая основная область контента
        # -------------------------------------------------------------
        self.frame_content = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_content.pack(side="right", fill="both", expand=True)
        
        # Панель статуса и запуска (Сверху) в стиле Material 3
        self.frame_top = ctk.CTkFrame(self.frame_content, height=80, fg_color=M3_SURFACE_CONTAINER, corner_radius=16)
        self.frame_top.pack(fill="x", padx=20, pady=20)
        self.frame_top.pack_propagate(False)
        
        self.label_status_title = ctk.CTkLabel(
            self.frame_top, text="Статус работы ПК:", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="medium")
        )
        self.label_status_title.pack(side="left", padx=20, pady=25)
        
        self.label_status = ctk.CTkLabel(
            self.frame_top, text="● Остановлен", 
            text_color=M3_RED, 
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
        )
        self.label_status.pack(side="left", padx=5, pady=25)
        
        self.btn_toggle_bot = ctk.CTkButton(
            self.frame_top, text="Запустить", 
            fg_color=M3_PRIMARY, 
            text_color=M3_ON_PRIMARY,
            hover_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            width=140, 
            height=38,
            corner_radius=19,
            command=self.toggle_bot_state
        )
        self.btn_toggle_bot.pack(side="right", padx=20, pady=21)
        
        # Создание фреймов-вкладок
        self.content_frames = {}
        
        # --- Вкладка Настройки ---
        self.frame_settings = ctk.CTkFrame(self.frame_content, fg_color="transparent")
        self.content_frames["settings"] = self.frame_settings
        
        self.lbl_set_title = ctk.CTkLabel(self.frame_settings, text="Параметры окружения (.env)", text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"))
        self.lbl_set_title.pack(anchor="w", padx=10, pady=(10, 15))
        
        self.label_token = ctk.CTkLabel(self.frame_settings, text="Telegram Bot Token (BOT_TOKEN):", text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        self.label_token.pack(anchor="w", padx=10, pady=(5, 2))
        
        self.entry_token = ctk.CTkEntry(
            self.frame_settings, fg_color=M3_SURFACE_CONTAINER, border_color=M3_TEXT_MUTED,
            text_color=M3_TEXT, placeholder_text_color=M3_TEXT_MUTED, corner_radius=8, height=35
        )
        self.entry_token.pack(fill="x", padx=10, pady=5)
        
        self.label_users = ctk.CTkLabel(self.frame_settings, text="Разрешенные ID администраторов (ALLOWED_USERS):", text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        self.label_users.pack(anchor="w", padx=10, pady=(15, 2))
        
        self.entry_users = ctk.CTkEntry(
            self.frame_settings, fg_color=M3_SURFACE_CONTAINER, border_color=M3_TEXT_MUTED,
            text_color=M3_TEXT, placeholder_text_color=M3_TEXT_MUTED, corner_radius=8, height=35
        )
        self.entry_users.pack(fill="x", padx=10, pady=5)
        
        self.label_tip = ctk.CTkLabel(
            self.frame_settings, 
            text="* Бот обрабатывает команды ТОЛЬКО от указанных пользователей для безопасности ПК.", 
            text_color=M3_TEXT_MUTED, 
            font=ctk.CTkFont(family="Segoe UI", size=11, slant="italic")
        )
        self.label_tip.pack(anchor="w", padx=10, pady=5)
        
        self.btn_save_settings = ctk.CTkButton(
            self.frame_settings, text="Сохранить настройки", 
            fg_color=M3_PRIMARY_CONTAINER, 
            text_color=M3_ON_PRIMARY_CONTAINER,
            hover_color=M3_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            width=220, 
            height=42,
            corner_radius=21,
            command=self.save_settings_from_ui
        )
        self.btn_save_settings.pack(pady=40)
        
        # --- Вкладка Программы ---
        self.frame_programs = ctk.CTkFrame(self.frame_content, fg_color="transparent")
        self.content_frames["programs"] = self.frame_programs
        
        self.lbl_prog_title = ctk.CTkLabel(self.frame_programs, text="Список запускаемых программ (programs.json)", text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"))
        self.lbl_prog_title.pack(anchor="w", padx=10, pady=(10, 10))
        
        # Форма добавления
        self.frame_add_prog = ctk.CTkFrame(self.frame_programs, fg_color=M3_SURFACE_CONTAINER, corner_radius=12, height=95)
        self.frame_add_prog.pack(fill="x", padx=10, pady=5)
        
        # Сетка формы
        self.entry_prog_name = ctk.CTkEntry(self.frame_add_prog, width=130, height=32, corner_radius=6, placeholder_text="Имя (например, Chrome)")
        self.entry_prog_name.grid(row=0, column=0, padx=8, pady=12)
        
        self.entry_prog_path = ctk.CTkEntry(self.frame_add_prog, width=190, height=32, corner_radius=6, placeholder_text="Полный путь к .exe файлу")
        self.entry_prog_path.grid(row=0, column=1, padx=4, pady=12)
        
        self.btn_browse = ctk.CTkButton(
            self.frame_add_prog, text="Обзор", fg_color=M3_PRIMARY_CONTAINER, text_color=M3_ON_PRIMARY_CONTAINER, hover_color=M3_PRIMARY,
            width=65, height=32, corner_radius=6, command=self.browse_executable
        )
        self.btn_browse.grid(row=0, column=2, padx=4, pady=12)
        
        self.entry_prog_process = ctk.CTkEntry(self.frame_add_prog, width=120, height=32, corner_radius=6, placeholder_text="Имя процесса (.exe)")
        self.entry_prog_process.grid(row=0, column=3, padx=4, pady=12)
        
        self.btn_add_prog = ctk.CTkButton(
            self.frame_add_prog, text="Добавить", fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            width=70, height=32, corner_radius=6, command=self.add_program_from_ui
        )
        self.btn_add_prog.grid(row=0, column=4, padx=8, pady=12)
        
        # Список с прокруткой
        self.scroll_progs = ctk.CTkScrollableFrame(self.frame_programs, fg_color="transparent", height=230)
        self.scroll_progs.pack(fill="both", expand=True, padx=5, pady=10)
        
        # --- Вкладка Логи ---
        self.frame_logs = ctk.CTkFrame(self.frame_content, fg_color="transparent")
        self.content_frames["logs"] = self.frame_logs
        
        self.lbl_log_title = ctk.CTkLabel(self.frame_logs, text="Логи работы и системные события", text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"))
        self.lbl_log_title.pack(anchor="w", padx=10, pady=(10, 10))
        
        self.textbox_logs = ctk.CTkTextbox(
            self.frame_logs, fg_color=M3_SURFACE_CONTAINER, text_color=M3_TEXT, border_color=M3_TEXT_MUTED, border_width=1,
            font=ctk.CTkFont(family="Consolas", size=11), corner_radius=12
        )
        self.textbox_logs.pack(fill="both", expand=True, padx=10, pady=5)
        self.textbox_logs.configure(state="disabled")
        
        self.btn_clear_logs = ctk.CTkButton(
            self.frame_logs, text="Очистить терминал", 
            fg_color=M3_SURFACE_CONTAINER, 
            text_color=M3_TEXT,
            hover_color=M3_SURFACE_HIGH,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            width=160, 
            height=36,
            corner_radius=18,
            command=self.clear_logs
        )
        self.btn_clear_logs.pack(pady=12)
        
        # Дефолтная активная вкладка
        self.switch_tab("settings")

    def switch_tab(self, tab_name: str):
        # Переключение визуальных стилей вкладок
        for name, button in self.nav_buttons.items():
            if name == tab_name:
                button.configure(fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color=M3_TEXT)
            else:
                button.configure(fg_color="transparent", text_color=M3_TEXT, hover_color=M3_SURFACE_HIGH)
                
        # Прячем все вкладки
        for frame in self.content_frames.values():
            frame.pack_forget()
            
        # Показываем выбранную вкладку
        self.content_frames[tab_name].pack(fill="both", expand=True, padx=20, pady=(0, 20))

    # --- Функции Управления Ботом ---
    def toggle_bot_state(self):
        if self.bot_runner.is_running:
            self.bot_runner.stop()
        else:
            token = self.entry_token.get().strip()
            if not token:
                messagebox.showerror("Ошибка", "Заполните поле Telegram Bot Token во вкладке Настройки!")
                self.switch_tab("settings")
                return
            
            self.save_settings_from_ui(silent=True)
            self.btn_toggle_bot.configure(state="disabled")
            self.bot_runner.start()

    def update_bot_status(self, status: str):
        def update():
            self.btn_toggle_bot.configure(state="normal")
            if status == "RUNNING":
                self.label_status.configure(text="● Запущен", text_color=M3_GREEN)
                self.title("Asya PC Bot [Запущен]")
                self.btn_toggle_bot.configure(text="Остановить", fg_color=M3_RED, text_color=M3_ON_RED, hover_color=M3_TEXT)
            else:
                self.label_status.configure(text="● Остановлен", text_color=M3_RED)
                self.title("Asya PC Bot [Остановлен]")
                self.btn_toggle_bot.configure(text="Запустить", fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color=M3_TEXT)
                
                # При остановке убираем значок с панели задач (сворачиваем в трей)
                if not self.is_quitting:
                    self.withdraw()
                    self.write_log("Бот остановлен. Окно автоматически скрыто в системный трей.\n")
        self.after(0, update)

    def restart_bot(self):
        def restart():
            self.write_log("Выполняю перезапуск бота...\n")
            self.toggle_bot_state()  # Останавливаем
            self.after(2000, self.toggle_bot_state)  # Через 2 секунды запускаем
        self.after(0, restart)

    def write_log(self, text: str):
        def append():
            self.textbox_logs.configure(state="normal")
            self.textbox_logs.insert("end", text)
            self.textbox_logs.see("end")
            self.textbox_logs.configure(state="disabled")
        self.after(0, append)

    def auto_start_bot_on_launch(self):
        token = self.entry_token.get().strip()
        if token and token != "your_telegram_bot_token_here" and token.strip():
            self.write_log("Выполняю автозапуск бота на старте...\n")
            self.toggle_bot_state()

    def clear_logs(self):
        self.textbox_logs.configure(state="normal")
        self.textbox_logs.delete("1.0", "end")
        self.textbox_logs.configure(state="disabled")

    # --- Функции Управления Настройками ---
    def load_settings_to_ui(self):
        env_path = os.path.join(config.BASE_DIR, ".env")
        token = ""
        allowed_users = ""
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("BOT_TOKEN="):
                            token = line.split("=", 1)[1]
                        elif line.startswith("ALLOWED_USERS="):
                            allowed_users = line.split("=", 1)[1]
            except Exception as e:
                self.write_log(f"Не удалось загрузить настройки .env: {e}\n")
                
        self.entry_token.delete(0, "end")
        self.entry_token.insert(0, token)
        self.entry_users.delete(0, "end")
        self.entry_users.insert(0, allowed_users)

    def save_settings_from_ui(self, silent=False):
        token = self.entry_token.get().strip()
        users = self.entry_users.get().strip()
        
        env_path = os.path.join(config.BASE_DIR, ".env")
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(f"BOT_TOKEN={token}\n")
                f.write(f"ALLOWED_USERS={users}\n")
            self.write_log("Настройки .env успешно сохранены!\n")
            if not silent:
                messagebox.showinfo("Успех", "Настройки сохранены успешно!")
        except Exception as e:
            self.write_log(f"Ошибка сохранения настроек: {e}\n")
            if not silent:
                messagebox.showerror("Ошибка", f"Не удалось сохранить настройки: {e}")

    # --- Функции Управления Программами ---
    def load_programs_list(self) -> list:
        programs_path = os.path.join(config.BASE_DIR, "programs.json")
        if os.path.exists(programs_path):
            try:
                with open(programs_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self.write_log(f"Ошибка чтения json программ: {e}\n")
                return []
        return []

    def save_programs_list(self, programs: list):
        programs_path = os.path.join(config.BASE_DIR, "programs.json")
        try:
            with open(programs_path, "w", encoding="utf-8") as f:
                json.dump(programs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.write_log(f"Ошибка сохранения json программ: {e}\n")

    def refresh_programs_list(self):
        # Очищаем виджеты
        for widget in self.scroll_progs.winfo_children():
            widget.destroy()
            
        programs = self.load_programs_list()
        
        # Заголовки
        lbl_h1 = ctk.CTkLabel(self.scroll_progs, text="Название программы", text_color=M3_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        lbl_h1.grid(row=0, column=0, padx=15, pady=8, sticky="w")
        lbl_h2 = ctk.CTkLabel(self.scroll_progs, text="Имя процесса", text_color=M3_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        lbl_h2.grid(row=0, column=1, padx=15, pady=8, sticky="w")
        
        for idx, prog in enumerate(programs):
            name = prog.get("name", "")
            process = prog.get("process", "")
            
            # Карточка для строки списка программы
            frame_item = ctk.CTkFrame(self.scroll_progs, fg_color=M3_SURFACE_CONTAINER, height=45, corner_radius=8)
            frame_item.grid(row=idx+1, column=0, columnspan=3, fill="x", padx=5, pady=4)
            frame_item.grid_columnconfigure(0, weight=2)
            frame_item.grid_columnconfigure(1, weight=2)
            frame_item.grid_columnconfigure(2, weight=1)
            
            lbl_name = ctk.CTkLabel(frame_item, text=name, text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=12, weight="medium"))
            lbl_name.grid(row=0, column=0, padx=15, pady=10, sticky="w")
            
            lbl_proc = ctk.CTkLabel(frame_item, text=process or "[Не указан]", text_color=M3_TEXT_MUTED, font=ctk.CTkFont(family="Segoe UI", size=12))
            lbl_proc.grid(row=0, column=1, padx=15, pady=10, sticky="w")
            
            btn_del = ctk.CTkButton(
                frame_item, text="Удалить", 
                fg_color=M3_RED, text_color=M3_ON_RED, hover_color=M3_TEXT,
                font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
                width=75, height=28, corner_radius=14,
                command=lambda i=idx: self.delete_program(i)
            )
            btn_del.grid(row=0, column=2, padx=15, pady=8, sticky="e")

    def browse_executable(self):
        filepath = filedialog.askopenfilename(
            title="Выберите исполняемый файл",
            filetypes=[("Исполняемые файлы", "*.exe"), ("Все файлы", "*.*")]
        )
        if filepath:
            filepath = os.path.normpath(filepath)
            self.entry_prog_path.delete(0, "end")
            self.entry_prog_path.insert(0, filepath)
            
            filename = os.path.basename(filepath)
            name_without_ext = os.path.splitext(filename)[0]
            
            if not self.entry_prog_name.get().strip():
                self.entry_prog_name.insert(0, name_without_ext.capitalize())
            if not self.entry_prog_process.get().strip():
                self.entry_prog_process.insert(0, filename)

    def add_program_from_ui(self):
        name = self.entry_prog_name.get().strip()
        path = self.entry_prog_path.get().strip()
        process = self.entry_prog_process.get().strip()
        
        if not name or not path:
            messagebox.showerror("Ошибка", "Заполните Название и Путь к программе!")
            return
            
        programs = self.load_programs_list()
        programs.append({
            "name": name,
            "path": path,
            "process": process
        })
        self.save_programs_list(programs)
        
        self.entry_prog_name.delete(0, "end")
        self.entry_prog_path.delete(0, "end")
        self.entry_prog_process.delete(0, "end")
        
        self.refresh_programs_list()
        self.write_log(f"Программа {name} добавлена в список запуска.\n")

    def delete_program(self, index: int):
        programs = self.load_programs_list()
        if 0 <= index < len(programs):
            deleted = programs.pop(index)
            self.save_programs_list(programs)
            self.refresh_programs_list()
            self.write_log(f"Программа {deleted.get('name')} удалена из списка.\n")

    # --- Функции Системного Трея (pystray) ---
    def setup_tray(self):
        self.tray_thread = threading.Thread(target=self._run_tray_loop, daemon=True)
        self.tray_thread.start()

    def _run_tray_loop(self):
        if not os.path.exists(self.icon_path):
            img = Image.new("RGBA", (16, 16), color=(59, 130, 246, 255))
        else:
            img = Image.open(self.icon_path)
            
        def on_open(icon, item):
            self.after(0, self.show_window)
            
        def on_exit(icon, item):
            self.after(0, self.quit_app)
            
        menu = Menu(
            item('Открыть панель', on_open, default=True),
            item('Выход', on_exit)
        )
        
        self.tray_icon = pystray.Icon("Asya PC Bot", img, "Asya PC Bot", menu)
        self.tray_icon.run()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def on_close_event(self):
        self.withdraw()
        self.write_log("Окно свернуто в системный трей.\n")

    def quit_app(self):
        if self.is_quitting:
            return
        self.is_quitting = True
        self.bot_runner.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()
        sys.exit(0)

if __name__ == "__main__":
    if not acquire_gui_lock():
        root = ctk.CTk()
        root.withdraw()
        messagebox.showerror("Ошибка", "Asya PC Bot Control Panel уже запущена!")
        sys.exit(0)
        
    app = AsyaPcBotApp()
    app.mainloop()
