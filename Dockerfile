# ── LOCAL_AI_ENGINE — Dockerfile ──────────────────────────────────────
# Python 3.13 slim image. Telegram-бот + ccxt + cloud LLM (Alibaba GLM).
# Image size: ~450 MB (slim base + pip packages).

FROM python:3.13-slim

# Metadata
LABEL maintainer="LOCAL_AI_ENGINE team" \
      description="Telegram bot: crypto analysis via cloud LLM" \
      version="2.1"

# Prevent Python from writing .pyc / buffering stdout
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps: matplotlib needs libfreetype, libpng; ccxt uses openssl
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY core/ ./core/
COPY main.py ./

# Create dirs: logs/ (rotated by logging_setup.py) and data/state/ (state_tracker.py)
RUN mkdir -p logs data/state

# .env is injected via docker-compose volumes (not baked into image)
# .env must contain: TOKEN, MY_CHAT_ID, LLM_API_KEY, LLM_BASE_URL, MODEL_NAME

# Health check: verify Python + aiogram + ccxt can import
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import aiogram, ccxt, httpx; exit(0)" || exit 1

# Run the bot
CMD ["python", "main.py"]
