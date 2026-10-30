#!/usr/bin/env bash
# ==============================================================================
# AnonBot Uninstaller
# ==============================================================================
set -euo pipefail
[[ $EUID -ne 0 ]] && { echo "[ERROR] Please run with sudo or as root."; exit 1; }

read -r -p "[!] Are you sure you want to uninstall the AnonBot service? [y/N]: " CONFIRM < /dev/tty
[[ "${CONFIRM,,}" != "y" ]] && exit 0

read -r -p "[?] Also purge database (/var/lib/anonbot), keys (/etc/anonbot) and backups? [y/N]: " PURGE < /dev/tty

systemctl stop anonbot.service 2>/dev/null || true
systemctl disable anonbot.service 2>/dev/null || true
rm -f /etc/systemd/system/anonbot.service /etc/cron.d/anonbot-backup /usr/local/bin/anonbot
systemctl daemon-reload
rm -rf /opt/anonbot /var/log/anonbot

if [[ "${PURGE,,}" == "y" ]]; then
  rm -rf /etc/anonbot /var/lib/anonbot /var/backups/anonbot
  id "anonbot" &>/dev/null && userdel "anonbot" 2>/dev/null || true
  echo "[OK] Service, application files, database, and keys completely removed."
else
  echo "[OK] Service removed. Database (/var/lib/anonbot) and keys (/etc/anonbot) were preserved."
fi
