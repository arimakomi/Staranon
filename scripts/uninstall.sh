#!/usr/bin/env bash
# ==============================================================================
# AnonBot Uninstaller
# ==============================================================================
set -euo pipefail
[[ $EUID -ne 0 ]] && { echo "❌ لطفاً با sudo اجرا کنید."; exit 1; }

read -r -p "⚠️ آیا از حذف سرویس AnonBot مطمئن هستید؟ [y/N]: " CONFIRM < /dev/tty
[[ "${CONFIRM,,}" != "y" ]] && exit 0

read -r -p "❓ آیا دیتابیس، تنظیمات (/etc/anonbot) و بک‌آپ‌ها نیز کامل پاک شوند؟ [y/N]: " PURGE < /dev/tty

systemctl stop anonbot.service 2>/dev/null || true
systemctl disable anonbot.service 2>/dev/null || true
rm -f /etc/systemd/system/anonbot.service /etc/cron.d/anonbot-backup /usr/local/bin/anonbot
systemctl daemon-reload
rm -rf /opt/anonbot /var/log/anonbot

if [[ "${PURGE,,}" == "y" ]]; then
  rm -rf /etc/anonbot /var/lib/anonbot /var/backups/anonbot
  id "anonbot" &>/dev/null && userdel "anonbot" 2>/dev/null || true
  echo "✅ سرویس، فایل‌ها و دیتابیس به طور کامل حذف شدند."
else
  echo "✅ سرویس حذف شد. دیتابیس (/var/lib/anonbot) و کلیدها (/etc/anonbot) حفظ شدند."
fi
