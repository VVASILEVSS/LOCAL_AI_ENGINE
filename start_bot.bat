@echo off
title 🤖 LOCAL_AI_ENGINE Bot
color 0A
cd /d "%~dp0"

echo ========================================
echo  🚀 Запуск LOCAL_AI_ENGINE Бота
echo ========================================
echo.

:: Проверка наличия Python в PATH
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Ошибка: Python не найден в PATH.
    echo    Убедитесь, что Python 3.13 установлен и добавлен в переменные окружения.
    pause >nul
    exit /b 1
)

echo ✅ Python найден. Папка проекта: %cd%
echo ⏳ Инициализация кэша рынков и запуск polling...
echo.

python main.py

echo.
echo ========================================
echo  ⏹️  Бот остановлен или завершил работу
echo ========================================
pause >nul