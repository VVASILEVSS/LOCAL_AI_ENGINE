#!/usr/bin/env bash
# LOCAL_AI_ENGINE — запуск бота (Linux / macOS)
cd "$(dirname "$0")" || exit 1

# Создать .env если нет (копировать из примера)
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "Создан .env из .env.example — ЗАПОЛНИТЕ ТОКЕН И API-КЛЮЧ!"
    else
        echo "ОШИБКА: нет .env и .env.example"
        exit 1
    fi
fi

# Активировать venv если есть
if [ -d .venv ]; then
    source .venv/bin/activate
fi

# Проверить зависимости
python -c "import aiogram" 2>/dev/null || {
    echo "Зависимости не установлены. Запускаю pip install..."
    pip install -r requirements.txt
}

python main.py