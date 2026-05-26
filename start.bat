@echo off
title Asya Pc Bot
cd /d "%~dp0"

:loop
echo --------------------------------------------------
echo Запуск Asya Pc Bot (aiogram)...
echo --------------------------------------------------
venv\Scripts\python.exe bot.py

rem 42 - специальный код выхода для перезапуска
if %errorlevel% equ 42 (
    echo.
    echo [Перезапуск] Бот запросил перезапуск. Выполняю...
    timeout /t 2 >nul
    goto loop
)

echo --------------------------------------------------
echo Бот завершил работу (код выхода: %errorlevel%).
pause

