#!/usr/bin/env bash
# ==============================================================================
# اسکریپت حذف کامل یا جزئی ربات صندوق پیام ناشناس (AnonBot Uninstaller)
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

AUTO_YES="false"
KEEP_DATA="ask"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y) AUTO_YES="true"; shift ;;
        --keep-data) KEEP_DATA="true"; shift ;;
        --purge-data) KEEP_DATA="false"; shift ;;
        *) shift ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}❌ این اسکریپت باید با دسترسی root (یا sudo) اجرا شود.${NC}"
    exit 1
fi

if [[ "${AUTO_YES}" == "false" ]]; then
    read -r -p "🚨 آیا از توقف و حذف سرویس ربات AnonBot مطمئن هستید؟ [y/N]: " ans
    if [[ ! "${ans,,}" =~ ^(y|yes)$ ]]; then
        echo "عملیات لغو شد."
        exit 0
    fi
fi

if [[ "${KEEP_DATA}" == "ask" ]]; then
    read -r -p "💾 آیا می‌خواهید دیتابیس، فایل کلیدها (/etc/anonbot/.env) و بک‌آپ‌ها حفظ شوند؟ [Y/n]: " keep_ans
    if [[ "${keep_ans,,}" =~ ^(n|no)$ ]]; then
        KEEP_DATA="false"
    else
        KEEP_DATA="true"
    fi
fi

echo -e "${YELLOW}⏳ در حال توقف و حذف سرویس systemd...${NC}"
systemctl stop anonbot.service >/dev/null 2>&1 || true
systemctl disable anonbot.service >/dev/null 2>&1 || true
rm -f /etc/systemd/system/anonbot.service
rm -f /etc/cron.d/anonbot-backup
systemctl daemon-reload >/dev/null 2>&1 || true

rm -f /usr/local/bin/anonbot
rm -rf /opt/anonbot

if [[ "${KEEP_DATA}" == "false" ]]; then
    echo -e "${RED}🗑 در حال حذف کامل دیتابیس، کلیدهای رمزنگاری و لاگ‌ها...${NC}"
    rm -rf /etc/anonbot /var/lib/anonbot /var/log/anonbot /var/backups/anonbot
    userdel anonbot >/dev/null 2>&1 || true
    echo -e "${GREEN}✅ ربات و تمام داده‌های آن به طور کامل از سرور پاک شدند.${NC}"
else
    echo -e "${GREEN}✅ سرویس و کدهای ربات حذف شدند؛ اما دیتابیس (/var/lib/anonbot) و کلیدها (/etc/anonbot/.env) حفظ گردیدند.${NC}"
fi
