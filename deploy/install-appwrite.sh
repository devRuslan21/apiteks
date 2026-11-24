#!/usr/bin/env bash
# Установка Appwrite (self-hosted, бесплатно) на Linux-сервер (Ubuntu/Debian) с доступом по IP.
# Запуск на сервере:  sudo bash install-appwrite.sh [IP_сервера]
set -euo pipefail

APPWRITE_VERSION="2.7.4"
INSTALL_DIR="/opt/appwrite"

if [[ $EUID -ne 0 ]]; then
  echo "Запустите через sudo: sudo bash $0 [IP_сервера]"
  exit 1
fi

SERVER_IP="${1:-}"
if [[ -z "$SERVER_IP" ]]; then
  SERVER_IP="$(curl -fsS4 --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')"
fi
echo "IP сервера: $SERVER_IP"

if ! command -v docker >/dev/null 2>&1; then
  echo "Устанавливаю Docker..."
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  echo "Открываю порты 80 и 443 в ufw..."
  ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
echo "Устанавливаю Appwrite $APPWRITE_VERSION в $INSTALL_DIR/appwrite ..."
docker run --rm \
  --volume /var/run/docker.sock:/var/run/docker.sock \
  --volume "$INSTALL_DIR/appwrite:/usr/src/code/appwrite:rw" \
  --entrypoint="install" \
  "appwrite/appwrite:$APPWRITE_VERSION" \
  --http-port=80 --https-port=443 --domain="$SERVER_IP" --interactive=N

echo "Жду запуска Appwrite (до 5 минут)..."
for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 "http://127.0.0.1/v1/health/version" >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
curl -fsS "http://127.0.0.1/v1/health/version" && echo

cat <<EOF

Appwrite запущен.

1. Сразу откройте консоль http://$SERVER_IP и зарегистрируйтесь.
   Первый зарегистрированный пользователь становится владельцем, остальные регистрации закрыты.
2. Создайте проект, затем API-ключ (Overview -> Integrations -> API keys)
   с правами на Database и Storage.
3. В .env бота укажите:
     APPWRITE_ENDPOINT=https://$SERVER_IP/v1
     APPWRITE_SELF_SIGNED=true
     APPWRITE_PROJECT_ID=<ID проекта>
     APPWRITE_API_KEY=<API-ключ>

Файлы установки: $INSTALL_DIR/appwrite (docker-compose.yml и .env с секретами).
EOF
