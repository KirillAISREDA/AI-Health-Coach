FROM python:3.12-slim

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости Python (кэшируются отдельным слоем)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходный код
COPY . .

# Непривилегированный пользователь
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import asyncio; from bot.config import settings; print('OK')" || exit 1

CMD ["python", "-m", "bot.main"]
