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

# Установка темной темы и акцентного цвета CustomTkinter
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
    def __init__(self, log_callback, status_callback, restart_callback):
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.restart_callback = restart_callback
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
                self.log_callback("Получен запрос на выключение бота из Telegram. Бот остановлен.\n")

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
        
        self.title("Asya PC Bot Control Panel")
        self.geometry("720x570")
        self.resizable(False, False)
        
        self.icon_path = os.path.join(config.BASE_DIR, "icon.ico")
        if os.path.exists(self.icon_path):
            self.iconbitmap(self.icon_path)
            
        self.bot_runner = BotRunner(self.write_log, self.update_bot_status, self.restart_bot)
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
        # Панель статуса и запуска (Сверху)
        self.frame_top = ctk.CTkFrame(self, height=70, corner_radius=10)
        self.frame_top.pack(fill="x", padx=15, pady=10)
        
        self.label_status_title = ctk.CTkLabel(self.frame_top, text="Статус бота:", font=ctk.CTkFont(size=14))
        self.label_status_title.pack(side="left", padx=15, pady=20)
        
        self.label_status = ctk.CTkLabel(self.frame_top, text="● Остановлен", text_color="#ef4444", font=ctk.CTkFont(size=14, weight="bold"))
        self.label_status.pack(side="left", padx=5, pady=20)
        
        self.btn_toggle_bot = ctk.CTkButton(self.frame_top, text="Запустить бота", fg_color="#10b981", hover_color="#059669", width=150, command=self.toggle_bot_state)
        self.btn_toggle_bot.pack(side="right", padx=15, pady=20)
        
        # Таб-панель (Центр)
        self.tabview = ctk.CTkTabview(self, width=690, height=450)
        self.tabview.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        
        self.tab_settings = self.tabview.add("Настройки (.env)")
        self.tab_programs = self.tabview.add("Программы (programs.json)")
        self.tab_logs = self.tabview.add("Консоль логов")
        
        # --- Вкладка Настройки ---
        self.label_token = ctk.CTkLabel(self.tab_settings, text="Telegram Bot Token (BOT_TOKEN):", font=ctk.CTkFont(size=12, weight="bold"))
        self.label_token.pack(anchor="w", padx=20, pady=(15, 2))
        
        self.entry_token = ctk.CTkEntry(self.tab_settings, width=640, placeholder_text="Вставьте токен вашего бота (из @BotFather)")
        self.entry_token.pack(padx=20, pady=5)
        
        self.label_users = ctk.CTkLabel(self.tab_settings, text="Разрешенные ID администраторов (ALLOWED_USERS):", font=ctk.CTkFont(size=12, weight="bold"))
        self.label_users.pack(anchor="w", padx=20, pady=(15, 2))
        
        self.entry_users = ctk.CTkEntry(self.tab_settings, width=640, placeholder_text="Пример: 123456789, 987654321 (через запятую)")
        self.entry_users.pack(padx=20, pady=5)
        
        self.label_tip = ctk.CTkLabel(self.tab_settings, text="* Бот будет реагировать на команды только от пользователей из этого списка ID.", text_color="#94a3b8", font=ctk.CTkFont(size=11, slant="italic"))
        self.label_tip.pack(anchor="w", padx=20, pady=5)
        
        self.btn_save_settings = ctk.CTkButton(self.tab_settings, text="Сохранить настройки", fg_color="#3b82f6", hover_color="#2563eb", width=200, command=self.save_settings_from_ui)
        self.btn_save_settings.pack(pady=30)
        
        # --- Вкладка Программы ---
        # Форма добавления новой программы
        self.frame_add_prog = ctk.CTkFrame(self.tab_programs)
        self.frame_add_prog.pack(fill="x", padx=10, pady=10)
        
        # Первая строка формы
        self.entry_prog_name = ctk.CTkEntry(self.frame_add_prog, width=150, placeholder_text="Название (Блокнот)")
        self.entry_prog_name.grid(row=0, column=0, padx=5, pady=5)
        
        self.entry_prog_path = ctk.CTkEntry(self.frame_add_prog, width=280, placeholder_text="Путь к файлу запуска (.exe)")
        self.entry_prog_path.grid(row=0, column=1, padx=5, pady=5)
        
        self.btn_browse = ctk.CTkButton(self.frame_add_prog, text="Обзор...", width=70, command=self.browse_executable)
        self.btn_browse.grid(row=0, column=2, padx=5, pady=5)
        
        self.entry_prog_process = ctk.CTkEntry(self.frame_add_prog, width=130, placeholder_text="Процесс (notepad.exe)")
        self.entry_prog_process.grid(row=0, column=3, padx=5, pady=5)
        
        self.btn_add_prog = ctk.CTkButton(self.frame_add_prog, text="Добавить", width=80, fg_color="#10b981", hover_color="#059669", command=self.add_program_from_ui)
        self.btn_add_prog.grid(row=0, column=4, padx=5, pady=5)
        
        # Список программ с прокруткой
        self.scroll_progs = ctk.CTkScrollableFrame(self.tab_programs, width=650, height=260)
        self.scroll_progs.pack(fill="both", expand=True, padx=10, pady=5)
        
        # --- Вкладка Логи ---
        self.textbox_logs = ctk.CTkTextbox(self.tab_logs, width=650, height=310, font=ctk.CTkFont(family="Consolas", size=11))
        self.textbox_logs.pack(fill="both", expand=True, padx=10, pady=10)
        self.textbox_logs.configure(state="disabled")
        
        self.btn_clear_logs = ctk.CTkButton(self.tab_logs, text="Очистить консоль", width=150, command=self.clear_logs)
        self.btn_clear_logs.pack(pady=(0, 10))

    # --- Функции Управления Ботом ---
    def auto_start_bot_on_launch(self):
        token = self.entry_token.get().strip()
        if token and token != "your_telegram_bot_token_here" and token.strip():
            self.write_log("Выполняю автозапуск бота...\n")
            self.toggle_bot_state()

    def toggle_bot_state(self):
        if self.bot_runner.is_running:
            self.bot_runner.stop()
        else:
            # Сначала проверяем заполненность настроек
            token = self.entry_token.get().strip()
            if not token:
                messagebox.showerror("Ошибка", "Заполните поле Telegram Bot Token во вкладке Настройки!")
                self.tabview.set("Настройки (.env)")
                return
            
            # Сохраняем настройки принудительно перед запуском
            self.save_settings_from_ui(silent=True)
            self.btn_toggle_bot.configure(state="disabled")
            self.bot_runner.start()

    def update_bot_status(self, status: str):
        def update():
            self.btn_toggle_bot.configure(state="normal")
            if status == "RUNNING":
                self.label_status.configure(text="● Запущен", text_color="#10b981")
                self.btn_toggle_bot.configure(text="Остановить бота", fg_color="#ef4444", hover_color="#dc2626")
            else:
                self.label_status.configure(text="● Остановлен", text_color="#ef4444")
                self.btn_toggle_bot.configure(text="Запустить бота", fg_color="#10b981", hover_color="#059669")
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
        # Очищаем виджеты в скролл-фрейме
        for widget in self.scroll_progs.winfo_children():
            widget.destroy()
            
        programs = self.load_programs_list()
        
        # Отрисовка заголовков таблицы
        label_h1 = ctk.CTkLabel(self.scroll_progs, text="Название программы", font=ctk.CTkFont(weight="bold"))
        label_h1.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        label_h2 = ctk.CTkLabel(self.scroll_progs, text="Имя процесса", font=ctk.CTkFont(weight="bold"))
        label_h2.grid(row=0, column=1, padx=10, pady=5, sticky="w")
        
        for idx, prog in enumerate(programs):
            name = prog.get("name", "")
            process = prog.get("process", "")
            path = prog.get("path", "")
            
            # Название
            lbl_name = ctk.CTkLabel(self.scroll_progs, text=name, anchor="w", width=180)
            lbl_name.grid(row=idx+1, column=0, padx=10, pady=5, sticky="w")
            
            # Процесс
            lbl_proc = ctk.CTkLabel(self.scroll_progs, text=process or "[Не указан]", anchor="w", width=180, text_color="#94a3b8")
            lbl_proc.grid(row=idx+1, column=1, padx=10, pady=5, sticky="w")
            
            # Кнопка удаления
            btn_del = ctk.CTkButton(self.scroll_progs, text="Удалить", width=80, fg_color="#ef4444", hover_color="#dc2626", command=lambda i=idx: self.delete_program(i))
            btn_del.grid(row=idx+1, column=2, padx=10, pady=5)

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
        
        # Очищаем форму ввода
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
            # Если иконки нет, создаем заглушку 16x16
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
        # Вместо закрытия скрываем в трей
        self.withdraw()
        self.write_log("Окно свернуто в системный трей.\n")

    def quit_app(self):
        if self.is_quitting:
            return
        self.is_quitting = True
        
        # Останавливаем бота
        self.bot_runner.stop()
        
        # Закрываем иконку трея
        if self.tray_icon:
            self.tray_icon.stop()
            
        # Закрываем GUI
        self.destroy()
        sys.exit(0)

if __name__ == "__main__":
    if not acquire_gui_lock():
        # Показываем сообщение и выходим, если копия уже запущена
        root = ctk.CTk()
        root.withdraw()
        messagebox.showerror("Ошибка", "Asya PC Bot Control Panel уже запущена!")
        sys.exit(0)
        
    app = AsyaPcBotApp()
    app.mainloop()
