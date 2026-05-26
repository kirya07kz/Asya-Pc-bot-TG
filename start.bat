@echo off
title Asya Pc Bot
cd /d "%~dp0"
echo --------------------------------------------------
echo Запуск Asya Pc Bot (aiogram)...
echo --------------------------------------------------
venv\Scripts\python.exe bot.py
echo --------------------------------------------------
echo Бот завершил работу или произошла ошибка.
pause

