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
# ЦВЕТОВАЯ ПАЛИТРА BLACK & WHITE MINIMALIST THEME (KIRO BOT STYLE)
# =====================================================================
M3_BG = "#000000"                  # Чисто черный фон приложения
M3_SURFACE_LOW = "#000000"         # Фон бокового меню (Черный)
M3_CARD_BG = "#161616"             # Темно-серый фон карточек (под Kiro Bot)
M3_INPUT_BG = "#090909"            # Фон полей ввода
M3_BORDER = "#1f1f1f"              # Ненавязчивая темно-серая обводка (border)
M3_SURFACE_HIGH = "#121212"        # Ховер кнопок навигации (Темно-серый)
M3_PRIMARY = "#ffffff"             # Белые кнопки и активные элементы
M3_ON_PRIMARY = "#000000"          # Черный текст на белых кнопках
M3_TEXT = "#ffffff"                # Чисто белый текст
M3_TEXT_MUTED = "#888888"          # Серый текст

# Установка базовой темы CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

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
    def __init__(self, textbox, update_state_callback):
        super().__init__()
        self.textbox = textbox
        self.update_state_callback = update_state_callback
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', '%H:%M:%S'))

    def emit(self, record):
        msg = self.format(record)
        def append():
            try:
                self.textbox.configure(state="normal")
                self.textbox.insert("end", msg + "\n")
                self.textbox.see("end")
                self.textbox.configure(state="disabled")
                self.update_state_callback()
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
        
        # Загружаем временный список программ в память
        self.temp_programs = self.load_programs_list()
        
        self.content_frames = {}
        self.nav_buttons = {}
        
        self.setup_ui()
        self.setup_tray()
        
        # Подгружаем настройки на экран
        self.load_settings_to_ui()
        self.refresh_programs_list()
        
        # Перехват логов с передачей колбэка обновления состояния логов
        gui_log_handler = GuiLogHandler(self.textbox_logs, self.update_logs_view_state)
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
        # Боковая панель навигации (Navigation Rail) в стиле B&W
        # -------------------------------------------------------------
        self.frame_rail = ctk.CTkFrame(
            self, width=200, fg_color=M3_SURFACE_LOW, 
            border_color=M3_BORDER, border_width=1, corner_radius=0
        )
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
            text="Версия 2.2 [B&W]", 
            text_color=M3_TEXT_MUTED, 
            font=ctk.CTkFont(family="Segoe UI", size=11, slant="italic")
        )
        self.lbl_version.pack(padx=15, pady=(0, 30))
        
        # Навигационные кнопки
        self.btn_nav_home = ctk.CTkButton(
            self.frame_rail, text="🏠  Главная", height=45, corner_radius=22,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            command=lambda: self.switch_tab("home")
        )
        self.btn_nav_home.pack(fill="x", padx=12, pady=8)
        self.nav_buttons["home"] = self.btn_nav_home

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
        
        # --- Вкладка Главная ---
        self.frame_home = ctk.CTkFrame(self.frame_content, fg_color="transparent")
        self.content_frames["home"] = self.frame_home
        
        # Шапка вкладки (сверху): ☰ Asya PC Bot и ⋮
        self.frame_home_header = ctk.CTkFrame(self.frame_home, fg_color="transparent", height=40)
        self.frame_home_header.pack(fill="x", pady=(0, 10))
        self.frame_home_header.pack_propagate(False)
        
        self.lbl_home_menu_icon = ctk.CTkLabel(
            self.frame_home_header, text="☰   Asya PC Bot", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        )
        self.lbl_home_menu_icon.pack(side="left", padx=5)
        
        self.lbl_home_more_icon = ctk.CTkLabel(
            self.frame_home_header, text="⋮", 
            text_color=M3_TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=20)
        )
        self.lbl_home_more_icon.pack(side="right", padx=5)
        
        # Название бота и активность
        self.lbl_home_title = ctk.CTkLabel(
            self.frame_home, text="Asya PC Bot", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold")
        )
        self.lbl_home_title.pack(anchor="w", padx=10, pady=(15, 0))
        
        self.lbl_home_subtitle = ctk.CTkLabel(
            self.frame_home, text="Активность бота", 
            text_color=M3_TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=13)
        )
        self.lbl_home_subtitle.pack(anchor="w", padx=10, pady=(0, 15))
        
        # Карточка статуса (темный закругленный прямоугольник)
        self.frame_status_card = ctk.CTkFrame(
            self.frame_home, fg_color=M3_CARD_BG, 
            border_color=M3_BORDER, border_width=1,
            height=54, corner_radius=12
        )
        self.frame_status_card.pack(fill="x", padx=10, pady=5)
        self.frame_status_card.pack_propagate(False)
        
        self.lbl_status_card_name = ctk.CTkLabel(
            self.frame_status_card, text="|   asya_pc_bot", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.lbl_status_card_name.pack(side="left", padx=15, pady=12)
        
        self.label_status_indicator = ctk.CTkLabel(
            self.frame_status_card, text="○ Выключен", 
            text_color=M3_TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.label_status_indicator.pack(side="right", padx=15, pady=12)
        
        # Кнопка Запустить / Остановить бота (Большая белая пилюля)
        self.btn_toggle_bot_large = ctk.CTkButton(
            self.frame_home, text="▶  ЗАПУСТИТЬ БОТА", 
            fg_color=M3_PRIMARY, 
            text_color=M3_ON_PRIMARY,
            hover_color="#e0e0e0",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            height=48,
            corner_radius=24,
            command=self.toggle_bot_state
        )
        self.btn_toggle_bot_large.pack(fill="x", padx=10, pady=(20, 15))
        
        # Раздел "О возможностях бота"
        self.lbl_features_header = ctk.CTkLabel(
            self.frame_home, text="О возможностях бота", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
        )
        self.lbl_features_header.pack(anchor="w", padx=10, pady=(10, 5))
        
        # Карточка возможностей
        self.frame_features_card = ctk.CTkFrame(
            self.frame_home, fg_color=M3_CARD_BG,
            border_color=M3_BORDER, border_width=1,
            corner_radius=12
        )
        self.frame_features_card.pack(fill="both", expand=True, padx=10, pady=5)
        
        # Элемент 1: Зачем он нужен?
        self.lbl_feat_1_title = ctk.CTkLabel(
            self.frame_features_card, text="▶   Зачем он нужен?",
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.lbl_feat_1_title.pack(anchor="w", padx=15, pady=(15, 2))
        
        self.lbl_feat_1_desc = ctk.CTkLabel(
            self.frame_features_card, 
            text="Бот работает круглосуточно, выполняет ваши команды по управлению компьютером, запускает программы и присылает уведомления.",
            text_color=M3_TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            wraplength=520, justify="left"
        )
        self.lbl_feat_1_desc.pack(anchor="w", padx=15, pady=(0, 15))
        
        # Элемент 2: Возможности
        self.lbl_feat_2_title = ctk.CTkLabel(
            self.frame_features_card, text="🌐   Возможности",
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold")
        )
        self.lbl_feat_2_title.pack(anchor="w", padx=15, pady=(0, 2))
        
        self.lbl_feat_2_bullets = ctk.CTkLabel(
            self.frame_features_card,
            text="• Запуск и закрытие программ из вашего списка.\n• Выключение и перезагрузка компьютера удаленно.\n• Получение скриншотов рабочего стола.\n• Уведомления о старте системы и фоновый режим 24/7.",
            text_color=M3_TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            justify="left"
        )
        self.lbl_feat_2_bullets.pack(anchor="w", padx=15, pady=(0, 15))
        
        # --- Вкладка Настройки ---
        self.frame_settings = ctk.CTkFrame(self.frame_content, fg_color="transparent")
        self.content_frames["settings"] = self.frame_settings
        
        # Шапка Settings
        self.frame_settings_header = ctk.CTkFrame(self.frame_settings, fg_color="transparent", height=40)
        self.frame_settings_header.pack(fill="x", pady=(0, 10))
        self.frame_settings_header.pack_propagate(False)
        
        self.lbl_settings_header_title = ctk.CTkLabel(
            self.frame_settings_header, text="⚙️   Настройки параметров", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        )
        self.lbl_settings_header_title.pack(side="left", padx=5)
        
        # Карточка для настроек
        self.frame_settings_card = ctk.CTkFrame(
            self.frame_settings, fg_color=M3_CARD_BG,
            border_color=M3_BORDER, border_width=1,
            corner_radius=12
        )
        self.frame_settings_card.pack(fill="x", padx=10, pady=10)
        
        self.label_token = ctk.CTkLabel(
            self.frame_settings_card, text="Telegram Bot Token (BOT_TOKEN):", 
            text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.label_token.pack(anchor="w", padx=15, pady=(15, 5))
        
        self.entry_token = ctk.CTkEntry(
            self.frame_settings_card, fg_color=M3_INPUT_BG, border_color=M3_BORDER,
            text_color=M3_TEXT, placeholder_text_color=M3_TEXT_MUTED, corner_radius=8, height=35
        )
        self.entry_token.pack(fill="x", padx=15, pady=(0, 15))
        
        self.label_users = ctk.CTkLabel(
            self.frame_settings_card, text="Разрешенные ID администраторов (ALLOWED_USERS):", 
            text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold")
        )
        self.label_users.pack(anchor="w", padx=15, pady=(0, 5))
        
        self.entry_users = ctk.CTkEntry(
            self.frame_settings_card, fg_color=M3_INPUT_BG, border_color=M3_BORDER,
            text_color=M3_TEXT, placeholder_text_color=M3_TEXT_MUTED, corner_radius=8, height=35
        )
        self.entry_users.pack(fill="x", padx=15, pady=(0, 15))
        
        self.label_tip = ctk.CTkLabel(
            self.frame_settings, 
            text="* Бот обрабатывает команды ТОЛЬКО от указанных пользователей для безопасности вашего ПК.", 
            text_color=M3_TEXT_MUTED, 
            font=ctk.CTkFont(family="Segoe UI", size=11, slant="italic")
        )
        self.label_tip.pack(anchor="w", padx=15, pady=5)
        
        self.btn_save_settings = ctk.CTkButton(
            self.frame_settings, text="Сохранить настройки", 
            fg_color=M3_PRIMARY, 
            text_color=M3_ON_PRIMARY,
            hover_color="#e0e0e0",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            width=220, 
            height=42,
            corner_radius=21,
            command=self.save_settings_from_ui
        )
        self.btn_save_settings.pack(pady=30)
        
        # --- Вкладка Программы ---
        self.frame_programs = ctk.CTkFrame(self.frame_content, fg_color="transparent")
        self.content_frames["programs"] = self.frame_programs
        
        # Шапка Programs
        self.frame_programs_header = ctk.CTkFrame(self.frame_programs, fg_color="transparent", height=40)
        self.frame_programs_header.pack(fill="x", pady=(0, 10))
        self.frame_programs_header.pack_propagate(False)
        
        self.lbl_programs_header_title = ctk.CTkLabel(
            self.frame_programs_header, text="🚀   Список запускаемых программ", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        )
        self.lbl_programs_header_title.pack(side="left", padx=5)
        
        # Форма добавления
        self.frame_add_prog = ctk.CTkFrame(
            self.frame_programs, fg_color=M3_CARD_BG, 
            border_color=M3_BORDER, border_width=1, 
            corner_radius=12, height=95
        )
        self.frame_add_prog.pack(fill="x", padx=10, pady=5)
        
        # Сетка формы
        self.entry_prog_name = ctk.CTkEntry(
            self.frame_add_prog, width=130, height=32, 
            fg_color=M3_INPUT_BG, border_color=M3_BORDER, corner_radius=6, 
            placeholder_text="Название"
        )
        self.entry_prog_name.grid(row=0, column=0, padx=8, pady=12)
        
        self.entry_prog_path = ctk.CTkEntry(
            self.frame_add_prog, width=190, height=32, 
            fg_color=M3_INPUT_BG, border_color=M3_BORDER, corner_radius=6, 
            placeholder_text="Путь к файлу"
        )
        self.entry_prog_path.grid(row=0, column=1, padx=4, pady=12)
        
        self.btn_browse = ctk.CTkButton(
            self.frame_add_prog, text="Обзор", 
            fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color="#e0e0e0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            width=65, height=32, corner_radius=6, command=self.browse_executable
        )
        self.btn_browse.grid(row=0, column=2, padx=4, pady=12)
        
        self.entry_prog_process = ctk.CTkEntry(
            self.frame_add_prog, width=120, height=32, 
            fg_color=M3_INPUT_BG, border_color=M3_BORDER, corner_radius=6, 
            placeholder_text="Имя процесса"
        )
        self.entry_prog_process.grid(row=0, column=3, padx=4, pady=12)
        
        self.btn_add_prog = ctk.CTkButton(
            self.frame_add_prog, text="Добавить", 
            fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color="#e0e0e0",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            width=70, height=32, corner_radius=6, command=self.add_program_from_ui
        )
        self.btn_add_prog.grid(row=0, column=4, padx=8, pady=12)
        
        # Список с прокруткой
        self.scroll_progs = ctk.CTkScrollableFrame(self.frame_programs, fg_color="transparent", height=180)
        self.scroll_progs.pack(fill="both", expand=True, padx=5, pady=10)
        
        # Кнопка сохранения изменений списка
        self.btn_save_programs = ctk.CTkButton(
            self.frame_programs, text="Сохранить список изменений", 
            fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color="#e0e0e0",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            width=240, height=40, corner_radius=20,
            command=self.save_programs_from_ui
        )
        self.btn_save_programs.pack(pady=10)
        
        # --- Вкладка Логи ---
        self.frame_logs = ctk.CTkFrame(self.frame_content, fg_color="transparent")
        self.content_frames["logs"] = self.frame_logs
        
        # Шапка Logs
        self.frame_logs_header = ctk.CTkFrame(self.frame_logs, fg_color="transparent", height=40)
        self.frame_logs_header.pack(fill="x", pady=(0, 10))
        self.frame_logs_header.pack_propagate(False)
        
        self.lbl_logs_header_title = ctk.CTkLabel(
            self.frame_logs_header, text="📊   Консоль логов", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold")
        )
        self.lbl_logs_header_title.pack(side="left", padx=5)
        
        # Внутренний контейнер, где будут находиться либо textbox, либо placeholder
        self.frame_logs_container = ctk.CTkFrame(self.frame_logs, fg_color="transparent")
        self.frame_logs_container.pack(fill="both", expand=True)
        
        # Плейсхолдер "Нет логов"
        self.frame_no_logs = ctk.CTkFrame(self.frame_logs_container, fg_color="transparent")
        
        # Иконка документа (Используем крупный эмодзи или текст 📄)
        self.lbl_no_logs_icon = ctk.CTkLabel(
            self.frame_no_logs, text="📄", 
            text_color=M3_TEXT_MUTED,
            font=ctk.CTkFont(size=64)
        )
        self.lbl_no_logs_icon.pack(expand=True, pady=(80, 5))
        
        self.lbl_no_logs_title = ctk.CTkLabel(
            self.frame_no_logs, text="Нет логов", 
            text_color=M3_TEXT,
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold")
        )
        self.lbl_no_logs_title.pack(pady=5)
        
        self.lbl_no_logs_subtitle = ctk.CTkLabel(
            self.frame_no_logs, text="Логи появятся после запуска бота", 
            text_color=M3_TEXT_MUTED,
            font=ctk.CTkFont(family="Segoe UI", size=12)
        )
        self.lbl_no_logs_subtitle.pack(pady=(0, 100))
        
        # Текстовое поле логов
        self.textbox_logs = ctk.CTkTextbox(
            self.frame_logs_container, fg_color=M3_CARD_BG, text_color=M3_TEXT, 
            border_color=M3_BORDER, border_width=1,
            font=ctk.CTkFont(family="Consolas", size=11), corner_radius=12
        )
        # Изначально не запаковываем, update_logs_view_state() сделает это
        
        # Плавающие кнопки в правом нижнем углу (как Share и Trash в Kiro Bot)
        # Кнопка Копирования логов
        self.btn_copy_logs_floating = ctk.CTkButton(
            self.frame_logs_container, text="📋", 
            fg_color="#1c1b1f", text_color=M3_TEXT, 
            hover_color="#2b2b2b",
            font=ctk.CTkFont(size=16),
            width=46, height=46, corner_radius=23,
            command=self.copy_logs_to_clipboard
        )
        self.btn_copy_logs_floating.place(relx=0.97, rely=0.84, anchor="se")
        
        # Кнопка Очистки логов
        self.btn_clear_logs_floating = ctk.CTkButton(
            self.frame_logs_container, text="🗑️", 
            fg_color="#1c1b1f", text_color=M3_TEXT, 
            hover_color="#2b2b2b",
            font=ctk.CTkFont(size=16),
            width=46, height=46, corner_radius=23,
            command=self.clear_logs
        )
        self.btn_clear_logs_floating.place(relx=0.97, rely=0.97, anchor="se")
        
        # Дефолтная активная вкладка
        self.switch_tab("home")

    def update_logs_view_state(self):
        log_content = self.textbox_logs.get("1.0", "end-1c").strip()
        if not log_content:
            self.textbox_logs.pack_forget()
            self.frame_no_logs.pack(fill="both", expand=True)
        else:
            self.frame_no_logs.pack_forget()
            self.textbox_logs.pack(fill="both", expand=True, padx=10, pady=5)
            # Убедимся, что плавающие кнопки поверх textbox
            self.btn_copy_logs_floating.lift()
            self.btn_clear_logs_floating.lift()

    def switch_tab(self, tab_name: str):
        # Переключение визуальных стилей вкладок
        for name, button in self.nav_buttons.items():
            if name == tab_name:
                button.configure(fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color="#e0e0e0")
            else:
                button.configure(fg_color="transparent", text_color=M3_TEXT, hover_color="#121212")
                
        # Прячем все вкладки
        for frame in self.content_frames.values():
            frame.pack_forget()
            
        # Упаковываем текущую вкладку
        self.content_frames[tab_name].pack(fill="both", expand=True, padx=20, pady=20)
        
        # Обновляем состояние логов при переключении на них
        if tab_name == "logs":
            self.update_logs_view_state()

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
            self.btn_toggle_bot_large.configure(state="disabled")
            self.bot_runner.start()

    def update_bot_status(self, status: str):
        def update():
            self.btn_toggle_bot_large.configure(state="normal")
            if status == "RUNNING":
                self.label_status_indicator.configure(text="● Активен", text_color=M3_PRIMARY)
                self.title("Asya PC Bot [Запущен]")
                self.btn_toggle_bot_large.configure(
                    text="⏹  ОСТАНОВИТЬ БОТА", 
                    fg_color=M3_PRIMARY, 
                    text_color=M3_ON_PRIMARY, 
                    hover_color="#e0e0e0"
                )
            else:
                self.label_status_indicator.configure(text="○ Выключен", text_color=M3_TEXT_MUTED)
                self.title("Asya PC Bot [Остановлен]")
                self.btn_toggle_bot_large.configure(
                    text="▶  ЗАПУСТИТЬ БОТА", 
                    fg_color=M3_PRIMARY, 
                    text_color=M3_ON_PRIMARY, 
                    hover_color="#e0e0e0"
                )
                
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
            self.update_logs_view_state()
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
        self.update_logs_view_state()

    def copy_logs_to_clipboard(self):
        log_content = self.textbox_logs.get("1.0", "end-1c")
        if log_content.strip():
            self.clipboard_clear()
            self.clipboard_append(log_content)
            messagebox.showinfo("Успех", "Логи успешно скопированы в буфер обмена!")
        else:
            messagebox.showwarning("Внимание", "Логи отсутствуют или пусты!")

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
        # Очищаем виджеты в скролле
        for widget in self.scroll_progs.winfo_children():
            widget.destroy()
            
        programs = self.temp_programs
        
        # Заголовки
        lbl_h1 = ctk.CTkLabel(self.scroll_progs, text="Название программы", text_color=M3_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        lbl_h1.grid(row=0, column=0, padx=15, pady=8, sticky="w")
        lbl_h2 = ctk.CTkLabel(self.scroll_progs, text="Имя процесса", text_color=M3_PRIMARY, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
        lbl_h2.grid(row=0, column=1, padx=15, pady=8, sticky="w")
        
        for idx, prog in enumerate(programs):
            name = prog.get("name", "")
            process = prog.get("process", "")
            
            # Карточка для строки списка программы (Темная карточка)
            frame_item = ctk.CTkFrame(
                self.scroll_progs, fg_color=M3_CARD_BG, 
                border_color=M3_BORDER, border_width=1, 
                height=45, corner_radius=8
            )
            frame_item.grid(row=idx+1, column=0, columnspan=3, padx=5, pady=4, sticky="ew")
            frame_item.grid_columnconfigure(0, weight=2)
            frame_item.grid_columnconfigure(1, weight=2)
            frame_item.grid_columnconfigure(2, weight=1)
            
            lbl_name = ctk.CTkLabel(frame_item, text=name, text_color=M3_TEXT, font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"))
            lbl_name.grid(row=0, column=0, padx=15, pady=10, sticky="w")
            
            lbl_proc = ctk.CTkLabel(frame_item, text=process or "[Не указан]", text_color=M3_TEXT_MUTED, font=ctk.CTkFont(family="Segoe UI", size=12))
            lbl_proc.grid(row=0, column=1, padx=15, pady=10, sticky="w")
            
            btn_del = ctk.CTkButton(
                frame_item, text="Удалить", 
                fg_color=M3_PRIMARY, text_color=M3_ON_PRIMARY, hover_color="#e0e0e0",
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
            
        # Лимит добавленных программ (максимум 10)
        if len(self.temp_programs) >= 10:
            messagebox.showwarning("Лимит превышен", "Вы не можете добавить больше 10 программ!")
            return
            
        self.temp_programs.append({
            "name": name,
            "path": path,
            "process": process
        })
        
        self.entry_prog_name.delete(0, "end")
        self.entry_prog_path.delete(0, "end")
        self.entry_prog_process.delete(0, "end")
        
        self.refresh_programs_list()
        self.write_log(f"Программа {name} добавлена во временный список. Нажмите «Сохранить список изменений» для записи на диск.\n")

    def delete_program(self, index: int):
        if 0 <= index < len(self.temp_programs):
            deleted = self.temp_programs.pop(index)
            self.refresh_programs_list()
            self.write_log(f"Программа {deleted.get('name')} удалена из временного списка. Нажмите «Сохранить список изменений» для записи на диск.\n")

    def save_programs_from_ui(self):
        self.save_programs_list(self.temp_programs)
        self.write_log("Список программ успешно сохранен на диск (programs.json)!\n")
        messagebox.showinfo("Успех", "Список программ успешно сохранен на диск!")

    # --- Функции Системного Трея (pystray) ---
    def setup_tray(self):
        self.tray_thread = threading.Thread(target=self._run_tray_loop, daemon=True)
        self.tray_thread.start()

    def _run_tray_loop(self):
        if not os.path.exists(self.icon_path):
            img = Image.new("RGBA", (16, 16), color=(255, 255, 255, 255))
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
