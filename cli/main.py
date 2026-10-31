import asyncio
import datetime
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
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
from app.db.session import get_session_factory, init_db_engine
from app.services.broadcaster import execute_broadcast_job
from app.services.telegram_helpers import build_bot_session, test_telegram_connection
from app.utils.crypto import CryptoManager, mask_secret
from cli.env_manager import read_env_dict, restart_systemd_service_if_active, write_env_key

console = Console()
app = typer.Typer(
    name="anonbot",
    help="AnonBot Management CLI (Multi-Inbox Anonymous Telegram Bot)",
    invoke_without_command=True,
)

config_app = typer.Typer(help="View and modify .env configuration")
token_app = typer.Typer(help="Manage and verify Telegram Bot Token")
admin_app = typer.Typer(help="Manage Super Admins")
user_app = typer.Typer(help="Manage bot users (list/info/ban/unban)")
inbox_app = typer.Typer(help="Manage anonymous inboxes")
db_app = typer.Typer(help="Database migrations, encrypted backups & restore")
proxy_app = typer.Typer(help="Manage SOCKS5 / HTTP proxy for Telegram connection")

app.add_typer(config_app, name="config")
app.add_typer(token_app, name="token")
app.add_typer(admin_app, name="admin")
app.add_typer(user_app, name="user")
app.add_typer(inbox_app, name="inbox")
app.add_typer(db_app, name="db")
app.add_typer(proxy_app, name="proxy")

SERVICE_NAME = "anonbot.service"
APP_DIR = Path(os.environ.get("ANONBOT_APP_DIR", "/opt/anonbot"))
GITHUB_TARBALL_MAIN = "https://github.com/arimakomi/staranon/archive/refs/heads/main.tar.gz"
GITHUB_TARBALL_MASTER = "https://github.com/arimakomi/staranon/archive/refs/heads/master.tar.gz"


def get_repo_context():
    cfg = load_settings()
    init_db_engine(cfg.database_url)
    hmac_sec = cfg.hmac_secret if len(cfg.hmac_secret) >= 16 else ("0" * 32)
    try:
        crypto = CryptoManager(hmac_sec, cfg.fernet_key)
    except Exception:
        _, f_key = CryptoManager.generate_new_secrets()
        crypto = CryptoManager(hmac_sec, f_key)
    return cfg, get_session_factory(), crypto


def validate_proxy_format(url: str) -> tuple[bool, str]:
    cleaned = url.strip()
    if cleaned.lower().startswith(("tg://", "https://t.me/proxy")):
        return (
            False,
            "[ERROR] Telegram MTProto proxies are not supported by Bot API (HTTPS REST). "
            "Please provide a standard socks5:// or http:// proxy.",
        )
    if not cleaned.lower().startswith(("socks5://", "socks5h://", "http://", "https://")):
        return False, "[ERROR] Proxy URL must start with socks5:// or http://"
    return True, ""


def run_systemctl(action: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", action, SERVICE_NAME],
        capture_output=True,
        text=True,
        check=False,
    )


def restart_service_if_running() -> None:
    if restart_systemd_service_if_active("anonbot"):
        console.print("[green][OK] Service anonbot.service restarted.[/green]")


# ==============================================================================
# 1. Service Lifecycle Commands
# ==============================================================================
@app.command("status")
def cmd_status() -> None:
    """Show systemd service status."""
    res = subprocess.run(["systemctl", "status", SERVICE_NAME, "--no-pager"], text=True, check=False)
    if res.returncode != 0:
        console.print("[yellow][!] Service anonbot.service is not active or not installed.[/yellow]")


@app.command("start")
def cmd_start() -> None:
    """Start the bot service."""
    res = run_systemctl("start")
    if res.returncode == 0:
        console.print("[green][OK] Bot service started.[/green]")
    else:
        console.print(f"[red][ERROR] Failed to start service: {res.stderr}[/red]")


@app.command("stop")
def cmd_stop() -> None:
    """Stop the bot service."""
    res = run_systemctl("stop")
    if res.returncode == 0:
        console.print("[yellow][OK] Bot service stopped.[/yellow]")
    else:
        console.print(f"[red][ERROR] Failed to stop service: {res.stderr}[/red]")


@app.command("restart")
def cmd_restart() -> None:
    """Restart the bot service."""
    res = run_systemctl("restart")
    if res.returncode == 0:
        console.print("[green][OK] Bot service restarted.[/green]")
    else:
        console.print(f"[red][ERROR] Failed to restart service: {res.stderr}[/red]")


@app.command("logs")
def cmd_logs(
    lines: int = typer.Option(50, "-n", "--lines", help="Number of log lines to show"),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow live log output"),
) -> None:
    """Show application and systemd logs."""
    cfg = load_settings()
    log_file = Path(cfg.log_dir) / "anonbot.log"
    if log_file.exists():
        cmd = ["tail", "-n", str(lines)]
        if follow:
            cmd.append("-f")
        cmd.append(str(log_file))
        subprocess.run(cmd, check=False)
    else:
        cmd = ["journalctl", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager"]
        if follow:
            cmd.append("-f")
        subprocess.run(cmd, check=False)


# ==============================================================================
# 2. Config, Token & Super Admin Commands
# ==============================================================================
@config_app.command("show")
def config_show() -> None:
    """Show current .env configuration (secrets are masked)."""
    env_path = find_env_file()
    data = read_env_dict(env_path)
    table = Table(title=f"Configuration ({env_path})")
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")
    sensitive = {"BOT_TOKEN", "HMAC_SECRET", "FERNET_KEY", "WEBHOOK_SECRET", "PROXY_URL", "DATABASE_URL"}
    for k, v in data.items():
        display = mask_secret(v) if (k in sensitive and v) else v
        table.add_row(k, display)
    console.print(table)


@config_app.command("get")
def config_get(key: str) -> None:
    """Get a single configuration value."""
    data = read_env_dict()
    if key not in data:
        console.print(f"[red]Key {key} not found.[/red]")
        raise typer.Exit(1)
    console.print(f"{key}={data[key]}")


@config_app.command("set")
def config_set(key: str, value: str) -> None:
    """Set a configuration value in .env and restart the service."""
    if key in ("HMAC_SECRET", "FERNET_KEY"):
        console.print("[bold red][WARNING] Changing HMAC_SECRET or FERNET_KEY will break decryption of existing messages![/bold red]")
        if not Confirm.ask("Are you sure you want to overwrite this cryptographic key?"):
            raise typer.Exit(0)
    write_env_key(key, value)
    console.print(f"[green][OK] {key} updated.[/green]")
    restart_service_if_running()


@token_app.command("set")
def token_set(new_token: str) -> None:
    """Test a new Bot Token via getMe, save it to .env, and restart."""
    cfg = load_settings()
    proxy = cfg.proxy_url if cfg.proxy_enabled else None
    console.print("[cyan][*] Verifying token with Telegram API (getMe)...[/cyan]")
    ok, info, latency = asyncio.run(test_telegram_connection(new_token, proxy, cfg.api_base_url))
    if not ok:
        console.print(f"[red][ERROR] Token verification failed: {info}[/red]")
        raise typer.Exit(1)
    username = info.split()[0].lstrip("@") if info else ""
    write_env_key("BOT_TOKEN", new_token)
    if username:
        write_env_key("BOT_USERNAME", username)
    console.print(f"[green][OK] Verified {info} ({latency:.0f} ms) and saved to .env.[/green]")
    restart_service_if_running()


@admin_app.command("list")
def admin_list() -> None:
    """List Super Admin Telegram User IDs."""
    cfg = load_settings()
    console.print(Panel("\n".join(str(x) for x in cfg.super_admins) or "Empty", title="Super Admins (SUPER_ADMINS)"))


@admin_app.command("add")
def admin_add(user_id: int) -> None:
    """Add a Super Admin User ID."""
    cfg = load_settings()
    ids = set(cfg.super_admins)
    ids.add(user_id)
    write_env_key("SUPER_ADMINS", ",".join(str(x) for x in sorted(ids)))
    console.print(f"[green][OK] Added {user_id} to SUPER_ADMINS.[/green]")
    restart_service_if_running()


@admin_app.command("remove")
def admin_remove(user_id: int) -> None:
    """Remove a Super Admin User ID."""
    cfg = load_settings()
    ids = [x for x in cfg.super_admins if x != user_id]
    write_env_key("SUPER_ADMINS", ",".join(str(x) for x in ids))
    console.print(f"[yellow][OK] Removed {user_id} from SUPER_ADMINS.[/yellow]")
    restart_service_if_running()


# ==============================================================================
# 3. Proxy Commands (SOCKS5 / HTTP)
# ==============================================================================
@proxy_app.command("set")
def proxy_set(url: str) -> None:
    """Validate and set SOCKS5 or HTTP proxy URL."""
    ok, err = validate_proxy_format(url)
    if not ok:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(1)
    write_env_key("PROXY_URL", url.strip())
    write_env_key("PROXY_ENABLED", "true")
    console.print(f"[green][OK] Proxy saved and enabled: {mask_secret(url.strip())}[/green]")
    restart_service_if_running()


@proxy_app.command("show")
def proxy_show() -> None:
    """Show current proxy configuration (password masked)."""
    data = read_env_dict()
    enabled = data.get("PROXY_ENABLED", "false")
    url = data.get("PROXY_URL", "")
    console.print(
        Panel(
            f"Enabled : [bold]{enabled}[/bold]\n"
            f"URL     : [cyan]{mask_secret(url) if url else 'Not configured'}[/cyan]",
            title="Proxy Configuration",
        )
    )


@proxy_app.command("enable")
def proxy_enable() -> None:
    """Enable the configured proxy."""
    data = read_env_dict()
    if not data.get("PROXY_URL"):
        console.print("[red][ERROR] PROXY_URL is empty. Run `anonbot proxy set <url>` first.[/red]")
        raise typer.Exit(1)
    write_env_key("PROXY_ENABLED", "true")
    console.print("[green][OK] Proxy enabled.[/green]")
    restart_service_if_running()


@proxy_app.command("disable")
def proxy_disable() -> None:
    """Disable proxy without deleting the URL."""
    write_env_key("PROXY_ENABLED", "false")
    console.print("[yellow][OK] Proxy disabled.[/yellow]")
    restart_service_if_running()


@proxy_app.command("remove")
def proxy_remove() -> None:
    """Remove proxy configuration completely."""
    write_env_key("PROXY_ENABLED", "false")
    write_env_key("PROXY_URL", "")
    console.print("[yellow][OK] Proxy removed.[/yellow]")
    restart_service_if_running()


@proxy_app.command("test")
def proxy_test() -> None:
    """Test connection to Telegram API (getMe) via proxy and measure latency."""
    cfg = load_settings()
    proxy = cfg.proxy_url if cfg.proxy_enabled and cfg.proxy_url else cfg.proxy_url
    if not proxy:
        console.print("[yellow][!] No proxy configured; testing direct connection to Telegram...[/yellow]")
    else:
        console.print(f"[cyan][*] Testing connection via proxy {mask_secret(proxy)}...[/cyan]")
    ok, info, latency = asyncio.run(test_telegram_connection(cfg.bot_token, proxy, cfg.api_base_url))
    if ok:
        console.print(f"[bold green][OK] Connected! Bot: {info} | Ping: {latency:.1f} ms[/bold green]")
    else:
        console.print(f"[bold red][FAIL] Connection failed ({latency:.1f} ms): {info}[/bold red]")
        raise typer.Exit(1)


# ==============================================================================
# 4. Users, Inboxes, Stats, Broadcast & Maintenance
# ==============================================================================
@user_app.command("list")
def user_list(limit: int = 30) -> None:
    """List recent bot users."""
    async def _run():
        _, factory, _ = get_repo_context()
        async with factory() as session:
            res = await session.execute(select(User).order_by(User.created_at.desc()).limit(limit))
            return list(res.scalars().all())

    users = asyncio.run(_run())
    table = Table(title=f"Bot Users (Last {len(users)})")
    table.add_column("User ID", style="cyan")
    table.add_column("Username")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Valid Reports")
    for u in users:
        status = "[red]Banned[/red]" if u.is_banned else "[green]Active[/green]"
        table.add_row(str(u.id), f"@{u.username}" if u.username else "—", u.first_name or "—", status, str(u.valid_reports_count))
    console.print(table)


@user_app.command("info")
def user_info(user_id: int) -> None:
    """Show detailed information about a user."""
    async def _run():
        _, factory, crypto = get_repo_context()
        async with factory() as session:
            repo = BotRepository(session, crypto)
            u = await repo.get_user(user_id)
            inboxes = await repo.get_user_owned_inboxes(user_id) if u else []
            return u, inboxes

    u, inboxes = asyncio.run(_run())
    if not u:
        console.print("[red]User not found.[/red]")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"User ID       : {u.id}\n"
            f"Username      : @{u.username or '—'}\n"
            f"First Name    : {u.first_name or '—'}\n"
            f"Banned        : {u.is_banned} (Until: {u.banned_until or '—'})\n"
            f"Valid Reports : {u.valid_reports_count}\n"
            f"Inboxes Count : {len(inboxes)}",
            title=f"User Info #{u.id}",
        )
    )


@user_app.command("ban")
def user_ban(
    user_id: int,
    hours: Optional[int] = typer.Option(None, "--hours", help="Temporary ban duration in hours (omit for permanent)"),
    reason: str = typer.Option("Banned via CLI", "--reason"),
) -> None:
    """Ban a user from the bot."""
    async def _run():
        _, factory, crypto = get_repo_context()
        async with factory() as session:
            repo = BotRepository(session, crypto)
            return await repo.ban_user(user_id, reason=reason, duration_hours=hours)

    if asyncio.run(_run()):
        console.print(f"[green][OK] User {user_id} banned.[/green]")
    else:
        console.print("[red]User not found.[/red]")


@user_app.command("unban")
def user_unban(user_id: int) -> None:
    """Unban a user."""
    async def _run():
        _, factory, crypto = get_repo_context()
        async with factory() as session:
            repo = BotRepository(session, crypto)
            return await repo.unban_user(user_id)

    if asyncio.run(_run()):
        console.print(f"[green][OK] User {user_id} unbanned.[/green]")
    else:
        console.print("[red]User not found.[/red]")


@inbox_app.command("list")
def inbox_list(limit: int = 30) -> None:
    """List created anonymous inboxes."""
    async def _run():
        _, factory, _ = get_repo_context()
        async with factory() as session:
            res = await session.execute(select(Inbox).order_by(Inbox.created_at.desc()).limit(limit))
            return list(res.scalars().all())

    inboxes = asyncio.run(_run())
    table = Table(title="Anonymous Inboxes")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Owner ID")
    table.add_column("Active")
    table.add_column("Messages")
    for ib in inboxes:
        table.add_row(
            str(ib.id),
            ib.title,
            ib.type,
            str(ib.owner_id),
            "[green]Yes[/green]" if ib.is_active else "[red]No[/red]",
            str(ib.messages_count),
        )
    console.print(table)


@inbox_app.command("info")
def inbox_info(inbox_id: int) -> None:
    """Show details of a specific inbox."""
    async def _run():
        _, factory, crypto = get_repo_context()
        async with factory() as session:
            repo = BotRepository(session, crypto)
            return await repo.get_inbox_by_id(inbox_id)

    ib = asyncio.run(_run())
    if not ib:
        console.print("[red]Inbox not found.[/red]")
        raise typer.Exit(1)
    console.print(
        Panel(
            f"Inbox ID   : {ib.id}\n"
            f"Title      : {ib.title}\n"
            f"Type       : {ib.type}\n"
            f"Owner ID   : {ib.owner_id}\n"
            f"Active     : {ib.is_active}\n"
            f"Token      : ib_{ib.token}\n"
            f"Slug       : {ib.custom_slug or '—'}\n"
            f"Received   : {ib.messages_count}\n"
            f"Replied    : {ib.replies_count}",
            title=f"Inbox #{ib.id}",
        )
    )


@inbox_app.command("disable")
def inbox_disable(inbox_id: int) -> None:
    """Disable an inbox."""
    async def _run():
        _, factory, crypto = get_repo_context()
        async with factory() as session:
            repo = BotRepository(session, crypto)
            ib = await repo.get_inbox_by_id(inbox_id)
            if not ib:
                return False
            ib.is_active = False
            await session.commit()
            return True

    if asyncio.run(_run()):
        console.print(f"[yellow][OK] Inbox #{inbox_id} disabled.[/yellow]")
    else:
        console.print("[red]Inbox not found.[/red]")


@inbox_app.command("enable")
def inbox_enable(inbox_id: int) -> None:
    """Enable an inbox."""
    async def _run():
        _, factory, crypto = get_repo_context()
        async with factory() as session:
            repo = BotRepository(session, crypto)
            ib = await repo.get_inbox_by_id(inbox_id)
            if not ib:
                return False
            ib.is_active = True
            await session.commit()
            return True

    if asyncio.run(_run()):
        console.print(f"[green][OK] Inbox #{inbox_id} enabled.[/green]")
    else:
        console.print("[red]Inbox not found.[/red]")


@app.command("stats")
def cmd_stats() -> None:
    """Display overall bot statistics."""
    async def _run():
        _, factory, crypto = get_repo_context()
        async with factory() as session:
            repo = BotRepository(session, crypto)
            return await repo.get_live_stats()

    st = asyncio.run(_run())
    table = Table(title="AnonBot Global Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="bold green")
    table.add_row("Total Users", str(st["total_users"]))
    table.add_row("Active Users (Last 24h)", str(st["active_24h"]))
    table.add_row("Total Inboxes", str(st["total_inboxes"]))
    table.add_row("Messages (Last 24h)", str(st["messages_today"]))
    table.add_row("Open Abuse Reports", str(st["open_reports"]))
    table.add_row("Banned Users", str(st["banned_users"]))
    console.print(table)


@app.command("broadcast")
def cmd_broadcast(text: str) -> None:
    """Send a text broadcast message to all active users from CLI."""
    async def _run():
        cfg, factory, _ = get_repo_context()
        async with factory() as session:
            job = BroadcastJob(
                created_by=cfg.super_admins[0] if cfg.super_admins else 0,
                mode="text",
                text_content=text,
                status="running",
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        bot = Bot(token=cfg.bot_token, session=build_bot_session(cfg))
        try:
            return await execute_broadcast_job(bot, job_id)
        finally:
            await bot.session.close()

    console.print("[cyan][*] Broadcasting message to active users...[/cyan]")
    job = asyncio.run(_run())
    if job:
        console.print(
            f"[green][OK] Broadcast complete! Total: {job.total_users} | Sent: {job.sent_count} | "
            f"Blocked: {job.blocked_count} | Failed: {job.failed_count}[/green]"
        )


@app.command("maintenance")
def cmd_maintenance(state: str = typer.Argument(..., help="on or off")) -> None:
    """Toggle bot maintenance mode (on / off)."""
    val = "true" if state.lower() in ("on", "true", "1") else "false"
    write_env_key("MAINTENANCE_MODE", val)
    console.print(f"[green][OK] Maintenance mode set to: {'ON' if val == 'true' else 'OFF'}.[/green]")
    restart_service_if_running()


# ==============================================================================
# 5. Database Migrations, Encrypted Backup (with Secret Keys) & Restore
# ==============================================================================
@db_app.command("migrate")
def db_migrate() -> None:
    """Run database migrations (Alembic if present + SQLAlchemy create_all_tables)."""
    alembic_ini = APP_DIR / "alembic.ini"
    alembic_dir = APP_DIR / "alembic"
    cwd = APP_DIR if alembic_ini.exists() else Path.cwd()
    if (cwd / "alembic.ini").exists() and (cwd / "alembic").is_dir():
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(cwd), check=False)
    try:
        from app.db.session import create_all_tables
        cfg = load_settings()
        init_db_engine(cfg.database_url)
        asyncio.run(create_all_tables())
        console.print("[green][OK] Database schema & migrations applied successfully.[/green]")
    except Exception as exc:
        console.print(f"[red][ERROR] Failed to apply database schema: {exc}[/red]")
        raise typer.Exit(1)


@db_app.command("backup")
def db_backup(
    passphrase: Optional[str] = typer.Option(None, "--passphrase", "-p", help="Encryption passphrase (defaults to FERNET_KEY)"),
) -> None:
    """Create an encrypted backup (.tar.gz.enc) containing the database AND secret keys (FERNET_KEY + HMAC_SECRET)."""
    cfg = load_settings()
    backup_dir = Path(cfg.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"anonbot_backup_{timestamp}.tar.gz.enc"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        if cfg.database_url.startswith("sqlite"):
            sqlite_path = cfg.database_url.split("///")[-1]
            src_db = Path(sqlite_path)
            if src_db.exists():
                shutil.copy2(src_db, tmp_path / "anonbot.db")
        else:
            dump_file = tmp_path / "mysql_dump.sql"
            subprocess.run(
                ["mysqldump", cfg.database_url],
                stdout=dump_file.open("w"),
                check=False,
            )

        secrets_manifest = {
            "created_at": timestamp,
            "HMAC_SECRET": cfg.hmac_secret,
            "FERNET_KEY": cfg.fernet_key,
            "DATABASE_URL": cfg.database_url,
            "WARNING": "THIS FILE CONTAINS CRYPTOGRAPHIC KEYS. KEEP IT STRICTLY CONFIDENTIAL.",
        }
        (tmp_path / "secrets_keys.json").write_text(
            json.dumps(secrets_manifest, indent=2), encoding="utf-8"
        )
        env_file = Path(cfg.env_file_path)
        if env_file.exists():
            shutil.copy2(env_file, tmp_path / ".env.backup")

        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
            for item in tmp_path.iterdir():
                tar.add(item, arcname=item.name)

        effective_pass = passphrase or cfg.fernet_key
        encrypted_bytes = CryptoManager.encrypt_backup_bytes(tar_buffer.getvalue(), effective_pass)
        backup_path.write_bytes(encrypted_bytes)
        os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)

    console.print(
        Panel(
            f"[bold green][OK] Encrypted backup created:[/bold green]\n"
            f"[cyan]{backup_path}[/cyan]\n\n"
            f"[bold yellow][SECURITY WARNING]:[/bold yellow]\n"
            f"This backup archive includes your database AND your secret keys (HMAC_SECRET & FERNET_KEY)\n"
            f"so that encrypted sender IDs remain functional after a restore.\n"
            f"Encryption passphrase used: {'Custom passphrase' if passphrase else 'Current FERNET_KEY from .env'}",
            title="Encrypted Backup & Keys",
        )
    )


@db_app.command("restore")
def db_restore(
    backup_file: Path = typer.Argument(..., help="Path to .tar.gz.enc backup file"),
    passphrase: Optional[str] = typer.Option(None, "--passphrase", "-p", help="Decryption passphrase (defaults to FERNET_KEY)"),
) -> None:
    """Decrypt and restore database and cryptographic keys from a backup file."""
    if not backup_file.exists():
        console.print(f"[red]Backup file {backup_file} not found.[/red]")
        raise typer.Exit(1)

    cfg = load_settings()
    effective_pass = passphrase or cfg.fernet_key
    try:
        raw_tar = CryptoManager.decrypt_backup_bytes(backup_file.read_bytes(), effective_pass)
    except Exception as exc:
        console.print(f"[red][ERROR] Failed to decrypt backup (invalid passphrase/key): {exc}[/red]")
        raise typer.Exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:gz") as tar:
            tar.extractall(path=tmp_path)

        db_backup = tmp_path / "anonbot.db"
        if db_backup.exists() and cfg.database_url.startswith("sqlite"):
            sqlite_path = Path(cfg.database_url.split("///")[-1])
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db_backup, sqlite_path)
            console.print(f"[green][OK] SQLite database restored to {sqlite_path}.[/green]")

        keys_file = tmp_path / "secrets_keys.json"
        if keys_file.exists():
            keys_data = json.loads(keys_file.read_text(encoding="utf-8"))
            write_env_key("HMAC_SECRET", keys_data["HMAC_SECRET"])
            write_env_key("FERNET_KEY", keys_data["FERNET_KEY"])
            console.print("[green][OK] Cryptographic keys (HMAC_SECRET & FERNET_KEY) restored to .env.[/green]")

    restart_service_if_running()


@db_app.command("auto-backup")
def db_auto_backup(
    enable: bool = typer.Option(True, "--enable/--disable", help="Enable or disable daily cron backup at 03:00 AM"),
) -> None:
    """Configure daily automated encrypted backups via /etc/cron.d/anonbot-backup."""
    cron_file = Path("/etc/cron.d/anonbot-backup")
    if enable:
        cron_content = "0 3 * * * root /usr/local/bin/anonbot db backup >/dev/null 2>&1\n"
        cron_file.write_text(cron_content, encoding="utf-8")
        os.chmod(cron_file, 0o644)
        console.print("[green][OK] Daily automated backup enabled (03:00 AM).[/green]")
    else:
        if cron_file.exists():
            cron_file.unlink()
        console.print("[yellow][OK] Daily automated backup disabled.[/yellow]")


# ==============================================================================
# 6. Health Check (doctor), Direct GitHub Update & Rollback
# ==============================================================================
@app.command("doctor")
def cmd_doctor() -> None:
    """Run full diagnostic check (systemd, .env 600 permissions, keys, DB, Telegram ping, proxy, disk)."""
    cfg = load_settings()
    env_file = Path(cfg.env_file_path)
    table = Table(title="AnonBot System Health Check (Doctor)")
    table.add_column("Component", style="cyan")
    table.add_column("Status")
    table.add_column("Details")

    # 1. systemd service
    srv = run_systemctl("is-active")
    srv_active = srv.stdout.strip() == "active"
    table.add_row(
        "systemd Service",
        "[green]OK[/green]" if srv_active else "[yellow]INACTIVE[/yellow]",
        srv.stdout.strip() or "inactive",
    )

    # 2. .env permissions (600)
    if env_file.exists():
        mode = oct(env_file.stat().st_mode & 0o777)
        perm_ok = mode == "0o600"
        table.add_row(
            ".env Permissions",
            "[green]OK (600)[/green]" if perm_ok else f"[yellow]WARN ({mode})[/yellow]",
            str(env_file),
        )
    else:
        table.add_row(".env File", "[red]MISSING[/red]", str(env_file))

    # 3. Cryptographic keys
    try:
        crypto = CryptoManager(cfg.hmac_secret, cfg.fernet_key)
        sample = crypto.encrypt_sender_id(12345)
        assert crypto.decrypt_sender_id(sample) == 12345
        table.add_row("Crypto Keys (Fernet/HMAC)", "[green]OK[/green]", "Encryption & decryption verified")
    except Exception as exc:
        table.add_row("Crypto Keys (Fernet/HMAC)", "[red]ERROR[/red]", str(exc))

    # 4. Database connection
    try:
        async def _check_db():
            _, factory, c = get_repo_context()
            async with factory() as session:
                repo = BotRepository(session, c)
                return await repo.get_live_stats()
        st = asyncio.run(_check_db())
        table.add_row("Database", "[green]OK[/green]", f"Users: {st['total_users']} | Inboxes: {st['total_inboxes']}")
    except Exception as exc:
        table.add_row("Database", "[red]ERROR[/red]", str(exc))

    # 5. Telegram API & Proxy connectivity
    proxy = cfg.proxy_url if cfg.proxy_enabled else None
    ok, info, latency = asyncio.run(test_telegram_connection(cfg.bot_token, proxy, cfg.api_base_url))
    table.add_row(
        "Telegram API (getMe)",
        "[green]OK[/green]" if ok else "[red]ERROR[/red]",
        f"{info} ({latency:.0f} ms)" if ok else info,
    )

    # 6. Disk space
    usage = shutil.disk_usage("/")
    free_gb = usage.free / (1024 ** 3)
    table.add_row(
        "Disk Space",
        "[green]OK[/green]" if free_gb > 1.0 else "[red]LOW[/red]",
        f"{free_gb:.2f} GB free",
    )

    console.print(table)


@app.command("update")
def cmd_update() -> None:
    """Update AnonBot by downloading the latest tarball directly from GitHub (no git pull), migrating DB, and restarting."""
    if not APP_DIR.exists():
        console.print(f"[red]Install directory {APP_DIR} does not exist.[/red]")
        raise typer.Exit(1)

    cfg = load_settings()
    backup_dir = Path(cfg.backup_dir)
    snapshot_dir = backup_dir / "last_code_snapshot"
    backup_dir.mkdir(parents=True, exist_ok=True)

    console.print("[cyan]1/5 Creating code snapshot for rollback...[/cyan]")
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for item in ("app", "cli", "alembic", "systemd", "scripts", "requirements.txt", "alembic.ini", "install.sh", "uninstall.sh"):
        src_item = APP_DIR / item
        if src_item.exists():
            if src_item.is_dir():
                shutil.copytree(src_item, snapshot_dir / item)
            else:
                shutil.copy2(src_item, snapshot_dir / item)

    console.print("[cyan]2/5 Downloading latest release archive from GitHub (arimakomi/staranon)...[/cyan]")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tarball_file = tmp_path / "staranon_main.tar.gz"
        curl_cmd = ["curl", "-fsSL", "--max-time", "60"]
        if cfg.proxy_enabled and cfg.proxy_url:
            curl_cmd.extend(["-x", cfg.proxy_url])
        dl = subprocess.run(curl_cmd + [GITHUB_TARBALL_MAIN, "-o", str(tarball_file)], capture_output=True, text=True, check=False)
        if dl.returncode != 0 or not tarball_file.exists():
            dl = subprocess.run(curl_cmd + [GITHUB_TARBALL_MASTER, "-o", str(tarball_file)], capture_output=True, text=True, check=False)
            if dl.returncode != 0 or not tarball_file.exists():
                console.print(f"[red][ERROR] Failed to download archive from GitHub: {dl.stderr}[/red]")
                raise typer.Exit(1)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball_file, mode="r:gz") as tar:
            tar.extractall(path=extract_dir)

        subfolders = [p for p in extract_dir.iterdir() if p.is_dir()]
        if not subfolders:
            console.print("[red][ERROR] Downloaded archive is empty.[/red]")
            raise typer.Exit(1)
        repo_root = subfolders[0]
        source_root = repo_root / "anonbot" if (repo_root / "anonbot" / "requirements.txt").exists() else repo_root
        if not (source_root / "requirements.txt").exists():
            console.print("[red][ERROR] Invalid archive structure (requirements.txt not found).[/red]")
            raise typer.Exit(1)

        console.print("[cyan]3/5 Replacing application files in /opt/anonbot...[/cyan]")
        for folder in ("app", "cli", "alembic", "systemd", "scripts"):
            src_folder = source_root / folder
            dst_folder = APP_DIR / folder
            if src_folder.exists():
                if dst_folder.exists():
                    shutil.rmtree(dst_folder)
                shutil.copytree(src_folder, dst_folder)
        for fname in ("requirements.txt", "alembic.ini", ".env.example", "install.sh", "uninstall.sh"):
            if (source_root / fname).exists():
                shutil.copy2(source_root / fname, APP_DIR / fname)

    pip_bin = APP_DIR / "venv" / "bin" / "pip"
    if pip_bin.exists():
        console.print("[cyan]4/5 Updating Python dependencies in virtualenv...[/cyan]")
        pip_cmd = [str(pip_bin), "install", "--quiet", "-r", str(APP_DIR / "requirements.txt")]
        if cfg.proxy_enabled and cfg.proxy_url:
            pip_cmd.extend(["--proxy", cfg.proxy_url])
        subprocess.run(pip_cmd, check=False)

    console.print("[cyan]5/5 Running database migrations and restarting service...[/cyan]")
    db_migrate()
    restart_service_if_running()
    console.print("[bold green][OK] Successfully updated AnonBot to the latest GitHub release![/bold green]")


@app.command("rollback")
def cmd_rollback() -> None:
    """Rollback to the previous code snapshot saved before the last `anonbot update`."""
    if not APP_DIR.exists():
        console.print(f"[red]Install directory {APP_DIR} does not exist.[/red]")
        raise typer.Exit(1)

    cfg = load_settings()
    snapshot_dir = Path(cfg.backup_dir) / "last_code_snapshot"
    if not snapshot_dir.exists() or not (snapshot_dir / "requirements.txt").exists():
        console.print("[red][ERROR] No previous code snapshot found for rollback.[/red]")
        raise typer.Exit(1)

    for item in ("app", "cli", "alembic", "systemd", "scripts", "requirements.txt", "alembic.ini", "install.sh", "uninstall.sh"):
        src_item = snapshot_dir / item
        dst_item = APP_DIR / item
        if src_item.exists():
            if src_item.is_dir():
                if dst_item.exists():
                    shutil.rmtree(dst_item)
                shutil.copytree(src_item, dst_item)
            else:
                shutil.copy2(src_item, dst_item)

    pip_bin = APP_DIR / "venv" / "bin" / "pip"
    if pip_bin.exists():
        subprocess.run([str(pip_bin), "install", "--quiet", "-r", str(APP_DIR / "requirements.txt")], check=False)
    restart_service_if_running()
    console.print("[green][OK] Rolled back to previous version and restarted service.[/green]")


# ==============================================================================
# 7. Interactive Numbered Terminal Menu (Default when run with no args)
# ==============================================================================
def interactive_menu() -> None:
    while True:
        console.print(
            Panel(
                "[1] Service Status & Health Check (doctor)\n"
                "[2] Restart Bot Service (restart)\n"
                "[3] View Recent Logs (logs)\n"
                "[4] Global Bot Statistics (stats)\n"
                "[5] Proxy Management (show / set / test / disable)\n"
                "[6] Encrypted Database & Key Backup (db backup)\n"
                "[7] Change Bot Token (token set)\n"
                "[8] Toggle Maintenance Mode (maintenance on/off)\n"
                "[9] Update from GitHub (update)\n"
                "[0] Exit",
                title="AnonBot Interactive Management Menu",
                border_style="cyan",
            )
        )
        choice = Prompt.ask("Select an option", choices=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], default="1")
        if choice == "0":
            break
        elif choice == "1":
            cmd_doctor()
        elif choice == "2":
            cmd_restart()
        elif choice == "3":
            cmd_logs(lines=40, follow=False)
        elif choice == "4":
            cmd_stats()
        elif choice == "5":
            proxy_show()
            sub = Prompt.ask("Action [1=Set Proxy, 2=Test Proxy, 3=Disable Proxy, 0=Back]", choices=["0", "1", "2", "3"], default="0")
            if sub == "1":
                url = Prompt.ask("Enter Proxy URL (socks5://user:pass@host:port or http://host:port)")
                try:
                    proxy_set(url)
                except typer.Exit:
                    pass
            elif sub == "2":
                try:
                    proxy_test()
                except typer.Exit:
                    pass
            elif sub == "3":
                proxy_disable()
        elif choice == "6":
            db_backup(passphrase=None)
        elif choice == "7":
            tok = Prompt.ask("Enter new Telegram Bot Token")
            try:
                token_set(tok)
            except typer.Exit:
                pass
        elif choice == "8":
            st = Prompt.ask("Maintenance Mode", choices=["on", "off"], default="off")
            cmd_maintenance(st)
        elif choice == "9":
            try:
                cmd_update()
            except typer.Exit:
                pass


@app.callback()
def main_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        interactive_menu()


if __name__ == "__main__":
    app()
