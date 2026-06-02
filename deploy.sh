#!/usr/bin/env bash
set -euo pipefail

APP="/opt/tg-stb-control"
SERVICE="tg-stb-control"
APP_USER="tg-stb-control"
APP_GROUP="tg-stb-control"

cd "$APP"

echo "==> Pulling latest code"
sudo -u "$APP_USER" git pull --ff-only

echo "==> Ensuring ownership"
sudo chown -R "$APP_USER:$APP_GROUP" "$APP"

echo "==> Ensuring virtualenv exists"
if [ ! -x "$APP/.venv/bin/python" ]; then
  sudo -u "$APP_USER" python3 -m venv "$APP/.venv"
fi

echo "==> Installing dependencies"
sudo -u "$APP_USER" "$APP/.venv/bin/python" -m pip install --upgrade pip
sudo -u "$APP_USER" "$APP/.venv/bin/python" -m pip install -r "$APP/requirements.txt"

echo "==> Checking server-only secrets"
if [ ! -f "$APP/.env" ]; then
  echo "ERROR: $APP/.env does not exist"
  exit 1
fi

sudo chown "$APP_USER:$APP_GROUP" "$APP/.env"
sudo chmod 600 "$APP/.env"

if [ -d "$APP/.ssh" ]; then
  sudo chown -R "$APP_USER:$APP_GROUP" "$APP/.ssh"
  sudo chmod 700 "$APP/.ssh"
  sudo find "$APP/.ssh" -type f -exec chmod 600 {} \;
fi

echo "==> Restarting service"
sudo systemctl restart "$SERVICE"

echo "==> Service status"
sudo systemctl status "$SERVICE" --no-pager