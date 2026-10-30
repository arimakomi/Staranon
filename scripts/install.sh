#!/usr/bin/env bash
# ==============================================================================
# AnonBot Fast & Complete Installer (Ubuntu 20.04 / 22.04 / 24.04)
# GitHub: https://github.com/arimakomi/staranon
# One-Liner:
#   bash <(curl -fsSL https://raw.githubusercontent.com/arimakomi/staranon/main/install.sh)
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

[[ $EUID -ne 0 ]] && { echo -e "${RED}❌ لطفاً با دسترسی root یا sudo اجرا کنید.${NC}"; exit 1; }

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}   🚀 نصب‌کننده سریع ربات صندوق پیام ناشناس (AnonBot)       ${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# ۱. جمع‌آوری تنظیمات (سریع با پیش‌فرض‌های هوشمند + گزینه پیشرفته)
PROXY_ENABLED="false"; PROXY_URL="$CLI_PROXY_URL"; USE_PROXY_FOR_INSTALL="n"
API_BASE_URL="$CLI_API_BASE_URL"; BOT_TOKEN="$CLI_TOKEN"; BOT_USERNAME=""
SUPER_ADMINS="$CLI_ADMIN_ID"; DB_TYPE="$CLI_DB_TYPE"
DATABASE_URL="sqlite+aiosqlite:///${VAR_DIR}/anonbot.db"
REDIS_URL="$CLI_REDIS_URL"; RUN_MODE="$CLI_RUN_MODE"
WEBHOOK_HOST="${CLI_WEBHOOK_HOST:-https://bot.example.com}"
FORCE_CHANNEL="$CLI_FORCE_CHANNEL"

if [[ "$NON_INTERACTIVE" == false ]]; then
  read -r -p "۱) آیا برای اتصال به تلگرام نیاز به پروکسی (SOCKS5/HTTP) دارید؟ [y/N]: " NEED_PROXY < /dev/tty
  if [[ "${NEED_PROXY,,}" == "y" ]]; then
    read -r -p "   آدرس پروکسی (مثال socks5://user:pass@127.0.0.1:1080): " PROXY_URL < /dev/tty
    [[ -n "$PROXY_URL" ]] && PROXY_ENABLED="true"
    read -r -p "   استفاده از همین پروکسی برای دانلود پکیج‌ها و گیت‌هاب؟ [y/N]: " USE_PROXY_FOR_INSTALL < /dev/tty
  fi

  while true; do
    read -r -p "۲) توکن ربات تلگرام (@BotFather): " BOT_TOKEN < /dev/tty
    [[ -z "$BOT_TOKEN" ]] && continue
    CHECK_BASE="${API_BASE_URL:-https://api.telegram.org}"
    CURL_ARGS=(-sS --max-time 15)
    [[ "$PROXY_ENABLED" == "true" && -n "$PROXY_URL" ]] && CURL_ARGS+=(-x "$PROXY_URL")
    echo -e "${YELLOW}⏳ در حال تست توکن با getMe...${NC}"
    RESP=$(curl "${CURL_ARGS[@]}" "${CHECK_BASE}/bot${BOT_TOKEN}/getMe" || true)
    if echo "$RESP" | grep -q '"ok":true'; then
      BOT_USERNAME=$(echo "$RESP" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')
      echo -e "${GREEN}✅ توکن تایید شد: @${BOT_USERNAME}${NC}"
      break
    else
      echo -e "${RED}❌ خطا در تایید توکن یا اتصال شبکه: ${RESP}${NC}"
      read -r -p "   ادامه بدون تست؟ [y/N]: " SKIP_CHECK < /dev/tty
      [[ "${SKIP_CHECK,,}" == "y" ]] && break
    fi
  done

  while [[ -z "$SUPER_ADMINS" ]]; do
    read -r -p "۳) آیدی عددی ادمین کل (مثلاً 123456789): " SUPER_ADMINS < /dev/tty
  done

  read -r -p "۴) تنظیمات پیشرفته (MySQL / Redis / Webhook / جوین اجباری)؟ [y/N - پیش‌فرض: SQLite + Polling]: " ADV_CHOICE < /dev/tty
  if [[ "${ADV_CHOICE,,}" == "y" ]]; then
    read -r -p "   نوع دیتابیس [1=SQLite پیش‌فرض, 2=MySQL]: " DB_CHOICE < /dev/tty
    if [[ "$DB_CHOICE" == "2" ]]; then
      DB_TYPE="mysql"
      read -r -p "   میزبان MySQL [127.0.0.1]: " M_HOST < /dev/tty; M_HOST="${M_HOST:-127.0.0.1}"
      read -r -p "   پورت MySQL [3306]: " M_PORT < /dev/tty; M_PORT="${M_PORT:-3306}"
      read -r -p "   نام دیتابیس [anonbot]: " M_DB < /dev/tty; M_DB="${M_DB:-anonbot}"
      read -r -p "   نام کاربری MySQL [anonbot]: " M_USER < /dev/tty; M_USER="${M_USER:-anonbot}"
      read -r -s -p "   رمز عبور MySQL: " M_PASS < /dev/tty; echo ""
      DATABASE_URL="mysql+aiomysql://${M_USER}:${M_PASS}@${M_HOST}:${M_PORT}/${M_DB}?charset=utf8mb4"
    fi
    read -r -p "   آدرس Redis اختیاری (خالی=حافظه/دیتابیس): " REDIS_URL < /dev/tty
    read -r -p "   سرور Bot API سفارشی (خالی=پیش‌فرض تلگرام): " API_BASE_URL < /dev/tty
    if [[ "$PROXY_ENABLED" == "false" ]]; then
      read -r -p "   حالت اجرا [1=Polling پیش‌فرض, 2=Webhook]: " MODE_CHOICE < /dev/tty
      if [[ "$MODE_CHOICE" == "2" ]]; then
        RUN_MODE="webhook"
        read -r -p "   دامنه HTTPS وب‌هوک (مثال https://bot.domain.com): " WEBHOOK_HOST < /dev/tty
      fi
    fi
    read -r -p "   یوزرنیم کانال جوین اجباری اولیه (مثال @MyChannel یا خالی): " FORCE_CHANNEL < /dev/tty
  fi
else
  [[ -z "$BOT_TOKEN" || -z "$SUPER_ADMINS" ]] && { echo -e "${RED}❌ در حالت غیرتعاملی --token و --admin-id الزامی است.${NC}"; exit 1; }
  [[ -n "$PROXY_URL" ]] && PROXY_ENABLED="true"
  [[ "$DB_TYPE" == "mysql" && -n "$CLI_MYSQL_DSN" ]] && DATABASE_URL="$CLI_MYSQL_DSN"
fi

if [[ "${USE_PROXY_FOR_INSTALL,,}" == "y" && -n "$PROXY_URL" ]]; then
  export http_proxy="$PROXY_URL" https_proxy="$PROXY_URL" ALL_PROXY="$PROXY_URL"
fi

# ۲. نصب پیش‌نیازهای سیستم و ساخت کاربر/پوشه‌ها
echo -e "${YELLOW}📦 در حال نصب پکیج‌های پایه سیستم و آماده‌سازی دایرکتوری‌ها...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev build-essential curl tar sqlite3 openssl

id "$APP_USER" &>/dev/null || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$ETC_DIR" "$VAR_DIR" "$LOG_DIR" "$BACKUP_DIR"

# ۳. دریافت سورس پروژه (از دایرکتوری فعلی یا دانلود مستقیم از گیت‌هاب)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR=""
if [[ -f "${SCRIPT_DIR}/requirements.txt" && -d "${SCRIPT_DIR}/app" ]]; then
  SOURCE_DIR="$SCRIPT_DIR"
elif [[ -f "$(dirname "$SCRIPT_DIR")/requirements.txt" && -d "$(dirname "$SCRIPT_DIR")/app" ]]; then
  SOURCE_DIR="$(dirname "$SCRIPT_DIR")"
else
  echo -e "${YELLOW}🌐 در حال دانلود آخرین نسخه از گیت‌هاب (${GITHUB_REPO})...${NC}"
  TMP_DL=$(mktemp -d)
  DL_ARGS=(-fsSL --max-time 60)
  [[ "${USE_PROXY_FOR_INSTALL,,}" == "y" && -n "$PROXY_URL" ]] && DL_ARGS+=(-x "$PROXY_URL")
  if ! curl "${DL_ARGS[@]}" "${GITHUB_REPO}/archive/refs/heads/main.tar.gz" -o "${TMP_DL}/repo.tar.gz" 2>/dev/null; then
    curl "${DL_ARGS[@]}" "${GITHUB_REPO}/archive/refs/heads/master.tar.gz" -o "${TMP_DL}/repo.tar.gz"
  fi
  tar -xzf "${TMP_DL}/repo.tar.gz" -C "$TMP_DL"
  EXTRACTED=$(find "$TMP_DL" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [[ -f "${EXTRACTED}/requirements.txt" ]]; then
    SOURCE_DIR="$EXTRACTED"
  elif [[ -f "${EXTRACTED}/anonbot/requirements.txt" ]]; then
    SOURCE_DIR="${EXTRACTED}/anonbot"
  else
    echo -e "${RED}❌ فایل‌های پروژه در آرشیو گیت‌هاب یافت نشد.${NC}"
    rm -rf "$TMP_DL"; exit 1
  fi
fi

cp -r "${SOURCE_DIR}/app" "${SOURCE_DIR}/cli" "${SOURCE_DIR}/alembic" "${SOURCE_DIR}/systemd" "${SOURCE_DIR}/scripts" "$APP_DIR/"
cp "${SOURCE_DIR}/requirements.txt" "${SOURCE_DIR}/alembic.ini" "${SOURCE_DIR}/.env.example" "$APP_DIR/"
[[ -f "${SOURCE_DIR}/install.sh" ]] && cp "${SOURCE_DIR}/install.sh" "$APP_DIR/"
[[ -f "${SOURCE_DIR}/uninstall.sh" ]] && cp "${SOURCE_DIR}/uninstall.sh" "$APP_DIR/"
[[ -n "${TMP_DL:-}" && -d "${TMP_DL:-}" ]] && rm -rf "$TMP_DL"

# ۴. ساخت Virtualenv (مطابق PEP 668 در Ubuntu 24.04) و نصب وابستگی‌ها
echo -e "${YELLOW}🐍 در حال ساخت محیط مجازی و نصب کتابخانه‌های پایتون...${NC}"
[[ ! -d "$VENV_DIR" ]] && python3 -m venv "$VENV_DIR"
PIP_ARGS=(--quiet --upgrade)
[[ "${USE_PROXY_FOR_INSTALL,,}" == "y" && -n "$PROXY_URL" ]] && PIP_ARGS+=(--proxy "$PROXY_URL")
"${VENV_DIR}/bin/pip" install "${PIP_ARGS[@]}" pip wheel setuptools
"${VENV_DIR}/bin/pip" install "${PIP_ARGS[@]}" -r "${APP_DIR}/requirements.txt"

# ۵. تولید کلیدهای رمزنگاری (با حفظ کلیدهای قبلی در صورت نصب مجدد)
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

# ۶. اجرای مایگریشن دیتابیس و ثبت کانال جوین اجباری اولیه
echo -e "${YELLOW}🗄 در حال ساخت جداول دیتابیس...${NC}"
(cd "$APP_DIR" && ANONBOT_ENV_FILE="$ENV_FILE" "${VENV_DIR}/bin/alembic" upgrade head)
chown -R "${APP_USER}:${APP_USER}" "$VAR_DIR"

if [[ -n "$FORCE_CHANNEL" ]]; then
  ANONBOT_ENV_FILE="$ENV_FILE" "${VENV_DIR}/bin/python" - "$FORCE_CHANNEL" <<'PYEOF'
import asyncio, sys
from app.db.session import AsyncSessionLocal
from app.db.repositories import ForceJoinRepo
async def main(ch):
    async with AsyncSessionLocal() as s:
        await ForceJoinRepo.add_channel(s, ch, ch, f"https://t.me/{ch.lstrip('@')}")
        await s.commit()
asyncio.run(main(sys.argv[1]))
PYEOF
fi

# ۷. نصب ابزار CLI (anonbot) و سرویس systemd
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

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}🎉 نصب و راه‌اندازی AnonBot با موفقیت کامل شد!${NC}"
echo -e "   • وضعیت سرویس : $(systemctl is-active anonbot.service)"
echo -e "   • مدیریت ربات : ${CYAN}sudo anonbot${NC}"
echo -e "   • بررسی سلامت : ${CYAN}sudo anonbot doctor${NC}"
echo -e "   • آپدیت گیت‌هاب: ${CYAN}sudo anonbot update${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
