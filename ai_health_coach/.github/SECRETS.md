# GitHub Secrets — настройка

Перейди: **GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

## Обязательные секреты для деплоя

| Secret | Описание | Пример |
|--------|----------|--------|
| `VPS_HOST` | IP или домен сервера | `123.45.67.89` |
| `VPS_USER` | SSH пользователь | `ubuntu` |
| `VPS_SSH_KEY` | Приватный SSH ключ (весь текст) | `-----BEGIN OPENSSH...` |
| `VPS_PORT` | SSH порт (опционально) | `22` |
| `APP_DIR` | Путь к проекту на сервере | `/opt/ai_health_coach` |

## Как сгенерировать SSH ключ для деплоя

```bash
# На локальной машине
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy

# Скопировать публичный ключ на сервер
ssh-copy-id -i ~/.ssh/github_deploy.pub ubuntu@your-vps-ip

# Содержимое приватного ключа вставить в секрет VPS_SSH_KEY:
cat ~/.ssh/github_deploy
```

## Подготовка VPS для автодеплоя

```bash
# На сервере: создать директорию и клонировать репо
mkdir -p /opt/ai_health_coach
cd /opt/ai_health_coach
git clone https://github.com/youruser/ai-health-coach.git .
cp .env.example .env
# Заполнить .env вручную!
nano .env
```

## Переменные окружения в GitHub (не секреты)

Можно добавить через **Settings → Secrets → Variables** (не секреты):

| Variable | Значение |
|----------|----------|
| `DEPLOY_ENVIRONMENT` | `production` |
