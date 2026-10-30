# ربات تلگرام صندوق پیام ناشناس چندگانه (AnonBot)

یک پروژه کامل، آماده اجرا در محیط عملیاتی (Production-Ready) و بدون هیچ‌گونه بخش ناتمام یا Placeholder برای مدیریت چندگانه صندوق‌های پیام ناشناس شخصی، کانالی و گروهی در تلگرام با رابط کاملاً فارسی، ابزار خط فرمان (CLI) مدیریتی، پشتیبانی از پروکسی و اسکریپت نصب خودکار لینوکس متصل به مخزن گیت‌هاب `arimakomi/staranon`.

## تصمیمات تاییدشده و تغییرات گیت‌هاب (User Review & Confirmed Decisions)

> [!IMPORTANT]
> بر اساس پاسخ‌های جدید شما، موارد زیر در اسکریپت نصب، CLI و مستندات اعمال می‌گردد:

- **مخزن رسمی گیت‌هاب (`https://github.com/arimakomi/staranon`)**:
  - لینک نصب تک‌خطی (One-Liner) در `README.md` و `scripts/install.sh` مستقیماً به مخزن `https://github.com/arimakomi/staranon` و اسکریپت نصب آن متصل می‌شود.
  - اگر `install.sh` به صورت مستقیم با `curl | sudo bash` روی سرور اجرا شود (بدون کلون قبلی)، خود اسکریپت به صورت خودکار آخرین نسخه آرشیو `main.tar.gz` را از `https://github.com/arimakomi/staranon/archive/refs/heads/main.tar.gz` دانلود و در `/opt/anonbot` استخراج می‌کند.
- **سازوکار آپدیت مستقیم از گیت‌هاب در CLI (`anonbot update`) بدون `git pull`**:
  - دستور `anonbot update` هرگز از `git pull` استفاده نمی‌کند؛ بلکه ابتدا از فایل‌های فعلی `/opt/anonbot` یک اسنپ‌شات فشرده (`/var/backups/anonbot/rollback_snapshot.tar.gz`) برای قابلیت `anonbot rollback` می‌سازد، سپس فایل فشرده آخرین نسخه برنچ `main` (`https://github.com/arimakomi/staranon/archive/refs/heads/main.tar.gz`) را مستقیماً (با پشتیبانی از پروکسی تنظیم‌شده در `.env`) دانلود و استخراج کرده، فایل‌های کد را در `/opt/anonbot` جایگزین می‌کند (بدون دست زدن به `venv` و `/etc/anonbot/.env`)، وابستگی‌های `requirements.txt` و مایگریشن‌های `alembic` را به‌روزرسانی کرده و سرویس را ریستارت می‌نماید.
- **ادامه نمایش کامل فایل‌ها در چت**:
  - بلافاصله پس از تایید طرح، نمایش کامل کدهای باقی‌مانده (از فایل ۱۳ تا انتها شامل مایگریشن، سرویس‌ها، میدل‌ورها، کیبوردها، هندلرها، CLI، اسکریپت‌های نصب/حذف، سرویس systemd، تست‌ها، README و چک‌لیست تست دستی) ادامه می‌یابد.

---

## ۱. معماری آپدیت و نصب مستقیم از گیت‌هاب (`arimakomi/staranon`)

```
┌────────────────────────────────────────────────────────────────────────────┐
│         GitHub Repository: https://github.com/arimakomi/staranon           │
│     Direct Tarball: .../archive/refs/heads/main.tar.gz (No git pull)       │
└───────────────────┬────────────────────────────────────┬───────────────────┘
                    │                                    │
      1. One-Liner Install (`install.sh`)      2. CLI Update (`anonbot update`)
                    │                                    │
                    ▼                                    ▼
┌───────────────────────────────────────┐  ┌─────────────────────────────────┐
│ • Auto-download main.tar.gz if needed │  │ • Save rollback_snapshot.tar.gz │
│ • Extractanonbot/ root or subfolder   │  │ • Download main.tar.gz (Proxy)  │
│ • Setup /opt/anonbot/venv (PEP 668)   │  │ • Replace code files cleanly    │
│ • Preserve /etc/anonbot/.env (600)    │  │ • pip install -r requirements   │
│ • Run alembic upgrade head & systemd  │  │ • alembic upgrade head & restart│
└───────────────────────────────────────┘  └─────────────────────────────────┘
```

---

## ۲. خلاصه سایر تصمیمات تاییدشده قبلی

- **مسیر تحویل پیام در صندوق‌های کانال و گروه**: ارسال به چت خصوصی (PV) ادمین‌های مجاز صندوق با قابلیت مدیریت مشترک و نمایش اعلان «ادمین X در حال پاسخ است».
- **بن خودکار سراسری با رمزگشایی داخلی**: رمزگشایی `sender_enc` صرفاً در حافظه داخلی سرویس هنگام تایید گزارش توسط ادمین کل، جهت افزایش `valid_reports_count` در جدول `users` و اعمال بن موقت سراسری بدون نمایش آیدی خام فرستنده.
- **بک‌آپ رمزنگاری‌شده حاوی کلیدهای حیاتی**: ذخیره دیتابیس به همراه `HMAC_SECRET` و `FERNET_KEY` در بسته رمزنگاری‌شده `.tar.gz.enc` همراه با هشدار صریح امنیتی.
