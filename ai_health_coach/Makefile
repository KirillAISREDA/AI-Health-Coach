.PHONY: help up down dev logs shell migrate revision ps clean

# ── Переменные ────────────────────────────────────────────────────────────────
COMPOSE      = docker compose
COMPOSE_PROD = docker compose --profile prod
BOT_SVC      = bot

# ── Помощь ────────────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  AI Health Coach Bot — команды"
	@echo ""
	@echo "  Разработка:"
	@echo "    make dev        — запустить postgres + redis локально (без бота)"
	@echo "    make run        — запустить бота напрямую (polling, без Docker)"
	@echo ""
	@echo "  Docker:"
	@echo "    make up         — запустить все сервисы (dev режим)"
	@echo "    make prod       — запустить все сервисы + nginx (prod режим)"
	@echo "    make down       — остановить все сервисы"
	@echo "    make logs       — показать логи бота"
	@echo "    make ps         — статус контейнеров"
	@echo ""
	@echo "  База данных:"
	@echo "    make migrate    — применить миграции"
	@echo "    make revision m=\"описание\" — создать новую миграцию"
	@echo "    make shell-db   — psql в контейнере postgres"
	@echo ""
	@echo "    make clean      — удалить контейнеры и volumes"
	@echo ""

# ── Dev: только инфра ────────────────────────────────────────────────────────
dev:
	$(COMPOSE) up -d postgres redis
	@echo "✅ Postgres и Redis запущены. Бот запускай локально: make run"

# ── Запуск бота локально ─────────────────────────────────────────────────────
run:
	python -m bot.main

# ── Docker: все сервисы ──────────────────────────────────────────────────────
up:
	$(COMPOSE) up -d --build
	@echo "✅ Все сервисы запущены"

prod:
	$(COMPOSE_PROD) up -d --build
	@echo "✅ Prod стек с nginx запущен"

down:
	$(COMPOSE) down

# ── Логи ────────────────────────────────────────────────────────────────────
logs:
	$(COMPOSE) logs -f $(BOT_SVC)

logs-all:
	$(COMPOSE) logs -f

logs-celery:
	$(COMPOSE) logs -f celery_worker celery_beat

# ── Статус ──────────────────────────────────────────────────────────────────
ps:
	$(COMPOSE) ps

# ── Миграции ─────────────────────────────────────────────────────────────────
migrate:
	$(COMPOSE) run --rm migrations alembic upgrade head

revision:
	$(COMPOSE) run --rm migrations alembic revision --autogenerate -m "$(m)"

downgrade:
	$(COMPOSE) run --rm migrations alembic downgrade -1

# ── Shell ────────────────────────────────────────────────────────────────────
shell:
	$(COMPOSE) exec $(BOT_SVC) /bin/bash

shell-db:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER} -d $${POSTGRES_DB}

shell-redis:
	$(COMPOSE) exec redis redis-cli

# ── Очистка ──────────────────────────────────────────────────────────────────
clean:
	$(COMPOSE) down -v --remove-orphans
	@echo "⚠️  Все контейнеры и volumes удалены"
