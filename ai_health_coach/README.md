# 🤖 AI Health Coach Bot

Персональный Telegram-бот для контроля питания, тренировок, воды и БАДов.
Использует GPT-4o для анализа еды по фото и живого диалога с коучем.

## 🗂 Структура проекта

```
ai_health_coach/
├── bot/
│   ├── config.py            # Настройки (pydantic-settings)
│   ├── main.py              # Точка входа
│   ├── models/              # SQLAlchemy модели
│   ├── services/
│   │   ├── ai_service.py    # GPT-4o: фото + текст + контекст
│   │   ├── user_service.py  # CRUD, TDEE, статистика
│   │   └── database.py      # Async engine + session
│   ├── handlers/
│   │   ├── onboarding.py    # FSM анкета (8 шагов)
│   │   ├── nutrition.py     # Фото еды + текстовый ввод
│   │   ├── water.py         # Трекер воды
│   │   ├── supplements.py   # БАДы + совместимость
│   │   └── stats.py         # Статистика + свободный чат
│   ├── keyboards/main.py    # Все клавиатуры
│   └── middlewares/         # UserContext middleware
├── celery_app/
│   └── tasks.py             # Напоминания + дайджест
├── migrations/              # Alembic миграции
├── nginx/nginx.conf         # Reverse proxy (prod)
├── docker-compose.yml
├── Dockerfile
├── Makefile
└── requirements.txt
```

## 🚀 Быстрый старт (локально)

### 1. Клонируй и настрой окружение

```bash
git clone https://github.com/yourrepo/ai-health-coach.git
cd ai_health_coach

cp .env.example .env
# Открой .env и заполни BOT_TOKEN и OPENAI_API_KEY
```

### 2. Запусти инфраструктуру

```bash
make dev
# Поднимет PostgreSQL и Redis в Docker
```

### 3. Примени миграции

```bash
pip install -r requirements.txt
alembic upgrade head
```

### 4. Запусти бота

```bash
make run
# или: python -m bot.main
```

### 5. Запусти Celery (напоминания)

```bash
# В отдельном терминале:
celery -A celery_app.tasks worker --loglevel=info

# И Beat (планировщик):
celery -A celery_app.tasks beat --loglevel=info
```

---

## 🐳 Запуск в Docker (всё сразу)

```bash
cp .env.example .env
# Заполни .env

make up
# Запустит: postgres, redis, migrations, bot, celery worker, celery beat
```

Смотреть логи:
```bash
make logs          # логи бота
make logs-celery   # логи Celery
make logs-all      # все сервисы
```

---

## 🌐 Деплой на VPS (prod)

### Требования
- VPS с Ubuntu 22.04+, минимум 2GB RAM
- Домен с A-записью на IP сервера
- SSL-сертификат (Let's Encrypt)

### 1. Получи SSL через Certbot

```bash
apt install certbot
certbot certonly --standalone -d yourdomain.com

# Сертификаты окажутся в:
# /etc/letsencrypt/live/yourdomain.com/fullchain.pem
# /etc/letsencrypt/live/yourdomain.com/privkey.pem
```

### 2. Скопируй сертификаты

```bash
mkdir -p nginx/ssl
cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem nginx/ssl/
cp /etc/letsencrypt/live/yourdomain.com/privkey.pem nginx/ssl/
```

### 3. Обнови .env для webhook

```bash
WEBHOOK_HOST=https://yourdomain.com
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET=ваш_секрет_32_символа
```

### 4. Обнови nginx.conf

Замени `yourdomain.com` на свой домен в `nginx/nginx.conf`.

### 5. Обнови bot/main.py для webhook

Раскомментируй блок webhook вместо polling (см. комментарий в файле).

### 6. Запусти prod стек

```bash
make prod
# Запустит все сервисы + nginx
```

---

## ⚙️ Команды Makefile

| Команда | Описание |
|---------|----------|
| `make up` | Запустить все сервисы |
| `make prod` | Запустить с nginx (prod) |
| `make down` | Остановить всё |
| `make logs` | Логи бота |
| `make migrate` | Применить миграции |
| `make revision m="..."` | Новая миграция |
| `make shell-db` | psql в postgres |
| `make shell-redis` | redis-cli |
| `make clean` | Удалить всё включая volumes |

---

## 📊 Архитектура данных

| Таблица | Назначение |
|---------|-----------|
| `users` | Профиль, параметры, TDEE, часовой пояс |
| `food_logs` | Дневник питания (фото + текст) |
| `water_logs` | Трекер воды по дням |
| `supplements` | Справочник БАДов пользователя |
| `supplement_logs` | Факты приёма БАДов |
| `reminders` | Расписание напоминаний |

---

## 🔑 Переменные окружения

| Переменная | Описание |
|-----------|---------|
| `BOT_TOKEN` | Токен от @BotFather |
| `OPENAI_API_KEY` | API ключ OpenAI |
| `POSTGRES_*` | Параметры PostgreSQL |
| `REDIS_HOST` | Хост Redis |
| `WEBHOOK_HOST` | Домен для webhook (prod) |
| `CONTEXT_MESSAGES_LIMIT` | Память диалога (по умолчанию 15) |

---

## 💡 Roadmap

- [x] MVP: онбординг, фото еды, вода, БАДы, свободный чат
- [ ] V2: анализ тренировок, генерация плана, PDF-отчёты
- [ ] V3: Apple Health / Google Fit, геймификация, подписка

---

> ⚠️ Бот предоставляет рекомендации информационного характера.
> Не является медицинским назначением. Консультируйся с врачом.
