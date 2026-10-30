#!/usr/bin/env bash
# ==============================================================================
# اسکریپت نصب خودکار و Idempotent ربات صندوق پیام ناشناس چندگانه (AnonBot)
# مخزن رسمی گیت‌هاب: https://github.com/arimakomi/staranon
# سازگار با Ubuntu 20.04 / 22.04 / 24.04 (رعایت کامل استاندارد PEP 668 با virtualenv)
# پشتیبانی از دو حالت تعاملی (Interactive) و غیرتعاملی (Non-Interactive با فلگ‌ها)
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

GITHUB_REPO="arimakomi/staranon"
GITHUB_TARBALL_URL="https://github.com/${GITHUB_REPO}/archive/refs/heads/main.tar.gz"

APP_USER="anonbot"
APP_DIR="/opt/anonbot"
ETC_DIR="/etc/anonbot"
ENV_FILE="${ETC_DIR}/.env"
DATA_DIR="/var/lib/anonbot"
LOG_DIR="/var/log/anonbot"
BACKUP_DIR="/var/backups/anonbot"
CLI_BIN="/usr/local/bin/anonbot"

NON_INTERACTIVE="false"
ARG_TOKEN=""
ARG_ADMIN_ID=""
ARG_DB_TYPE="sqlite"
ARG_MYSQL_URL=""
ARG_REDIS_URL=""
ARG_RUN_MODE="polling"
ARG_WEBHOOK_HOST=""
ARG_WEBHOOK_PORT="8080"
ARG_PROXY_URL=""
ARG_API_BASE_URL=""
ARG_FORCED_CHANNEL=""
ARG_USE_INSTALL_PROXY="false"

usage() {
    cat <<EOF
نحوه استفاده از نصب‌کننده AnonBot (${GITHUB_REPO}):
  حالت تعاملی:
    sudo bash install.sh

  حالت غیرتعاملی:
    sudo bash install.sh --non-interactive \\
      --token "123456:ABC-DEF" \\
      --admin-id "987654321" \\
      [--db-type sqlite|mysql] \\
      [--mysql-url "mysql+aiomysql://user:pass@127.0.0.1:3306/anonbot"] \\
      [--redis-url "redis://127.0.0.1:6379/0"] \\
      [--run-mode polling|webhook] \\
      [--webhook-host "https://bot.example.com"] \\
      [--webhook-port 8080] \\
      [--proxy "socks5://user:pass@127.0.0.1:1080"] \\
      [--api-base-url "https://api.telegram.org"] \\
      [--install-proxy]
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --non-interactive) NON_INTERACTIVE="true"; shift ;;
        --token) ARG_TOKEN="$2"; shift 2 ;;
        --admin-id) ARG_ADMIN_ID="$2"; shift 2 ;;
        --db-type) ARG_DB_TYPE="$2"; shift 2 ;;
        --mysql-url) ARG_MYSQL_URL="$2"; shift 2 ;;
        --redis-url) ARG_REDIS_URL="$2"; shift 2 ;;
        --run-mode) ARG_RUN_MODE="$2"; shift 2 ;;
        --webhook-host) ARG_WEBHOOK_HOST="$2"; shift 2 ;;
        --webhook-port) ARG_WEBHOOK_PORT="$2"; shift 2 ;;
        --proxy) ARG_PROXY_URL="$2"; shift 2 ;;
        --api-base-url) ARG_API_BASE_URL="$2"; shift 2 ;;
        --forced-channel) ARG_FORCED_CHANNEL="$2"; shift 2 ;;
        --install-proxy) ARG_USE_INSTALL_PROXY="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo -e "${RED}آرگومان ناشناخته: $1${NC}"; usage; exit 1 ;;
    esac
done

echo -e "${CYAN}======================================================================${NC}"
echo -e "${CYAN}   🚀 نصب‌کننده خودکار ربات صندوق پیام ناشناس چندگانه (AnonBot)       ${NC}"
echo -e "${CYAN}   📦 مخزن گیت‌هاب: https://github.com/${GITHUB_REPO}                 ${NC}"
echo -e "${CYAN}======================================================================${NC}"

# ۱. بررسی دسترسی root و سیستم‌عامل Ubuntu
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}❌ خطا: این اسکریپت باید با دسترسی root (یا sudo) اجرا شود.${NC}"
    exit 1
fi

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    if [[ "${ID:-}" != "ubuntu" && "${ID_LIKE:-}" != *"ubuntu"* && "${ID:-}" != "debian" ]]; then
        echo -e "${YELLOW}⚠️ هشدار: سیستم‌عامل شما (${PRETTY_NAME:-Unknown}) اوبونتو نیست؛ ادامه نصب با فرض سازگاری Debian/Ubuntu...${NC}"
    else
        echo -e "${GREEN}✔ سیستم‌عامل شناسایی شد: ${PRETTY_NAME}${NC}"
    fi
fi

# ۲. دریافت تنظیمات در حالت تعاملی
PROXY_ENABLED="false"
if [[ "${NON_INTERACTIVE}" == "false" ]]; then
    echo ""
    read -r -p "🌐 آیا برای اتصال ربات به تلگرام نیاز به پروکسی (SOCKS5 / HTTP) دارید؟ [y/N]: " yn_proxy < /dev/tty
    if [[ "${yn_proxy,,}" =~ ^(y|yes)$ ]]; then
        read -r -p "🔗 آدرس پروکسی را وارد کنید (مثال: socks5://user:pass@127.0.0.1:1080): " ARG_PROXY_URL < /dev/tty
        PROXY_ENABLED="true"
        read -r -p "📦 آیا هنگام دانلود از گیت‌هاب، apt و pip نیز از همین پروکسی استفاده شود؟ [y/N]: " yn_inst_proxy < /dev/tty
        if [[ "${yn_inst_proxy,,}" =~ ^(y|yes)$ ]]; then
            ARG_USE_INSTALL_PROXY="true"
        fi
    fi

    read -r -p "🌐 آدرس سرور Bot API سفارشی (اختیاری - برای پیش‌فرض تلگرام Enter بزنید): " ARG_API_BASE_URL < /dev/tty

    while [[ -z "${ARG_TOKEN}" ]]; do
        read -r -p "🤖 توکن ربات تلگرام (دریافتی از @BotFather) را وارد کنید: " ARG_TOKEN < /dev/tty
    done

    while [[ -z "${ARG_ADMIN_ID}" ]]; do
        read -r -p "👑 آیدی عددی ادمین کل ربات (مثلاً 123456789) را وارد کنید: " ARG_ADMIN_ID < /dev/tty
    done

    read -r -p "🗄 نوع دیتابیس را انتخاب کنید [1=SQLite پیش‌فرض, 2=MySQL]: " db_choice < /dev/tty
    if [[ "${db_choice}" == "2" ]]; then
        ARG_DB_TYPE="mysql"
        read -r -p "  هاست MySQL [127.0.0.1]: " m_host < /dev/tty; m_host=${m_host:-127.0.0.1}
        read -r -p "  پورت MySQL [3306]: " m_port < /dev/tty; m_port=${m_port:-3306}
        read -r -p "  نام دیتابیس [anonbot]: " m_db < /dev/tty; m_db=${m_db:-anonbot}
        read -r -p "  نام کاربری MySQL [anonbot]: " m_user < /dev/tty; m_user=${m_user:-anonbot}
        read -r -s -p "  رمز عبور MySQL: " m_pass < /dev/tty; echo ""
        ARG_MYSQL_URL="mysql+aiomysql://${m_user}:${m_pass}@${m_host}:${m_port}/${m_db}?charset=utf8mb4"
    else
        ARG_DB_TYPE="sqlite"
    fi

    read -r -p "⚡ آدرس Redis اختیاری (برای استفاده از حافظه/دیتابیس بدون Redis، کلید Enter را بزنید): " ARG_REDIS_URL < /dev/tty

    if [[ "${PROXY_ENABLED}" == "true" ]]; then
        echo -e "${YELLOW}ℹ️ به دلیل فعال بودن پروکسی، حالت اجرای ربات روی Polling تنظیم شد.${NC}"
        ARG_RUN_MODE="polling"
    else
        read -r -p "📡 حالت اجرای ربات [1=Polling پیش‌فرض, 2=Webhook]: " mode_choice < /dev/tty
        if [[ "${mode_choice}" == "2" ]]; then
            ARG_RUN_MODE="webhook"
            read -r -p "  دامنه HTTPS وب‌هوک (مثال: https://bot.example.com): " ARG_WEBHOOK_HOST < /dev/tty
            read -r -p "  پورت محلی وب‌هوک [8080]: " ARG_WEBHOOK_PORT < /dev/tty
            ARG_WEBHOOK_PORT=${ARG_WEBHOOK_PORT:-8080}
        else
            ARG_RUN_MODE="polling"
        fi
    fi

    read -r -p "📢 یوزرنیم کانال جوین اجباری اولیه (اختیاری - مثلاً @mychannel یا Enter برای رد کردن): " ARG_FORCED_CHANNEL < /dev/tty
else
    if [[ -n "${ARG_PROXY_URL}" ]]; then
        PROXY_ENABLED="true"
    fi
    if [[ -z "${ARG_TOKEN}" || -z "${ARG_ADMIN_ID}" ]]; then
        echo -e "${RED}❌ در حالت غیرتعاملی وارد کردن --token و --admin-id الزامی است.${NC}"
        exit 1
    fi
fi

# ۳. اعمال پروکسی محیطی برای apt، curl و pip در صورت درخواست کاربر
if [[ "${ARG_USE_INSTALL_PROXY}" == "true" && -n "${ARG_PROXY_URL}" ]]; then
    export http_proxy="${ARG_PROXY_URL}"
    export https_proxy="${ARG_PROXY_URL}"
    export ALL_PROXY="${ARG_PROXY_URL}"
    echo -e "${CYAN}🌐 پروکسی برای دانلود پکیج‌ها فعال شد.${NC}"
fi

# ۴. نصب پکیج‌های سیستمی مورد نیاز
echo -e "${CYAN}📦 در حال بررسی و نصب پیش‌نیازهای سیستمی (python3, venv, curl, sqlite3, tar)...${NC}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip python3-dev build-essential curl sqlite3 openssl tar ca-certificates

# ۵. ساخت کاربر سیستمی و دایرکتوری‌ها (Idempotent)
if ! id -u "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "${APP_USER}"
    echo -e "${GREEN}✔ کاربر سیستمی ${APP_USER} ساخته شد.${NC}"
fi

mkdir -p "${APP_DIR}" "${ETC_DIR}" "${DATA_DIR}" "${LOG_DIR}" "${BACKUP_DIR}"

# ۶. تهیه سورس پروژه (از پوشه محلی یا دانلود مستقیم آرشیو main.tar.gz از گیت‌هاب arimakomi/staranon)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/tmp}")" 2>/dev/null && pwd || echo "/tmp")"
PROJECT_SRC="$(cd "${SCRIPT_DIR}/.." 2>/dev/null && pwd || echo "/tmp")"

if [[ -f "${PROJECT_SRC}/requirements.txt" && -d "${PROJECT_SRC}/app" ]]; then
    if [[ "${PROJECT_SRC}" != "${APP_DIR}" ]]; then
        echo -e "${CYAN}📂 در حال کپی فایل‌های پروژه از مسیر محلی به ${APP_DIR}...${NC}"
        cp -r "${PROJECT_SRC}/." "${APP_DIR}/"
    fi
elif [[ -f "${PROJECT_SRC}/anonbot/requirements.txt" && -d "${PROJECT_SRC}/anonbot/app" ]]; then
    echo -e "${CYAN}📂 در حال کپی فایل‌های پروژه از زیرپوشه anonbot به ${APP_DIR}...${NC}"
    cp -r "${PROJECT_SRC}/anonbot/." "${APP_DIR}/"
else
    echo -e "${CYAN}🌐 در حال دانلود مستقیم آخرین نسخه از گیت‌هاب (${GITHUB_TARBALL_URL})...${NC}"
    TMP_DL_DIR="$(mktemp -d /tmp/staranon_dl.XXXXXX)"
    curl -fsSL "${GITHUB_TARBALL_URL}" -o "${TMP_DL_DIR}/main.tar.gz"
    tar -xzf "${TMP_DL_DIR}/main.tar.gz" -C "${TMP_DL_DIR}"
    EXTRACTED_ROOT="$(find "${TMP_DL_DIR}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    if [[ -f "${EXTRACTED_ROOT}/anonbot/requirements.txt" ]]; then
        cp -r "${EXTRACTED_ROOT}/anonbot/." "${APP_DIR}/"
    elif [[ -f "${EXTRACTED_ROOT}/requirements.txt" ]]; then
        cp -r "${EXTRACTED_ROOT}/." "${APP_DIR}/"
    else
        echo -e "${RED}❌ خطا: فایل‌های پروژه در آرشیو گیت‌هاب یافت نشد.${NC}"
        rm -rf "${TMP_DL_DIR}"
        exit 1
    fi
    rm -rf "${TMP_DL_DIR}"
fi

# ۷. ساخت محیط مجازی پایتون (virtualenv) برای رعایت کامل PEP 668 در Ubuntu 24.04
if [[ ! -d "${APP_DIR}/venv" ]]; then
    echo -e "${CYAN}🐍 در حال ساخت محیط مجازی پایتون در ${APP_DIR}/venv...${NC}"
    python3 -m venv "${APP_DIR}/venv"
fi

echo -e "${CYAN}📥 در حال نصب وابستگی‌های پایتون داخل virtualenv...${NC}"
"${APP_DIR}/venv/bin/pip" install --upgrade pip wheel
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

# ۸. تست فوری اتصال به تلگرام (getMe) قبل از نهایی‌سازی
echo -e "${CYAN}🔌 در حال تست اتصال به تلگرام (getMe)...${NC}"
BOT_USERNAME_DETECTED=$(PYTHONPATH="${APP_DIR}" "${APP_DIR}/venv/bin/python" - <<PYEOF
import asyncio, sys
from app.services.telegram_helpers import test_telegram_connection
ok, info, latency = asyncio.run(
    test_telegram_connection(
        bot_token="${ARG_TOKEN}",
        proxy_url="${ARG_PROXY_URL}" if "${PROXY_ENABLED}" == "true" else None,
        api_base_url="${ARG_API_BASE_URL}" if "${ARG_API_BASE_URL}" else None,
    )
)
if not ok:
    print(f"ERROR:{info}", file=sys.stderr)
    sys.exit(1)
uname = info.split(" ", 1)[0].lstrip("@") if info.startswith("@") else "AnonInboxBot"
print(uname)
PYEOF
) || {
    echo -e "${RED}❌ خطا در اتصال به تلگرام (getMe)! لطفاً صحت توکن یا پروکسی را بررسی کنید.${NC}"
    exit 1
}
echo -e "${GREEN}✔ اتصال به تلگرام با موفقیت تایید شد: @${BOT_USERNAME_DETECTED}${NC}"

# ۹. تولید یا حفظ کلیدهای امنیتی (Idempotent: اگر در .env قبلی وجود دارد حفظ شود!)
EXISTING_HMAC=""
EXISTING_FERNET=""
EXISTING_WH_SECRET=""
if [[ -f "${ENV_FILE}" ]]; then
    EXISTING_HMAC=$(grep -E '^HMAC_SECRET=' "${ENV_FILE}" | cut -d'=' -f2- || true)
    EXISTING_FERNET=$(grep -E '^FERNET_KEY=' "${ENV_FILE}" | cut -d'=' -f2- || true)
    EXISTING_WH_SECRET=$(grep -E '^WEBHOOK_SECRET=' "${ENV_FILE}" | cut -d'=' -f2- || true)
fi

if [[ -z "${EXISTING_HMAC}" || -z "${EXISTING_FERNET}" ]]; then
    echo -e "${CYAN}🔐 در حال تولید کلیدهای رمزنگاری امن (HMAC-SHA256 و Fernet)...${NC}"
    KEYS_OUT=$(PYTHONPATH="${APP_DIR}" "${APP_DIR}/venv/bin/python" -c "from app.utils.crypto import CryptoManager; h, f = CryptoManager.generate_new_secrets(); print(f'{h}|{f}')")
    EXISTING_HMAC="${KEYS_OUT%%|*}"
    EXISTING_FERNET="${KEYS_OUT##*|}"
else
    echo -e "${GREEN}✔ کلیدهای رمزنگاری قبلی در ${ENV_FILE} حفظ شدند تا پیام‌های قدیمی از کار نیفتند.${NC}"
fi

if [[ -z "${EXISTING_WH_SECRET}" ]]; then
    EXISTING_WH_SECRET=$(openssl rand -hex 24)
fi

if [[ "${ARG_DB_TYPE}" == "mysql" && -n "${ARG_MYSQL_URL}" ]]; then
    FINAL_DB_URL="${ARG_MYSQL_URL}"
else
    FINAL_DB_URL="sqlite+aiosqlite:///${DATA_DIR}/anonbot.db"
fi

cat > "${ENV_FILE}" <<EOF
BOT_TOKEN=${ARG_TOKEN}
BOT_USERNAME=${BOT_USERNAME_DETECTED}
SUPER_ADMINS=${ARG_ADMIN_ID}

HMAC_SECRET=${EXISTING_HMAC}
FERNET_KEY=${EXISTING_FERNET}

DATABASE_URL=${FINAL_DB_URL}
REDIS_URL=${ARG_REDIS_URL}

RUN_MODE=${ARG_RUN_MODE}
WEBHOOK_HOST=${ARG_WEBHOOK_HOST}
WEBHOOK_PATH=/webhook/anonbot
WEBHOOK_SECRET=${EXISTING_WH_SECRET}
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=${ARG_WEBHOOK_PORT}

PROXY_ENABLED=${PROXY_ENABLED}
PROXY_URL=${ARG_PROXY_URL}
API_BASE_URL=${ARG_API_BASE_URL}

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

chmod 600 "${ENV_FILE}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}" "${ETC_DIR}" "${DATA_DIR}" "${LOG_DIR}" "${BACKUP_DIR}"

# ۱۰. اجرای مایگریشن دیتابیس با Alembic
echo -e "${CYAN}🗄 در حال اجرای مایگریشن‌های دیتابیس (Alembic)...${NC}"
(
    cd "${APP_DIR}"
    ANONBOT_ENV_FILE="${ENV_FILE}" "${APP_DIR}/venv/bin/python" -m alembic upgrade head
)

# ثبت کانال جوین اجباری اولیه در صورت وارد شدن
if [[ -n "${ARG_FORCED_CHANNEL}" ]]; then
    (
        cd "${APP_DIR}"
        ANONBOT_ENV_FILE="${ENV_FILE}" "${APP_DIR}/venv/bin/python" - <<PYEOF
import asyncio
from app.config import load_settings
from app.db.session import init_db_engine, get_session_factory, close_db_engine
from app.db.repositories import BotRepository
from app.utils.crypto import CryptoManager

async def _add_ch():
    cfg = load_settings("${ENV_FILE}")
    init_db_engine(cfg.database_url)
    crypto = CryptoManager(cfg.hmac_secret, cfg.fernet_key)
    sf = get_session_factory()
    ch = "${ARG_FORCED_CHANNEL}".strip()
    link = f"https://t.me/{ch.lstrip('@')}"
    async with sf() as s:
        repo = BotRepository(s, crypto)
        await repo.add_forced_channel(ch, ch, link)
    await close_db_engine()

asyncio.run(_add_ch())
PYEOF
    )
fi

chown -R "${APP_USER}:${APP_USER}" "${DATA_DIR}"

# ۱۱. ساخت دستور سراسری /usr/local/bin/anonbot
cat > "${CLI_BIN}" <<'EOF'
#!/usr/bin/env bash
export ANONBOT_ENV_FILE="/etc/anonbot/.env"
export PYTHONPATH="/opt/anonbot"
exec /opt/anonbot/venv/bin/python /opt/anonbot/cli/main.py "$@"
EOF
chmod +x "${CLI_BIN}"

# ۱۲. نصب و فعال‌سازی سرویس systemd
cp "${APP_DIR}/systemd/anonbot.service" /etc/systemd/system/anonbot.service
systemctl daemon-reload
systemctl enable anonbot.service
systemctl restart anonbot.service

echo ""
echo -e "${GREEN}======================================================================${NC}"
echo -e "${GREEN}   🎉 نصب و راه‌اندازی ربات AnonBot با موفقیت به پایان رسید!           ${NC}"
echo -e "${GREEN}======================================================================${NC}"
echo -e "• نام کاربری ربات:    ${CYAN}@${BOT_USERNAME_DETECTED}${NC}"
echo -e "• مسیر پروژه:         ${CYAN}${APP_DIR}${NC}"
echo -e "• فایل تنظیمات امن:   ${CYAN}${ENV_FILE} (مجوز 600)${NC}"
echo -e "• مسیر لاگ‌ها:         ${CYAN}${LOG_DIR}/anonbot.log${NC}"
echo -e "• دستور مدیریت ربات:  ${YELLOW}sudo anonbot${NC} (منوی تعاملی) یا ${YELLOW}sudo anonbot doctor${NC}"
echo -e "${GREEN}======================================================================${NC}"
