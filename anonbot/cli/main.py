#!/usr/bin/env python3
"""
ابزار خط فرمان مدیریتی جامع ربات صندوق پیام ناشناس (anonbot CLI).
پشتیبانی از حالت بدون آرگومان (منوی تعاملی شماره‌دار و رنگی) و زیرفرمان‌های مستقیم Typer.
"""

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from aiogram import Bot
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from sqlalchemy import select

from app.config import find_env_file, load_settings
from app.db.models import BroadcastJob, Inbox, User
from app.db.repositories import BotRepository
from app.db.session import close_db_engine, get_session_factory, init_db_engine
from app.services.broadcaster import execute_broadcast_job
from app.services.telegram_helpers import build_bot_session, test_telegram_connection
from app.utils.crypto import CryptoManager, mask_secret
from cli.env_manager import read_env_dict, restart_systemd_service_if_active, write_env_key

console = Console()
app = typer.Typer(
    name="anonbot",
    help="ابزار مدیریت خط فرمان ربات تلگرام صندوق پیام ناشناس چندگانه (AnonBot)",
    invoke_without_command=True,
    no_args_is_help=False,
)

config_app = typer.Typer(help="مشاهده و ویرایش مقادیر فایل .env")
token_app = typer.Typer(help="مدیریت توکن ربات تلگرام")
admin_app = typer.Typer(help="مدیریت ادمین‌های کل ربات (SUPER_ADMINS)")
user_app = typer.Typer(help="مدیریت کاربران ربات (جستجو، بن، رفع بن، لیست)")
inbox_app = typer.Typer(help="مدیریت صندوق‌های پیام ناشناس")
db_app = typer.Typer(help="مدیریت دیتابیس، مایگریشن، بک‌آپ رمزنگاری‌شده و بازیابی")
proxy_app = typer.Typer(help="مدیریت پروکسی SOCKS5/HTTP برای اتصال به تلگرام")

app.add_typer(config_app, name="config")
app.add_typer(token_app, name="token")
app.add_typer(admin_app, name="admin")
app.add_typer(user_app, name="user")
app.add_typer(inbox_app, name="inbox")
app.add_typer(db_app, name="db")
app.add_typer(proxy_app, name="proxy")

SECRET_ENV_KEYS = {"BOT_TOKEN", "HMAC_SECRET", "FERNET_KEY", "WEBHOOK_SECRET", "PROXY_URL", "DATABASE_URL"}
SERVICE_NAME = "anonbot"
GITHUB_REPO = "arimakomi/staranon"
GITHUB_TARBALL_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.tar.gz"
CODE_ITEMS_TO_SYNC = [
    "app",
    "cli",
    "alembic",
    "scripts",
    "systemd",
    "tests",
    "requirements.txt",
    "alembic.ini",
    "README.md",
    "LICENSE",
    ".env.example",
]


def _get_repo_context():
    cfg = load_settings()
    init_db_engine(cfg.database_url)
    crypto = CryptoManager(
        hmac_secret=cfg.hmac_secret or ("0" * 64),
        fernet_key=cfg.fernet_key or CryptoManager.generate_new_secrets()[1],
    )
    return cfg, crypto, get_session_factory()


# ==============================================================================
# ۱. کنترل سرویس systemd و لاگ‌ها (status, start, stop, restart, logs, doctor)
# ==============================================================================
@app.command("status")
def cmd_status() -> None:
    """نمایش وضعیت فعلی سرویس systemd ربات."""
    subprocess.run(["systemctl", "status", SERVICE_NAME, "--no-pager"], check=False)


@app.command("start")
def cmd_start() -> None:
    """روشن کردن سرویس ربات."""
    res = subprocess.run(["systemctl", "start", SERVICE_NAME], check=False)
    if res.returncode == 0:
        console.print("[bold green]✅ سرویس anonbot با موفقیت روشن شد.[/bold green]")
    else:
        console.print("[bold red]❌ خطا در روشن کردن سرویس anonbot.[/bold red]")


@app.command("stop")
def cmd_stop() -> None:
    """متوقف کردن سرویس ربات."""
    res = subprocess.run(["systemctl", "stop", SERVICE_NAME], check=False)
    if res.returncode == 0:
        console.print("[bold yellow]⏹ سرویس anonbot متوقف شد.[/bold yellow]")


@app.command("restart")
def cmd_restart() -> None:
    """ریستارت کردن سرویس ربات."""
    res = subprocess.run(["systemctl", "restart", SERVICE_NAME], check=False)
    if res.returncode == 0:
        console.print("[bold green]🔄 سرویس anonbot با موفقیت ریستارت شد.[/bold green]")
    else:
        console.print("[bold red]❌ خطا در ریستارت سرویس.[/bold red]")


@app.command("logs")
def cmd_logs(
    follow: bool = typer.Option(False, "-f", "--follow", help="نمایش زنده لاگ‌ها"),
    lines: int = typer.Option(100, "-n", "--lines", help="تعداد خطوط پایانی"),
) -> None:
    """مشاهده لاگ‌های سرویس و فایل لاگ چرخشی."""
    cfg = load_settings()
    log_file = Path(cfg.log_dir) / "anonbot.log"
    if log_file.is_file():
        cmd = ["tail", f"-n{lines}"]
        if follow:
            cmd.append("-f")
        cmd.append(str(log_file))
        subprocess.run(cmd, check=False)
    else:
        cmd = ["journalctl", "-u", SERVICE_NAME, f"-n{lines}", "--no-pager"]
        if follow:
            cmd.remove("--no-pager")
            cmd.append("-f")
        subprocess.run(cmd, check=False)


@app.command("doctor")
def cmd_doctor() -> None:
    """بررسی جامع سلامت سیستم: سرویس، فایل env، دیتابیس، اتصال تلگرام، پروکسی و دیسک."""
    cfg = load_settings()
    table = Table(title="🩺 گزارش سلامت سامانه (AnonBot Doctor)", show_lines=True)
    table.add_column("بخش", style="cyan", no_wrap=True)
    table.add_column("وضعیت", style="bold")
    table.add_column("جزئیات", style="white")

    env_path = Path(cfg.env_file_path or find_env_file())
    if env_path.is_file():
        mode = oct(env_path.stat().st_mode & 0o777)
        perm_ok = mode == "0o600"
        table.add_row(
            "فایل تنظیمات (.env)",
            "[green]سالم[/green]" if perm_ok else "[yellow]هشدار مجوز[/yellow]",
            f"مسیر: {env_path} (مجوز: {mode})",
        )
    else:
        table.add_row("فایل تنظیمات (.env)", "[red]یافت نشد[/red]", str(env_path))

    try:
        cfg.validate_secrets()
        table.add_row("کلیدهای امنیتی (HMAC/Fernet)", "[green]معتبر[/green]", "کلیدها ساختار صحیحی دارند.")
    except Exception as exc:
        table.add_row("کلیدهای امنیتی (HMAC/Fernet)", "[red]نامعتبر[/red]", str(exc))

    try:
        res = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME], capture_output=True, text=True, check=False
        )
        st = res.stdout.strip()
        table.add_row(
            "سرویس systemd",
            "[green]فعال (active)[/green]" if st == "active" else f"[yellow]{st}[/yellow]",
            f"anonbot.service ({st})",
        )
    except FileNotFoundError:
        table.add_row("سرویس systemd", "[yellow]غیرفعال در محیط کانتینر[/yellow]", "systemctl یافت نشد.")

    async def _check_db_and_tg():
        db_ok = False
        db_detail = ""
        try:
            init_db_engine(cfg.database_url)
            sf = get_session_factory()
            async with sf() as s:
                await s.execute(select(1))
            db_ok = True
            db_detail = mask_secret(cfg.database_url, 10)
        except Exception as exc:
            db_detail = str(exc)
        finally:
            await close_db_engine()

        proxy_to_use = cfg.proxy_url if cfg.proxy_enabled else None
        tg_ok, tg_info, latency = await test_telegram_connection(
            cfg.bot_token, proxy_url=proxy_to_use, api_base_url=cfg.api_base_url
        )
        return db_ok, db_detail, tg_ok, tg_info, latency

    db_ok, db_detail, tg_ok, tg_info, latency = asyncio.run(_check_db_and_tg())
    table.add_row(
        "دیتابیس",
        "[green]متصل[/green]" if db_ok else "[red]خطا[/red]",
        db_detail,
    )
    table.add_row(
        "پروکسی تلگرام",
        "[green]فعال[/green]" if cfg.proxy_enabled else "[dim]غیرفعال[/dim]",
        mask_secret(cfg.proxy_url) if cfg.proxy_url else "بدون پروکسی",
    )
    table.add_row(
        "اتصال به تلگرام (getMe)",
        "[green]برقرار[/green]" if tg_ok else "[red]قطع[/red]",
        f"{tg_info} | پینگ: {latency:.0f}ms",
    )

    usage = shutil.disk_usage("/")
    free_gb = usage.free / (1024**3)
    table.add_row(
        "فضای خالی دیسک",
        "[green]کافی[/green]" if free_gb > 1.0 else "[red]کمبود فضا[/red]",
        f"{free_gb:.2f} گیگابایت آزاد",
    )

    console.print(table)


# ==============================================================================
# ۲. دستورات تنظیمات و توکن (config, token, admin, maintenance)
# ==============================================================================
@config_app.command("show")
def config_show() -> None:
    """نمایش تمام مقادیر فایل .env (با ماسک کردن مقادیر حساس و توکن)."""
    env_data = read_env_dict()
    table = Table(title="⚙️ تنظیمات فعلی (.env)")
    table.add_column("کلید (Key)", style="cyan")
    table.add_column("مقدار (Value)", style="green")
    for k, v in env_data.items():
        val_display = mask_secret(v) if k in SECRET_ENV_KEYS else v
        table.add_row(k, val_display)
    console.print(table)


@config_app.command("get")
def config_get(key: str) -> None:
    """نمایش مقدار یک کلید خاص از فایل .env."""
    env_data = read_env_dict()
    k = key.strip().upper()
    if k not in env_data:
        console.print(f"[red]کلید {k} در فایل .env یافت نشد.[/red]")
        raise typer.Exit(1)
    val = mask_secret(env_data[k]) if k in SECRET_ENV_KEYS else env_data[k]
    console.print(f"{k}={val}")


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """تغییر مقدار یک کلید در فایل .env و ریستارت خودکار سرویس."""
    k = key.strip().upper()
    path = write_env_key(k, value.strip())
    restarted = restart_systemd_service_if_active(SERVICE_NAME)
    console.print(f"[green]✅ مقدار {k} در {path} ذخیره شد.[/green]")
    if restarted:
        console.print("[cyan]🔄 سرویس anonbot به صورت خودکار ریستارت شد.[/cyan]")


@token_app.command("set")
def token_set(new_token: str) -> None:
    """تست و ثبت توکن جدید ربات و ریستارت خودکار سرویس."""
    cfg = load_settings()
    proxy_url = cfg.proxy_url if cfg.proxy_enabled else None
    ok, info, latency = asyncio.run(
        test_telegram_connection(new_token.strip(), proxy_url=proxy_url, api_base_url=cfg.api_base_url)
    )
    if not ok:
        console.print(f"[bold red]❌ توکن نامعتبر است یا اتصال به تلگرام برقرار نشد: {info}[/bold red]")
        raise typer.Exit(1)
    write_env_key("BOT_TOKEN", new_token.strip())
    if info.startswith("@"):
        uname = info.split(" ", 1)[0].lstrip("@")
        write_env_key("BOT_USERNAME", uname)
    restart_systemd_service_if_active(SERVICE_NAME)
    console.print(f"[bold green]✅ توکن جدید تایید شد ({info} - {latency:.0f}ms) و سرویس ریستارت گردید.[/bold green]")


@admin_app.command("list")
def admin_list() -> None:
    """نمایش لیست آیدی‌های ادمین کل ربات."""
    cfg = load_settings()
    console.print(Panel(", ".join(str(a) for a in cfg.super_admins) or "بدون ادمین", title="👑 ادمین‌های کل ربات"))


@admin_app.command("add")
def admin_add(user_id: int) -> None:
    """افزودن یک آیدی عددی به لیست ادمین‌های کل ربات."""
    cfg = load_settings()
    admins = list(cfg.super_admins)
    if user_id not in admins:
        admins.append(user_id)
    write_env_key("SUPER_ADMINS", ",".join(str(a) for a in admins))
    restart_systemd_service_if_active(SERVICE_NAME)
    console.print(f"[green]✅ کاربر {user_id} به لیست SUPER_ADMINS اضافه شد.[/green]")


@admin_app.command("remove")
def admin_remove(user_id: int) -> None:
    """حذف یک آیدی عددی از لیست ادمین‌های کل ربات."""
    cfg = load_settings()
    admins = [a for a in cfg.super_admins if a != user_id]
    write_env_key("SUPER_ADMINS", ",".join(str(a) for a in admins))
    restart_systemd_service_if_active(SERVICE_NAME)
    console.print(f"[yellow]🗑 کاربر {user_id} از لیست SUPER_ADMINS حذف شد.[/yellow]")


@app.command("maintenance")
def cmd_maintenance(state: str = typer.Argument(..., help="on یا off")) -> None:
    """فعال یا غیرفعال کردن حالت تعمیرات ربات."""
    st = state.strip().lower()
    if st not in ("on", "off"):
        console.print("[red]لطفاً فقط مقدار on یا off وارد کنید.[/red]")
        raise typer.Exit(1)
    val = "true" if st == "on" else "false"
    write_env_key("MAINTENANCE_MODE", val)

    async def _set_db():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            await repo.set_setting("maintenance_mode", val)
        await close_db_engine()

    asyncio.run(_set_db())
    console.print(f"[bold green]🔧 حالت تعمیرات روی {st.upper()} تنظیم شد.[/bold green]")


# ==============================================================================
# ۳. مدیریت پروکسی (proxy set, show, test, remove, enable, disable)
# ==============================================================================
@proxy_app.command("show")
def proxy_show() -> None:
    """نمایش وضعیت فعلی پروکسی (با ماسک کردن رمز عبور)."""
    cfg = load_settings()
    status = "[bold green]فعال (Enabled)[/bold green]" if cfg.proxy_enabled else "[bold yellow]غیرفعال (Disabled)[/bold yellow]"
    masked_url = mask_secret(cfg.proxy_url) if cfg.proxy_url else "<تنظیم نشده>"
    api_base = cfg.api_base_url or "پیش‌فرض تلگرام (https://api.telegram.org)"
    console.print(
        Panel(
            f"• وضعیت پروکسی: {status}\n"
            f"• آدرس پروکسی: [cyan]{masked_url}[/cyan]\n"
            f"• سرور Bot API: [magenta]{api_base}[/magenta]",
            title="🌐 تنظیمات پروکسی تلگرام",
        )
    )


@proxy_app.command("set")
def proxy_set(url: str = typer.Argument(..., help="مثال: socks5://user:pass@127.0.0.1:1080 یا http://host:port")) -> None:
    """تنظیم آدرس پروکسی، فعال‌سازی خودکار، بررسی حالت Polling و ریستارت سرویس."""
    clean_url = url.strip()
    if not clean_url.startswith(("socks5://", "socks4://", "http://", "https://")):
        console.print(
            "[bold red]❌ فرمت پروکسی نامعتبر است. فرمت مجاز: socks5://user:pass@host:port یا http://host:port[/bold red]\n"
            "[yellow]نکته: پروکسی‌های MTProto تلگرام با Bot API سازگار نیستند.[/yellow]"
        )
        raise typer.Exit(1)

    write_env_key("PROXY_URL", clean_url)
    write_env_key("PROXY_ENABLED", "true")
    cfg = load_settings()
    if cfg.run_mode == "webhook":
        console.print(
            "[bold yellow]⚠️ هشدار: با فعال بودن پروکسی، حالت اجرای ربات به صورت خودکار به polling تغییر یافت.[/bold yellow]"
        )
        write_env_key("RUN_MODE", "polling")

    restart_systemd_service_if_active(SERVICE_NAME)
    console.print(f"[bold green]✅ پروکسی {mask_secret(clean_url)} ثبت و فعال شد و سرویس ریستارت گردید.[/bold green]")


@proxy_app.command("test")
def proxy_test() -> None:
    """تست اتصال به تلگرام (getMe) از طریق پروکسی تنظیم‌شده و نمایش پینگ."""
    cfg = load_settings()
    if not cfg.proxy_url:
        console.print("[red]❌ هیچ آدرس پروکسی در فایل .env ثبت نشده است.[/red]")
        raise typer.Exit(1)
    console.print(f"⏳ در حال تست اتصال از طریق پروکسی [cyan]{mask_secret(cfg.proxy_url)}[/cyan] ...")
    ok, info, latency = asyncio.run(
        test_telegram_connection(cfg.bot_token, proxy_url=cfg.proxy_url, api_base_url=cfg.api_base_url)
    )
    if ok:
        console.print(f"[bold green]✅ اتصال پروکسی موفق بود! ربات: {info} | تاخیر: {latency:.0f}ms[/bold green]")
    else:
        console.print(f"[bold red]❌ اتصال از طریق پروکسی ناموفق بود ({latency:.0f}ms): {info}[/bold red]")
        raise typer.Exit(1)


@proxy_app.command("enable")
def proxy_enable() -> None:
    """فعال کردن پروکسی ذخیره شده و ریستارت سرویس."""
    write_env_key("PROXY_ENABLED", "true")
    restart_systemd_service_if_active(SERVICE_NAME)
    console.print("[bold green]✅ پروکسی فعال شد و سرویس ریستارت گردید.[/bold green]")


@proxy_app.command("disable")
def proxy_disable() -> None:
    """غیرفعال کردن پروکسی بدون حذف آدرس آن و ریستارت سرویس."""
    write_env_key("PROXY_ENABLED", "false")
    restart_systemd_service_if_active(SERVICE_NAME)
    console.print("[bold yellow]⏸ پروکسی غیرفعال شد و سرویس ریستارت گردید.[/bold yellow]")


@proxy_app.command("remove")
def proxy_remove() -> None:
    """حذف کامل آدرس پروکسی و غیرفعال‌سازی آن."""
    write_env_key("PROXY_ENABLED", "false")
    write_env_key("PROXY_URL", "")
    restart_systemd_service_if_active(SERVICE_NAME)
    console.print("[bold yellow]🗑 پروکسی حذف و غیرفعال شد.[/bold yellow]")


# ==============================================================================
# ۴. مدیریت کاربران، صندوق‌ها، آمار و پیام همگانی (user, inbox, stats, broadcast)
# ==============================================================================
@user_app.command("list")
def user_list(limit: int = typer.Option(30, help="تعداد کاربران")) -> None:
    """نمایش لیست آخرین کاربران ربات."""
    async def _run():
        _, _, sf = _get_repo_context()
        async with sf() as s:
            res = await s.execute(select(User).order_by(User.id.desc()).limit(limit))
            return list(res.scalars().all())

    users = asyncio.run(_run())
    asyncio.run(close_db_engine())
    table = Table(title=f"👥 لیست کاربران ({len(users)} مورد)")
    table.add_column("آیدی عددی", style="cyan")
    table.add_column("نام", style="white")
    table.add_column("یوزرنیم", style="green")
    table.add_column("وضعیت", style="bold")
    table.add_column("گزارش معتبر", justify="right")
    for u in users:
        st = "[red]بن[/red]" if u.is_banned else "[green]آزاد[/green]"
        table.add_row(str(u.id), u.first_name, f"@{u.username}" if u.username else "-", st, str(u.valid_reports_count))
    console.print(table)


@user_app.command("info")
def user_info(user_id: int) -> None:
    """نمایش اطلاعات کامل یک کاربر و صندوق‌های او."""
    async def _run():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            u = await repo.get_user(user_id)
            inboxes = await repo.list_user_accessible_inboxes(user_id) if u else []
            return u, inboxes

    u, inboxes = asyncio.run(_run())
    asyncio.run(close_db_engine())
    if not u:
        console.print(f"[red]کاربر با آیدی {user_id} یافت نشد.[/red]")
        raise typer.Exit(1)

    ib_str = "\n".join([f"  - #{ib.id}: {ib.title} ({ib.type})" for ib, _ in inboxes]) or "  ندارد"
    console.print(
        Panel(
            f"• آیدی: [cyan]{u.id}[/cyan]\n"
            f"• نام: {u.first_name} (نام مستعار: {u.alias_name or '-'})\n"
            f"• یوزرنیم: @{u.username or '-'}\n"
            f"• مسدود: {'بله (' + (u.ban_reason or '') + ')' if u.is_banned else 'خیر'}\n"
            f"• تعداد گزارش‌های معتبر: {u.valid_reports_count}\n"
            f"• صندوق‌ها:\n{ib_str}",
            title="👤 اطلاعات کاربر",
        )
    )


@user_app.command("ban")
def user_ban(
    user_id: int,
    hours: int = typer.Option(0, "--hours", help="مدت مسدودیت به ساعت (۰ یعنی دائمی)"),
    reason: str = typer.Option("مسدودسازی از طریق CLI", "--reason", help="دلیل مسدودیت"),
) -> None:
    """مسدود کردن حساب یک کاربر."""
    async def _run():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            return await repo.ban_user(user_id, reason=reason, duration_hours=hours if hours > 0 else None)

    u = asyncio.run(_run())
    asyncio.run(close_db_engine())
    if not u:
        console.print("[red]کاربر یافت نشد.[/red]")
        raise typer.Exit(1)
    console.print(f"[bold red]⛔️ کاربر {user_id} مسدود شد.[/bold red]")


@user_app.command("unban")
def user_unban(user_id: int) -> None:
    """رفع مسدودیت یک کاربر."""
    async def _run():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            return await repo.unban_user(user_id)

    u = asyncio.run(_run())
    asyncio.run(close_db_engine())
    if not u:
        console.print("[red]کاربر یافت نشد.[/red]")
        raise typer.Exit(1)
    console.print(f"[bold green]🔓 مسدودیت کاربر {user_id} رفع شد.[/bold green]")


@inbox_app.command("list")
def inbox_list(limit: int = typer.Option(30, help="تعداد صندوق‌ها")) -> None:
    """نمایش لیست صندوق‌های ساخته شده در ربات."""
    async def _run():
        _, _, sf = _get_repo_context()
        async with sf() as s:
            res = await s.execute(select(Inbox).order_by(Inbox.id.desc()).limit(limit))
            return list(res.scalars().all())

    items = asyncio.run(_run())
    asyncio.run(close_db_engine())
    table = Table(title="📬 لیست صندوق‌های پیام ناشناس")
    table.add_column("ID", style="cyan")
    table.add_column("عنوان", style="white")
    table.add_column("نوع", style="magenta")
    table.add_column("مالک", style="green")
    table.add_column("Slug", style="yellow")
    table.add_column("وضعیت")
    for ib in items:
        st = "[green]فعال[/green]" if ib.is_active else "[red]غیرفعال[/red]"
        table.add_row(str(ib.id), ib.title, ib.type, str(ib.owner_id), ib.slug or "-", st)
    console.print(table)


@inbox_app.command("info")
def inbox_info(identifier: str) -> None:
    """نمایش جزئیات یک صندوق با آیدی یا Slug."""
    async def _run():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            if identifier.isdigit():
                return await repo.get_inbox_by_id(int(identifier))
            return await repo.get_inbox_by_token_or_slug(identifier)

    ib = asyncio.run(_run())
    asyncio.run(close_db_engine())
    if not ib:
        console.print("[red]صندوق یافت نشد.[/red]")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"• شناسه: {ib.id}\n"
            f"• عنوان: {ib.title}\n"
            f"• نوع: {ib.type}\n"
            f"• آیدی مالک: {ib.owner_id}\n"
            f"• کد کوتاه (Slug): {ib.slug or '-'}\n"
            f"• وضعیت: {'فعال' if ib.is_active else 'غیرفعال'}",
            title="📬 جزئیات صندوق",
        )
    )


@inbox_app.command("disable")
def inbox_disable(identifier: str) -> None:
    """غیرفعال کردن یک صندوق از طریق CLI."""
    async def _run():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            ib = await repo.get_inbox_by_id(int(identifier)) if identifier.isdigit() else await repo.get_inbox_by_token_or_slug(identifier)
            if ib:
                ib.is_active = False
                await s.commit()
            return ib

    ib = asyncio.run(_run())
    asyncio.run(close_db_engine())
    if not ib:
        console.print("[red]صندوق یافت نشد.[/red]")
        raise typer.Exit(1)
    console.print(f"[yellow]⏸ صندوق «{ib.title}» (#{ib.id}) غیرفعال شد.[/yellow]")


@inbox_app.command("enable")
def inbox_enable(identifier: str) -> None:
    """فعال کردن مجدد یک صندوق از طریق CLI."""
    async def _run():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            ib = await repo.get_inbox_by_id(int(identifier)) if identifier.isdigit() else await repo.get_inbox_by_token_or_slug(identifier)
            if ib:
                ib.is_active = True
                await s.commit()
            return ib

    ib = asyncio.run(_run())
    asyncio.run(close_db_engine())
    if not ib:
        console.print("[red]صندوق یافت نشد.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]🟢 صندوق «{ib.title}» (#{ib.id}) فعال شد.[/green]")


@app.command("stats")
def cmd_stats() -> None:
    """نمایش آمار زنده ربات."""
    async def _run():
        _, crypto, sf = _get_repo_context()
        async with sf() as s:
            repo = BotRepository(s, crypto)
            return await repo.get_live_stats()

    st = asyncio.run(_run())
    asyncio.run(close_db_engine())
    table = Table(title="📊 آمار زنده ربات (AnonBot Live Stats)")
    table.add_column("شاخص", style="cyan")
    table.add_column("مقدار", style="bold green", justify="right")
    table.add_row("کل کاربران ثبت‌شده", str(st["total_users"]))
    table.add_row("کاربران فعال ۲۴ ساعت گذشته", str(st["active_24h"]))
    table.add_row("کل صندوق‌های ناشناس", str(st["total_inboxes"]))
    table.add_row("پیام‌های مبادله‌شده در ۲۴ ساعت گذشته", str(st["messages_today"]))
    table.add_row("گزارش‌های تخلف باز", str(st["open_reports"]))
    table.add_row("کاربران مسدود شده", str(st["banned_users"]))
    console.print(table)


@app.command("broadcast")
def cmd_broadcast(text: str = typer.Argument(..., help="متن پیام همگانی")) -> None:
    """ارسال پیام متنی همگانی از خط فرمان به تمام کاربران."""
    async def _run():
        cfg, _, sf = _get_repo_context()
        admin_id = cfg.super_admins[0] if cfg.super_admins else 0
        async with sf() as s:
            job = BroadcastJob(
                admin_id=admin_id,
                mode="text",
                text_content=text,
                status="running",
            )
            s.add(job)
            await s.commit()
            await s.refresh(job)
            job_id = job.id

        session = build_bot_session(cfg)
        bot = Bot(token=cfg.bot_token, session=session)
        try:
            return await execute_broadcast_job(bot, job_id)
        finally:
            await bot.session.close()
            await close_db_engine()

    console.print("⏳ در حال ارسال پیام همگانی با رعایت محدودیت نرخ تلگرام...")
    final_job = asyncio.run(_run())
    if final_job:
        console.print(
            f"[bold green]✅ ارسال همگانی تکمیل شد! ارسال موفق: {final_job.sent_count} | بلاک: {final_job.blocked_count} | خطا: {final_job.failed_count}[/bold green]"
        )


# ==============================================================================
# ۵. دیتابیس، مایگریشن و بک‌آپ رمزنگاری‌شده حاوی کلیدها (db migrate/backup/restore)
# ==============================================================================
@db_app.command("migrate")
def db_migrate() -> None:
    """اجرای مایگریشن‌های Alembic (alembic upgrade head)."""
    project_root = Path(__file__).resolve().parent.parent
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(project_root),
        check=False,
    )
    if res.returncode == 0:
        console.print("[bold green]✅ مایگریشن‌های دیتابیس با موفقیت اعمال شدند.[/bold green]")
    else:
        console.print("[bold red]❌ خطا در اجرای مایگریشن دیتابیس.[/bold red]")
        raise typer.Exit(1)


@db_app.command("backup")
def db_backup(
    passphrase: Optional[str] = typer.Option(
        None, "--passphrase", "-p", help="رمز عبور برای رمزنگاری فایل بک‌آپ (در صورت عدم ورود از کلید سیستم استفاده می‌شود)"
    ),
) -> None:
    """
    تهیه بک‌آپ فشرده و رمزنگاری‌شده شامل دیتابیس + کلیدهای امنیتی (HMAC_SECRET و FERNET_KEY).
    """
    cfg = load_settings()
    backup_dir = Path(cfg.backup_dir)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        backup_dir = Path("./backups")
        backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = backup_dir / f"anonbot_backup_{timestamp}.tar.gz.enc"

    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        env_path = Path(cfg.env_file_path or find_env_file())
        if env_path.is_file():
            tar.add(str(env_path), arcname="env.backup")

        secrets_meta = json.dumps(
            {
                "HMAC_SECRET": cfg.hmac_secret,
                "FERNET_KEY": cfg.fernet_key,
                "DATABASE_URL": cfg.database_url,
                "created_at": timestamp,
            },
            indent=2,
        ).encode("utf-8")
        info = tarfile.TarInfo(name="keys_manifest.json")
        info.size = len(secrets_meta)
        tar.addfile(info, io.BytesIO(secrets_meta))

        if cfg.database_url.startswith("sqlite"):
            raw_db_path = cfg.database_url.split("///", 1)[-1]
            db_file = Path(raw_db_path).resolve()
            if db_file.is_file():
                tar.add(str(db_file), arcname="anonbot.sqlite3")
        else:
            console.print("⏳ در حال تهیه دامپ از دیتابیس MySQL...")
            clean = cfg.database_url.split("://", 1)[-1]
            creds, hostdb = clean.rsplit("@", 1)
            db_user, db_pass = creds.split(":", 1)
            hostport, db_name = hostdb.split("/", 1)
            db_name = db_name.split("?", 1)[0]
            db_host = hostport.split(":", 1)[0]
            db_port = hostport.split(":", 1)[1] if ":" in hostport else "3306"
            dump_res = subprocess.run(
                [
                    "mysqldump",
                    f"-h{db_host}",
                    f"-P{db_port}",
                    f"-u{db_user}",
                    f"-p{db_pass}",
                    "--single-transaction",
                    db_name,
                ],
                capture_output=True,
                check=False,
            )
            if dump_res.returncode == 0:
                sql_bytes = dump_res.stdout
                sql_info = tarfile.TarInfo(name="mysql_dump.sql")
                sql_info.size = len(sql_bytes)
                tar.addfile(sql_info, io.BytesIO(sql_bytes))

    effective_pass = passphrase or cfg.hmac_secret or "anonbot-default-backup-key"
    encrypted_bytes = CryptoManager.encrypt_backup_bytes(tar_buffer.getvalue(), effective_pass)
    out_file.write_bytes(encrypted_bytes)
    try:
        os.chmod(out_file, 0o600)
    except OSError:
        pass

    console.print(
        Panel(
            f"[bold green]✅ فایل بک‌آپ رمزنگاری‌شده با موفقیت ساخته شد:[/bold green]\n"
            f"[cyan]{out_file}[/cyan]\n\n"
            "[bold red]⚠️ هشدار امنیتی بسیار مهم:[/bold red]\n"
            "[yellow]این فایل حاوی کلیدهای رمزنگاری (FERNET_KEY و HMAC_SECRET) و اطلاعات دیتابیس است!\n"
            "بدون این کلیدها، پس از بازیابی هیچ پاسخ یا حذفی روی پیام‌های قدیمی کار نخواهد کرد.\n"
            "این فایل را در مکان امن نگهداری کنید و در اختیار افراد غیرمجاز قرار ندهید.[/yellow]",
            title="💾 بک‌آپ رمزنگاری‌شده (Database + Keys)",
        )
    )


@db_app.command("restore")
def db_restore(
    backup_file: str = typer.Argument(..., help="مسیر فایل بک‌آپ (.tar.gz.enc)"),
    passphrase: Optional[str] = typer.Option(
        None, "--passphrase", "-p", help="رمز عبور فایل بک‌آپ"
    ),
) -> None:
    """بازیابی دیتابیس و کلیدهای امنیتی (HMAC و Fernet) از فایل بک‌آپ رمزنگاری‌شده."""
    bpath = Path(backup_file)
    if not bpath.is_file():
        console.print(f"[red]❌ فایل بک‌آپ یافت نشد: {bpath}[/red]")
        raise typer.Exit(1)

    cfg = load_settings()
    effective_pass = passphrase or cfg.hmac_secret or Prompt.ask("🔑 رمز عبور یا HMAC_SECRET بک‌آپ را وارد کنید")

    try:
        raw_tar = CryptoManager.decrypt_backup_bytes(bpath.read_bytes(), effective_pass)
    except Exception as exc:
        console.print(f"[bold red]❌ خطا در رمزگشایی فایل بک‌آپ: {exc}[/bold red]")
        raise typer.Exit(1)

    subprocess.run(["systemctl", "stop", SERVICE_NAME], check=False)

    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:gz") as tar:
        names = tar.getnames()
        if "keys_manifest.json" in names:
            f_meta = tar.extractfile("keys_manifest.json")
            if f_meta:
                meta = json.loads(f_meta.read().decode("utf-8"))
                if meta.get("HMAC_SECRET"):
                    write_env_key("HMAC_SECRET", meta["HMAC_SECRET"])
                if meta.get("FERNET_KEY"):
                    write_env_key("FERNET_KEY", meta["FERNET_KEY"])

        if "anonbot.sqlite3" in names and cfg.database_url.startswith("sqlite"):
            raw_db_path = cfg.database_url.split("///", 1)[-1]
            db_target = Path(raw_db_path).resolve()
            db_target.parent.mkdir(parents=True, exist_ok=True)
            f_db = tar.extractfile("anonbot.sqlite3")
            if f_db:
                db_target.write_bytes(f_db.read())

    restart_systemd_service_if_active(SERVICE_NAME)
    console.print("[bold green]✅ دیتابیس و کلیدهای امنیتی (HMAC و Fernet) با موفقیت بازیابی شدند و سرویس ریستارت گردید![/bold green]")


@db_app.command("auto-backup")
def db_auto_backup(
    enable: bool = typer.Option(True, "--enable/--disable", help="فعال یا غیرفعال‌سازی کرون بک‌آپ روزانه")
) -> None:
    """تنظیم بک‌آپ خودکار روزانه در /etc/cron.d/anonbot-backup."""
    cron_file = Path("/etc/cron.d/anonbot-backup")
    if enable:
        try:
            cron_file.write_text(
                "0 3 * * * root /usr/local/bin/anonbot db backup >/dev/null 2>&1\n",
                encoding="utf-8",
            )
            os.chmod(cron_file, 0o644)
            console.print("[bold green]✅ بک‌آپ خودکار روزانه (ساعت ۰۳:۰۰ بامداد) فعال شد.[/bold green]")
        except OSError as exc:
            console.print(f"[red]❌ خطا در ساخت فایل cron (نیاز به دسترسی root): {exc}[/red]")
    else:
        if cron_file.is_file():
            cron_file.unlink()
        console.print("[yellow]⏸ بک‌آپ خودکار روزانه غیرفعال شد.[/yellow]")


# ==============================================================================
# ۶. آپدیت مستقیم از گیت‌هاب (بدون git pull)، بازگشت به نسخه قبل و حذف
# ==============================================================================
@app.command("update")
def cmd_update(
    tarball_url: str = typer.Option(
        GITHUB_TARBALL_URL,
        "--url",
        help="آدرس فایل فشرده آخرین نسخه گیت‌هاب (main.tar.gz)",
    )
) -> None:
    """
    دانلود مستقیم آخرین نسخه از گیت‌هاب (https://github.com/arimakomi/staranon) بدون git pull،
    ذخیره اسنپ‌شات برای Rollback، جایگزینی فایل‌ها، آپدیت requirements، مایگریشن و ریستارت.
    """
    project_root = Path(__file__).resolve().parent.parent
    cfg = load_settings()
    venv_pip = project_root / "venv" / "bin" / "pip"
    pip_cmd = [str(venv_pip)] if venv_pip.is_file() else [sys.executable, "-m", "pip"]

    backup_dir = Path(cfg.backup_dir)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        backup_dir = project_root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

    rollback_tar_path = backup_dir / "rollback_snapshot.tar.gz"

    # ۱. تهیه اسنپ‌شات از کدهای فعلی برای Rollback
    console.print("📸 در حال ذخیره اسنپ‌شات از نسخه فعلی برای قابلیت Rollback...")
    with tarfile.open(str(rollback_tar_path), mode="w:gz") as r_tar:
        for item_name in CODE_ITEMS_TO_SYNC:
            item_path = project_root / item_name
            if item_path.exists():
                r_tar.add(str(item_path), arcname=item_name)

    # ۲. دانلود مستقیم فایل فشرده آخرین نسخه برنچ main از گیت‌هاب با curl (بدون git pull)
    env = os.environ.copy()
    curl_cmd = ["curl", "-fsSL", "--connect-timeout", "20", "--retry", "3"]
    if cfg.proxy_enabled and cfg.proxy_url:
        env["ALL_PROXY"] = cfg.proxy_url
        env["HTTPS_PROXY"] = cfg.proxy_url
        env["HTTP_PROXY"] = cfg.proxy_url
        curl_cmd.extend(["-x", cfg.proxy_url])

    with tempfile.TemporaryDirectory(prefix="anonbot_update_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        archive_file = tmp_path / "main.tar.gz"
        curl_cmd.extend(["-o", str(archive_file), tarball_url])

        console.print(f"🌐 در حال دانلود مستقیم آخرین نسخه از گیت‌هاب ([cyan]{GITHUB_REPO}[/cyan])...")
        dl_res = subprocess.run(curl_cmd, env=env, check=False)
        if dl_res.returncode != 0 or not archive_file.is_file():
            console.print("[bold red]❌ خطا در دانلود فایل فشرده از گیت‌هاب! وضعیت شبکه یا پروکسی را بررسی کنید.[/bold red]")
            raise typer.Exit(1)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(str(archive_file), mode="r:gz") as tar:
            tar.extractall(path=str(extract_dir))

        # یافتن ریشه کد داخل آرشیو استخراج‌شده (چه در ریشه مخزن باشد چه داخل پوشه anonbot/)
        subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        if not subdirs:
            console.print("[bold red]❌ آرشیو دانلودشده از گیت‌هاب خالی یا نامعتبر است.[/bold red]")
            raise typer.Exit(1)

        repo_root = subdirs[0]
        if (repo_root / "anonbot" / "requirements.txt").is_file() and (repo_root / "anonbot" / "app").is_dir():
            source_root = repo_root / "anonbot"
        elif (repo_root / "requirements.txt").is_file() and (repo_root / "app").is_dir():
            source_root = repo_root
        else:
            console.print("[bold red]❌ ساختار فایل‌های ربات در آرشیو گیت‌هاب یافت نشد.[/bold red]")
            raise typer.Exit(1)

        console.print("📂 در حال جایگزینی فایل‌های نسخه جدید (با حفظ کامل دیتابیس، venv و کلیدهای .env)...")
        for item_name in CODE_ITEMS_TO_SYNC:
            src_item = source_root / item_name
            dst_item = project_root / item_name
            if not src_item.exists():
                continue
            if src_item.is_dir():
                if dst_item.exists():
                    shutil.rmtree(dst_item)
                shutil.copytree(src_item, dst_item)
            else:
                shutil.copy2(src_item, dst_item)

    # ۳. به‌روزرسانی پکیج‌های پایتون و اجرای مایگریشن‌ها
    console.print("📦 در حال به‌روزرسانی وابستگی‌های پایتون داخل محیط مجازی...")
    subprocess.run(
        pip_cmd + ["install", "-r", str(project_root / "requirements.txt")],
        cwd=str(project_root),
        env=env,
        check=False,
    )

    db_migrate()
    restart_systemd_service_if_active(SERVICE_NAME)
    console.print(
        f"[bold green]🎉 آپدیت مستقیم از گیت‌هاب ({GITHUB_REPO}) با موفقیت انجام شد و سرویس ریستارت گردید![/bold green]"
    )


@app.command("rollback")
def cmd_rollback() -> None:
    """بازگشت سریع به اسنپ‌شات نسخه قبلی (Rollback) در صورت بروز مشکل پس از آپدیت."""
    project_root = Path(__file__).resolve().parent.parent
    cfg = load_settings()
    rollback_tar_path = Path(cfg.backup_dir) / "rollback_snapshot.tar.gz"
    if not rollback_tar_path.is_file():
        fallback = project_root / "backups" / "rollback_snapshot.tar.gz"
        if fallback.is_file():
            rollback_tar_path = fallback
        else:
            console.print("[red]❌ هیچ اسنپ‌شات بازگشتی (rollback_snapshot.tar.gz) یافت نشد.[/red]")
            raise typer.Exit(1)

    console.print("⏪ در حال بازیابی فایل‌های کد از اسنپ‌شات نسخه قبلی...")
    with tarfile.open(str(rollback_tar_path), mode="r:gz") as r_tar:
        for item_name in CODE_ITEMS_TO_SYNC:
            dst_item = project_root / item_name
            if dst_item.is_dir():
                shutil.rmtree(dst_item, ignore_errors=True)
        r_tar.extractall(path=str(project_root))

    restart_systemd_service_if_active(SERVICE_NAME)
    console.print("[bold green]✅ بازگشت به نسخه قبلی (Rollback) با موفقیت انجام شد و سرویس ریستارت گردید.[/bold green]")


@app.command("uninstall")
def cmd_uninstall(
    keep_data: bool = typer.Option(True, "--keep-data/--purge-data", help="نگه‌داشتن دیتابیس و کلیدها یا حذف کامل")
) -> None:
    """حذف سرویس و فایل‌های اجرایی ربات (با گزینه حفظ یا حذف داده‌ها)."""
    if not Confirm.ask("🚨 آیا از حذف ربات AnonBot از روی سرور مطمئن هستید؟", default=False):
        console.print("عملیات لغو شد.")
        return

    uninstall_script = Path(__file__).resolve().parent.parent / "scripts" / "uninstall.sh"
    if uninstall_script.is_file():
        flag = "--keep-data" if keep_data else "--purge-data"
        subprocess.run(["bash", str(uninstall_script), "--yes", flag], check=False)
    else:
        subprocess.run(["systemctl", "stop", SERVICE_NAME], check=False)
        subprocess.run(["systemctl", "disable", SERVICE_NAME], check=False)
        console.print("[green]سرویس متوقف و غیرفعال شد.[/green]")


# ==============================================================================
# ۷. منوی تعاملی شماره‌دار و رنگی هنگام اجرای بدون آرگومان
# ==============================================================================
def interactive_menu() -> None:
    while True:
        console.print(
            Panel(
                "[bold cyan]1.[/bold cyan] 🟢 وضعیت سرویس و سلامت جامع (Doctor)\n"
                "[bold cyan]2.[/bold cyan] 🔄 شروع / توقف / ریستارت سرویس ربات\n"
                "[bold cyan]3.[/bold cyan] 📜 مشاهده لاگ‌های ربات\n"
                "[bold cyan]4.[/bold cyan] 🌐 مدیریت پروکسی تلگرام (تنظیم، تست، روشن/خاموش)\n"
                "[bold cyan]5.[/bold cyan] ⚙️ مشاهده و ویرایش تنظیمات (.env) و توکن ربات\n"
                "[bold cyan]6.[/bold cyan] 📊 مشاهده آمار زنده ربات\n"
                "[bold cyan]7.[/bold cyan] 👥 مدیریت کاربران و صندوق‌های ناشناس\n"
                "[bold cyan]8.[/bold cyan] 💾 بک‌آپ رمزنگاری‌شده (دیتابیس + کلیدها) و بازیابی\n"
                "[bold cyan]9.[/bold cyan] 🚀 آپدیت مستقیم از گیت‌هاب / Rollback / حالت تعمیرات\n"
                "[bold cyan]0.[/bold cyan] 🚪 خروج",
                title="🤖 پنل مدیریت خط فرمان AnonBot (منوی تعاملی)",
                subtitle="یک عدد را وارد کنید",
            )
        )
        choice = Prompt.ask("انتخاب شما", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")

        if choice == "0":
            console.print("[bold green]خدانگهدار![/bold green]")
            break
        elif choice == "1":
            cmd_doctor()
        elif choice == "2":
            act = Prompt.ask("عملیات سرویس", choices=["start", "stop", "restart", "status"], default="restart")
            if act == "start":
                cmd_start()
            elif act == "stop":
                cmd_stop()
            elif act == "restart":
                cmd_restart()
            else:
                cmd_status()
        elif choice == "3":
            cmd_logs(follow=False, lines=50)
        elif choice == "4":
            proxy_show()
            p_act = Prompt.ask(
                "عملیات پروکسی",
                choices=["set", "test", "enable", "disable", "remove", "back"],
                default="test",
            )
            if p_act == "set":
                u = Prompt.ask("آدرس پروکسی (مثلاً socks5://user:pass@127.0.0.1:1080)")
                proxy_set(u)
            elif p_act == "test":
                try:
                    proxy_test()
                except typer.Exit:
                    pass
            elif p_act == "enable":
                proxy_enable()
            elif p_act == "disable":
                proxy_disable()
            elif p_act == "remove":
                proxy_remove()
        elif choice == "5":
            config_show()
            if Confirm.ask("آیا می‌خواهید مقداری را ویرایش کنید؟", default=False):
                k = Prompt.ask("نام کلید (مثلاً RATE_LIMIT_PER_MINUTE)")
                v = Prompt.ask("مقدار جدید")
                config_set(k, v)
        elif choice == "6":
            cmd_stats()
        elif choice == "7":
            sub = Prompt.ask("بخش مورد نظر", choices=["users", "inboxes", "ban", "unban"], default="users")
            if sub == "users":
                user_list(limit=20)
            elif sub == "inboxes":
                inbox_list(limit=20)
            elif sub == "ban":
                uid = int(Prompt.ask("آیدی عددی کاربر"))
                user_ban(uid)
            elif sub == "unban":
                uid = int(Prompt.ask("آیدی عددی کاربر"))
                user_unban(uid)
        elif choice == "8":
            b_act = Prompt.ask("عملیات دیتابیس", choices=["backup", "restore", "migrate"], default="backup")
            if b_act == "backup":
                db_backup(passphrase=None)
            elif b_act == "restore":
                fp = Prompt.ask("مسیر کامل فایل بک‌آپ (.tar.gz.enc)")
                db_restore(fp, passphrase=None)
            elif b_act == "migrate":
                db_migrate()
        elif choice == "9":
            u_act = Prompt.ask("عملیات", choices=["update", "rollback", "maint-on", "maint-off"], default="update")
            if u_act == "update":
                cmd_update()
            elif u_act == "rollback":
                cmd_rollback()
            elif u_act == "maint-on":
                cmd_maintenance("on")
            elif u_act == "maint-off":
                cmd_maintenance("off")


@app.callback(invoke_without_command=True)
def main_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        interactive_menu()


if __name__ == "__main__":
    app()
