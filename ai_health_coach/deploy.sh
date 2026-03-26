#!/usr/bin/env bash
# deploy.sh — деплой AI Health Coach на VPS (Ubuntu 22.04+)
#
# Использование:
#   chmod +x deploy.sh
#   ./deploy.sh            # первый деплой
#   ./deploy.sh --update   # обновление без пересоздания volumes

set -euo pipefail

# --- Цвета ---------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# --- Аргументы -----------------------------------------------------------
UPDATE_MODE=false
[[ "${1:-}" == "--update" ]] && UPDATE_MODE=true

echo -e "${BOLD}"
echo "╔══════════════════════════════════════╗"
echo "║     AI Health Coach — Deploy         ║"
echo "╚══════════════════════════════════════╝"
echo -e "${NC}"

# --- Проверки окружения ---------------------------------------------------
info "Проверяем окружение..."

command -v docker  >/dev/null 2>&1 || error "Docker не установлен"
command -v git     >/dev/null 2>&1 || error "Git не установлен"
docker compose version >/dev/null 2>&1 || error "Docker Compose V2 не установлен"

[[ -f ".env" ]] || error ".env не найден. Скопируй .env.example → .env и заполни."

# Проверяем обязательные переменные
source .env
[[ -z "${BOT_TOKEN:-}" ]]      && error "BOT_TOKEN не задан в .env"
[[ -z "${OPENAI_API_KEY:-}" ]] && error "OPENAI_API_KEY не задан в .env"
[[ -z "${POSTGRES_PASSWORD:-}" ]] && error "POSTGRES_PASSWORD не задан в .env"

success "Окружение OK"

# --- SSL-сертификаты (только для prod с webhook) --------------------------
if [[ "${WEBHOOK_HOST:-}" != "" ]]; then
    info "Webhook режим обнаружен: ${WEBHOOK_HOST}"

    SSL_DIR="nginx/ssl"
    if [[ ! -f "${SSL_DIR}/fullchain.pem" || ! -f "${SSL_DIR}/privkey.pem" ]]; then
        warn "SSL сертификаты не найдены в ${SSL_DIR}/"
        info "Пытаемся найти через certbot..."

        DOMAIN="${WEBHOOK_HOST#https://}"

        CERT_PATH="/etc/letsencrypt/live/${DOMAIN}"
        if [[ -d "${CERT_PATH}" ]]; then
            mkdir -p "${SSL_DIR}"
            cp "${CERT_PATH}/fullchain.pem" "${SSL_DIR}/"
            cp "${CERT_PATH}/privkey.pem" "${SSL_DIR}/"
            success "SSL сертификаты скопированы из ${CERT_PATH}"
        else
            warn "Сертификаты не найдены. Запускаем certbot..."
            command -v certbot >/dev/null 2>&1 || apt-get install -y certbot
            certbot certonly --standalone -d "${DOMAIN}" --non-interactive --agree-tos \
                --email "admin@${DOMAIN}" || error "Certbot не смог получить сертификат"
            mkdir -p "${SSL_DIR}"
            cp "${CERT_PATH}/fullchain.pem" "${SSL_DIR}/"
            cp "${CERT_PATH}/privkey.pem" "${SSL_DIR}/"
            success "SSL сертификаты получены и скопированы"
        fi

        # Обновляем nginx.conf с доменом
        sed -i "s/yourdomain.com/${DOMAIN}/g" nginx/nginx.conf
        info "nginx.conf обновлён для домена ${DOMAIN}"

        # В prod используем webhook режим
        if ! grep -q "uvicorn" .env; then
            echo "BOT_COMMAND=uvicorn bot.webhook:app --host 0.0.0.0 --port 8080" >> .env
            info "Режим запуска: webhook (uvicorn)"
        fi
    else
        success "SSL сертификаты найдены"
    fi

    COMPOSE_PROFILE="--profile prod"
else
    info "Polling режим (без webhook)"
    COMPOSE_PROFILE=""
fi

# --- Первый деплой -------------------------------------------------------
if [[ "$UPDATE_MODE" == false ]]; then
    info "Первый деплой..."

    # Устанавливаем Docker если нужно
    if ! command -v docker &>/dev/null; then
        info "Устанавливаем Docker..."
        curl -fsSL https://get.docker.com | sh
        usermod -aG docker "${USER}" || true
    fi

    # Создаём директории
    mkdir -p logs nginx/ssl
    touch logs/.gitkeep

    info "Собираем образы..."
    docker compose ${COMPOSE_PROFILE} build --no-cache

    info "Запускаем базы данных..."
    docker compose up -d postgres redis
    sleep 5  # ждём готовности postgres

    info "Применяем миграции..."
    docker compose run --rm migrations alembic upgrade head

    info "Запускаем все сервисы..."
    docker compose ${COMPOSE_PROFILE} up -d

# --- Обновление ----------------------------------------------------------
else
    info "Режим обновления..."

    info "Пересобираем образы..."
    docker compose ${COMPOSE_PROFILE} build bot celery_worker celery_beat

    info "Применяем новые миграции..."
    docker compose run --rm migrations alembic upgrade head

    info "Перезапускаем сервисы (zero-downtime)..."
    docker compose ${COMPOSE_PROFILE} up -d --no-deps bot celery_worker celery_beat
fi

# --- Проверка здоровья ---------------------------------------------------
info "Проверяем статус контейнеров..."
sleep 5

FAILED=()
for svc in postgres redis bot celery_worker celery_beat; do
    STATUS=$(docker compose ps -q "${svc}" 2>/dev/null | xargs -I{} docker inspect --format='{{.State.Status}}' {} 2>/dev/null || echo "missing")
    if [[ "$STATUS" == "running" ]]; then
        success "  ${svc}: running"
    else
        FAILED+=("${svc}")
        warn "  ${svc}: ${STATUS}"
    fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    error "Следующие сервисы не запустились: ${FAILED[*]}"
    echo ""
    echo "Смотри логи: docker compose logs ${FAILED[*]}"
fi

# --- Итог ----------------------------------------------------------------
echo ""
echo -e "${GREEN}${BOLD}═══════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Деплой успешно завершён! 🎉${NC}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}Полезные команды:${NC}"
echo -e "  make logs        — логи бота"
echo -e "  make logs-all    — все логи"
echo -e "  make ps          — статус контейнеров"
echo -e "  make shell-db    — консоль PostgreSQL"
echo -e "  ./deploy.sh --update  — обновить после git pull"
echo ""
