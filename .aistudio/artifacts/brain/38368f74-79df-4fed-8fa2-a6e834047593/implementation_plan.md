# برنامه انگلیسی‌سازی کامل نصب‌کننده و ابزار خط فرمان ترمینال (CLI)

از آنجا که ترمینال‌های لینوکس و SSH متن‌های فارسی (راست‌به‌چپ) را به‌صورت برعکس و به‌هم‌ریخته نمایش می‌دهند، تمامی بخش‌هایی که در ترمینال اجرا و چاپ می‌شوند به **انگلیسی استاندارد و خوانا** تبدیل می‌شوند، در حالی که **محیط داخل ربات تلگرام ۱۰۰٪ فارسی باقی می‌ماند**.

---

## فایل‌هایی که به انگلیسی تبدیل می‌شوند

### ۱. اسکریپت‌های نصب و حذف (`/install.sh`، `/scripts/install.sh`، `/uninstall.sh`، `/scripts/uninstall.sh`)
- تبدیل تمام سوال‌های ویزارد نصب (Proxy، Bot Token، Super Admin ID، Advanced Settings، MySQL، Redis، Webhook، Force-Join Channel) به انگلیسی روان و استاندارد.
- تبدیل تمام پیام‌های وضعیت نصب، تست `getMe`، خطاها و خلاصه پایان نصب به انگلیسی.
- تبدیل سوالات تایید حذف و پاک‌سازی کامل (`uninstall.sh`) به انگلیسی.

### ۲. ابزار مدیریت خط فرمان و منوی تعاملی (`/cli/main.py`)
- تبدیل منوی تعاملی شماره‌دار (`sudo anonbot` بدون آرگومان) و تمام جداول و پنل‌های `Rich` به انگلیسی.
- تبدیل خروجی تمام دستورات ترمینال به انگلیسی:
  - سرویس و سلامت: `status`، `start`، `stop`، `restart`، `logs`، `doctor`
  - تنظیمات و توکن: `config show/get/set`، `token set`، `admin list/add/remove`، `maintenance on/off`
  - پروکسی: `proxy set/show/test/enable/disable/remove`
  - دیتابیس و بک‌آپ: `db migrate`، `db backup` (به همراه هشدار امنیتی انگلیسی درباره کلیدهای `FERNET_KEY` و `HMAC_SECRET`)، `db restore`، `db auto-backup`
  - آپدیت گیت‌هاب: `update` (دانلود مستقیم از گیت‌هاب بدون `git pull`) و `rollback`
  - مدیریت کاربران و صندوق‌ها در ترمینال: `stats`، `user list/info/ban/unban`، `inbox list/info/enable/disable`، `broadcast`
