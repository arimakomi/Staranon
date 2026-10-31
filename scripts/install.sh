#!/usr/bin/env bash
# ==============================================================================
# AnonBot Self-Healing Complete Installer (Ubuntu 20.04 / 22.04 / 24.04)
# GitHub: https://github.com/arimakomi/staranon
# One-Liner (with cache-buster):
#   bash <(curl -fsSL "https://raw.githubusercontent.com/arimakomi/staranon/main/install.sh?t=$(date +%s)")
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
APP_USER="anonbot"
APP_DIR="/opt/anonbot"
VENV_DIR="/opt/anonbot/venv"
ETC_DIR="/etc/anonbot"
ENV_FILE="/etc/anonbot/.env"
VAR_DIR="/var/lib/anonbot"
LOG_DIR="/var/log/anonbot"
BACKUP_DIR="/var/backups/anonbot"
GITHUB_REPO="https://github.com/arimakomi/staranon"

NON_INTERACTIVE=false
CLI_TOKEN=""; CLI_ADMIN_ID=""; CLI_DB_TYPE="sqlite"; CLI_MYSQL_DSN=""
CLI_REDIS_URL=""; CLI_RUN_MODE="polling"; CLI_WEBHOOK_HOST=""
CLI_PROXY_URL=""; CLI_API_BASE_URL=""; CLI_FORCE_CHANNEL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive) NON_INTERACTIVE=true; shift ;;
    --token) CLI_TOKEN="$2"; shift 2 ;;
    --admin-id) CLI_ADMIN_ID="$2"; shift 2 ;;
    --db-type) CLI_DB_TYPE="$2"; shift 2 ;;
    --mysql-dsn) CLI_MYSQL_DSN="$2"; shift 2 ;;
    --redis-url) CLI_REDIS_URL="$2"; shift 2 ;;
    --run-mode) CLI_RUN_MODE="$2"; shift 2 ;;
    --webhook-host) CLI_WEBHOOK_HOST="$2"; shift 2 ;;
    --proxy) CLI_PROXY_URL="$2"; shift 2 ;;
    --api-base-url) CLI_API_BASE_URL="$2"; shift 2 ;;
    --force-channel) CLI_FORCE_CHANNEL="$2"; shift 2 ;;
    *) shift ;;
  esac
done

[[ $EUID -ne 0 ]] && { echo -e "${RED}[ERROR] Please run this installer as root or with sudo.${NC}"; exit 1; }

echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}   AnonBot Multi-Inbox Telegram Bot - Fast Installer        ${NC}"
echo -e "${CYAN}============================================================${NC}"

# 1. Collect Configuration
PROXY_ENABLED="false"; PROXY_URL="$CLI_PROXY_URL"; USE_PROXY_FOR_INSTALL="n"
API_BASE_URL="$CLI_API_BASE_URL"; BOT_TOKEN="$CLI_TOKEN"; BOT_USERNAME=""
SUPER_ADMINS="$CLI_ADMIN_ID"; DB_TYPE="$CLI_DB_TYPE"
DATABASE_URL="sqlite+aiosqlite:///${VAR_DIR}/anonbot.db"
M_HOST="127.0.0.1"; M_PORT="3306"; M_DB="anonbot"; M_USER="anonbot"; M_PASS=""
REDIS_URL="$CLI_REDIS_URL"; RUN_MODE="$CLI_RUN_MODE"
WEBHOOK_HOST="${CLI_WEBHOOK_HOST:-https://bot.example.com}"
FORCE_CHANNEL="$CLI_FORCE_CHANNEL"

if [[ "$NON_INTERACTIVE" == false ]]; then
  read -r -p "1) Use proxy (SOCKS5/HTTP) for Telegram API? [y/N]: " NEED_PROXY < /dev/tty
  if [[ "${NEED_PROXY,,}" == "y" ]]; then
    read -r -p "   Proxy URL (e.g. socks5://user:pass@127.0.0.1:1080): " PROXY_URL < /dev/tty
    [[ -n "$PROXY_URL" ]] && PROXY_ENABLED="true"
    read -r -p "   Also use this proxy for GitHub & pip downloads? [y/N]: " USE_PROXY_FOR_INSTALL < /dev/tty
  fi

  while true; do
    read -r -p "2) Telegram Bot Token (@BotFather): " BOT_TOKEN < /dev/tty
    [[ -z "$BOT_TOKEN" ]] && continue
    CHECK_BASE="${API_BASE_URL:-https://api.telegram.org}"
    CURL_ARGS=(-sS --max-time 15)
    [[ "$PROXY_ENABLED" == "true" && -n "$PROXY_URL" ]] && CURL_ARGS+=(-x "$PROXY_URL")
    echo -e "${YELLOW}[*] Verifying token via getMe...${NC}"
    RESP=$(curl "${CURL_ARGS[@]}" "${CHECK_BASE}/bot${BOT_TOKEN}/getMe" || true)
    if echo "$RESP" | grep -q '"ok":true'; then
      BOT_USERNAME=$(echo "$RESP" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')
      echo -e "${GREEN}[OK] Verified bot: @${BOT_USERNAME}${NC}"
      break
    else
      echo -e "${RED}[!] Token verification failed: ${RESP}${NC}"
      read -r -p "   Continue without verification? [y/N]: " SKIP_CHECK < /dev/tty
      [[ "${SKIP_CHECK,,}" == "y" ]] && break
    fi
  done

  while [[ -z "$SUPER_ADMINS" ]]; do
    read -r -p "3) Super Admin Telegram User ID (e.g. 123456789): " SUPER_ADMINS < /dev/tty
  done

  read -r -p "4) Configure advanced options (MySQL / Redis / Webhook / Force-Join)? [y/N - Default: SQLite + Polling]: " ADV_CHOICE < /dev/tty
  if [[ "${ADV_CHOICE,,}" == "y" ]]; then
    read -r -p "   Database Type [1=SQLite (default), 2=MySQL]: " DB_CHOICE < /dev/tty
    if [[ "$DB_CHOICE" == "2" ]]; then
      DB_TYPE="mysql"
      read -r -p "   MySQL Host [127.0.0.1]: " M_HOST < /dev/tty; M_HOST="${M_HOST:-127.0.0.1}"
      read -r -p "   MySQL Port [3306]: " M_PORT < /dev/tty; M_PORT="${M_PORT:-3306}"
      read -r -p "   MySQL Database Name [anonbot]: " M_DB < /dev/tty; M_DB="${M_DB:-anonbot}"
      read -r -p "   MySQL Username [anonbot]: " M_USER < /dev/tty; M_USER="${M_USER:-anonbot}"
      read -r -s -p "   MySQL Password (leave empty to auto-generate on local MySQL): " M_PASS < /dev/tty; echo ""
    fi
    read -r -p "   Optional Redis URL (leave empty for memory/DB fallback): " REDIS_URL < /dev/tty
    read -r -p "   Custom Bot API Server URL (leave empty for default): " API_BASE_URL < /dev/tty
    if [[ "$PROXY_ENABLED" == "false" ]]; then
      read -r -p "   Run Mode [1=Polling (default), 2=Webhook]: " MODE_CHOICE < /dev/tty
      if [[ "$MODE_CHOICE" == "2" ]]; then
        RUN_MODE="webhook"
        read -r -p "   Webhook HTTPS Base URL (e.g. https://bot.domain.com): " WEBHOOK_HOST < /dev/tty
      fi
    fi
    read -r -p "   Initial Force-Join Channel (e.g. @MyChannel or leave empty): " FORCE_CHANNEL < /dev/tty
  fi
else
  [[ -z "$BOT_TOKEN" || -z "$SUPER_ADMINS" ]] && { echo -e "${RED}[ERROR] --token and --admin-id are required in non-interactive mode.${NC}"; exit 1; }
  [[ -n "$PROXY_URL" ]] && PROXY_ENABLED="true"
  [[ "$DB_TYPE" == "mysql" && -n "$CLI_MYSQL_DSN" ]] && DATABASE_URL="$CLI_MYSQL_DSN"
fi

if [[ "${USE_PROXY_FOR_INSTALL,,}" == "y" && -n "$PROXY_URL" ]]; then
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL" ALL_PROXY="$PROXY_URL"
fi

# 2. Install OS Packages & Prepare Directories (Plus Auto-Provision Local MySQL if selected)
echo -e "${YELLOW}[*] Installing system packages and preparing directories...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
APT_PKGS=(python3 python3-venv python3-dev build-essential curl tar sqlite3 openssl)
if [[ "$DB_TYPE" == "mysql" && ( "$M_HOST" == "127.0.0.1" || "$M_HOST" == "localhost" ) ]]; then
  APT_PKGS+=(mysql-server)
fi
apt-get install -y -qq "${APT_PKGS[@]}"

if [[ "$DB_TYPE" == "mysql" && -z "$CLI_MYSQL_DSN" ]]; then
  if [[ -z "$M_PASS" ]]; then
    M_PASS=$(openssl rand -hex 12)
    echo -e "${CYAN}[*] Generated MySQL password for user '${M_USER}': ${M_PASS}${NC}"
  fi
  if [[ "$M_HOST" == "127.0.0.1" || "$M_HOST" == "localhost" ]]; then
    echo -e "${YELLOW}[*] Configuring local MySQL database '${M_DB}' and user '${M_USER}'...${NC}"
    systemctl enable --now mysql >/dev/null 2>&1 || true
    mysql -u root <<SQL || true
CREATE DATABASE IF NOT EXISTS \`${M_DB}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${M_USER}'@'127.0.0.1' IDENTIFIED BY '${M_PASS}';
CREATE USER IF NOT EXISTS '${M_USER}'@'localhost' IDENTIFIED BY '${M_PASS}';
ALTER USER '${M_USER}'@'127.0.0.1' IDENTIFIED BY '${M_PASS}';
ALTER USER '${M_USER}'@'localhost' IDENTIFIED BY '${M_PASS}';
GRANT ALL PRIVILEGES ON \`${M_DB}\`.* TO '${M_USER}'@'127.0.0.1';
GRANT ALL PRIVILEGES ON \`${M_DB}\`.* TO '${M_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL
  fi
  DATABASE_URL="mysql+aiomysql://${M_USER}:${M_PASS}@${M_HOST}:${M_PORT}/${M_DB}?charset=utf8mb4"
fi

id "$APP_USER" &>/dev/null || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$ETC_DIR" "$VAR_DIR" "$LOG_DIR" "$BACKUP_DIR"

# 3. Fetch Application Source (Local directory or GitHub Tarball) & Copy Safely
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR=""
if [[ -d "${SCRIPT_DIR}/app" ]]; then
  SOURCE_DIR="$SCRIPT_DIR"
elif [[ -d "$(dirname "$SCRIPT_DIR")/app" ]]; then
  SOURCE_DIR="$(dirname "$SCRIPT_DIR")"
else
  echo -e "${YELLOW}[*] Downloading latest release from GitHub (${GITHUB_REPO})...${NC}"
  TMP_DL=$(mktemp -d)
  DL_ARGS=(-fsSL --max-time 60)
  [[ "${USE_PROXY_FOR_INSTALL,,}" == "y" && -n "$PROXY_URL" ]] && DL_ARGS+=(-x "$PROXY_URL")
  if ! curl "${DL_ARGS[@]}" "${GITHUB_REPO}/archive/refs/heads/main.tar.gz?t=$(date +%s)" -o "${TMP_DL}/repo.tar.gz" 2>/dev/null; then
    curl "${DL_ARGS[@]}" "${GITHUB_REPO}/archive/refs/heads/master.tar.gz?t=$(date +%s)" -o "${TMP_DL}/repo.tar.gz"
  fi
  tar -xzf "${TMP_DL}/repo.tar.gz" -C "$TMP_DL"
  EXTRACTED=$(find "$TMP_DL" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [[ -d "${EXTRACTED}/app" ]]; then
    SOURCE_DIR="$EXTRACTED"
  elif [[ -d "${EXTRACTED}/anonbot/app" ]]; then
    SOURCE_DIR="${EXTRACTED}/anonbot"
  else
    echo -e "${RED}[ERROR] Directory 'app/' not found in GitHub repository (${GITHUB_REPO}).${NC}"
    rm -rf "$TMP_DL"; exit 1
  fi
fi

# Copy all existing files/folders without failing on missing optional directories
cp -a "${SOURCE_DIR}/." "$APP_DIR/"
[[ -n "${TMP_DL:-}" && -d "${TMP_DL:-}" ]] && rm -rf "$TMP_DL"

# Self-Heal: Generate requirements.txt if missing from repo
if [[ ! -f "${APP_DIR}/requirements.txt" ]]; then
  cat > "${APP_DIR}/requirements.txt" <<'REQEOF'
aiogram>=3.13.1,<4.0.0
aiohttp>=3.10.5,<4.0.0
aiohttp-socks>=0.9.0,<1.0.0
SQLAlchemy[asyncio]>=2.0.35,<3.0.0
aiosqlite>=0.20.0,<1.0.0
aiomysql>=0.2.0,<1.0.0
cryptography>=43.0.1,<45.0.0
alembic>=1.13.3,<2.0.0
redis>=5.1.1,<6.0.0
python-dotenv>=1.0.1,<2.0.0
typer>=0.12.5,<1.0.0
rich>=13.9.2,<14.0.0
pydantic>=2.9.2,<3.0.0
pytest>=8.3.3,<9.0.0
pytest-asyncio>=0.24.0,<1.0.0
REQEOF
fi

# Self-Heal: Generate systemd/anonbot.service if missing from repo
mkdir -p "${APP_DIR}/systemd"
cat > "${APP_DIR}/systemd/anonbot.service" <<EOF
[Unit]
Description=AnonBot Multi-Inbox Anonymous Telegram Bot
After=network-online.target mysql.service redis.service
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="ANONBOT_ENV_FILE=${ENV_FILE}"
Environment="PYTHONPATH=${APP_DIR}"
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python -m app.bot
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

# 4. Create Isolated Virtualenv (PEP 668 compliant on Ubuntu 24.04)
echo -e "${YELLOW}[*] Creating Python virtual environment and installing dependencies...${NC}"
[[ ! -d "$VENV_DIR" ]] && python3 -m venv "$VENV_DIR"
PIP_ARGS=(--quiet --upgrade)
[[ "${USE_PROXY_FOR_INSTALL,,}" == "y" && -n "$PROXY_URL" ]] && PIP_ARGS+=(--proxy "$PROXY_URL")
"${VENV_DIR}/bin/pip" install "${PIP_ARGS[@]}" pip wheel setuptools
"${VENV_DIR}/bin/pip" install "${PIP_ARGS[@]}" -r "${APP_DIR}/requirements.txt"

# 5. Generate Cryptographic Keys (Preserve existing keys on re-install)
EXISTING_HMAC=""; EXISTING_FERNET=""
if [[ -f "$ENV_FILE" ]]; then
  EXISTING_HMAC=$(grep -E '^HMAC_SECRET=' "$ENV_FILE" | cut -d'=' -f2- || true)
  EXISTING_FERNET=$(grep -E '^FERNET_KEY=' "$ENV_FILE" | cut -d'=' -f2- || true)
fi
HMAC_SECRET="${EXISTING_HMAC:-$(openssl rand -hex 32)}"
FERNET_KEY="${EXISTING_FERNET:-$("${VENV_DIR}/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")}"
WEBHOOK_SECRET=$(openssl rand -hex 24)

cat > "$ENV_FILE" <<EOF
BOT_TOKEN=${BOT_TOKEN}
BOT_USERNAME=${BOT_USERNAME}
SUPER_ADMINS=${SUPER_ADMINS}
HMAC_SECRET=${HMAC_SECRET}
FERNET_KEY=${FERNET_KEY}
DATABASE_URL=${DATABASE_URL}
REDIS_URL=${REDIS_URL}
RUN_MODE=${RUN_MODE}
WEBHOOK_HOST=${WEBHOOK_HOST}
WEBHOOK_PATH=/webhook/anonbot
WEBHOOK_SECRET=${WEBHOOK_SECRET}
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8080
PROXY_ENABLED=${PROXY_ENABLED}
PROXY_URL=${PROXY_URL}
API_BASE_URL=${API_BASE_URL}
RATE_LIMIT_PER_MINUTE=15
DELETE_WINDOW_SECONDS=300
REVEAL_DAILY_LIMIT=5
REVEAL_COOLDOWN_SECONDS=3600
AUTO_BAN_REPORTS_THRESHOLD=3
AUTO_BAN_DURATION_HOURS=72
REACTION_EMOJIS=❤️,👍,😂,🔥,😢
FILTER_LINKS_DEFAULT=false
MAINTENANCE_MODE=false
LOG_LEVEL=INFO
LOG_DIR=${LOG_DIR}
BACKUP_DIR=${BACKUP_DIR}
EOF

chown -R "${APP_USER}:${APP_USER}" "$APP_DIR" "$ETC_DIR" "$VAR_DIR" "$LOG_DIR" "$BACKUP_DIR"
chmod 600 "$ENV_FILE"

# 6. Initialize Database Tables & Seed Initial Force-Join Channel
echo -e "${YELLOW}[*] Initializing database schema...${NC}"
if [[ -d "${APP_DIR}/alembic" && -f "${APP_DIR}/alembic.ini" ]]; then
  (cd "$APP_DIR" && ANONBOT_ENV_FILE="$ENV_FILE" "${VENV_DIR}/bin/alembic" upgrade head) || true
fi
ANONBOT_ENV_FILE="$ENV_FILE" PYTHONPATH="$APP_DIR" "${VENV_DIR}/bin/python" - <<'PYEOF'
import asyncio
from app.db.session import create_all_tables
asyncio.run(create_all_tables())
PYEOF
chown -R "${APP_USER}:${APP_USER}" "$VAR_DIR"

if [[ -n "$FORCE_CHANNEL" ]]; then
  ANONBOT_ENV_FILE="$ENV_FILE" PYTHONPATH="$APP_DIR" "${VENV_DIR}/bin/python" - "$FORCE_CHANNEL" <<'PYEOF'
import asyncio, sys
from sqlalchemy import select
from app.db.session import get_session_factory
from app.db.models import ForcedChannel
async def main(ch):
    factory = get_session_factory()
    async with factory() as s:
        existing = (await s.execute(select(ForcedChannel).where(ForcedChannel.chat_id == ch))).scalar_one_or_none()
        if not existing:
            s.add(ForcedChannel(chat_id=ch, title=ch, invite_link=f"https://t.me/{ch.lstrip('@')}", is_active=True))
            await s.commit()
asyncio.run(main(sys.argv[1]))
PYEOF
fi

# 7. Install CLI Wrapper & Enable systemd Service
cat > /usr/local/bin/anonbot <<'EOF'
#!/usr/bin/env bash
export ANONBOT_ENV_FILE="/etc/anonbot/.env"
export PYTHONPATH="/opt/anonbot"
exec /opt/anonbot/venv/bin/python -m cli.main "$@"
EOF
chmod +x /usr/local/bin/anonbot

cp "${APP_DIR}/systemd/anonbot.service" /etc/systemd/system/anonbot.service
systemctl daemon-reload
systemctl enable anonbot.service >/dev/null 2>&1
systemctl restart anonbot.service
sleep 2

echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}[SUCCESS] AnonBot installed and started successfully!${NC}"
echo -e "  * Service Status : $(systemctl is-active anonbot.service)"
echo -e "  * Control Panel  : ${CYAN}sudo anonbot${NC}"
echo -e "  * Health Check   : ${CYAN}sudo anonbot doctor${NC}"
echo -e "  * GitHub Update  : ${CYAN}sudo anonbot update${NC}"
echo -e "${GREEN}============================================================${NC}"
