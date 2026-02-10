#!/usr/bin/env python3
"""
Stopkran daemon — Telegram bot + Unix socket server for
remote approval of Claude Code permission requests.

Usage:
    uv run python stopkran_daemon.py
"""

import asyncio
import json
import logging
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".config" / "stopkran" / "config.json"
SOCKET_PATH = "/tmp/stopkran.sock"
DEFAULT_TIMEOUT = 300  # seconds before auto-deny

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("stopkran")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        log.error("Config not found at %s — run stopkran_setup.py first", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)


# ---------------------------------------------------------------------------
# Pending-request registry
# ---------------------------------------------------------------------------

# request_id -> {event: asyncio.Event, decision: str|None, tg_message_id: int|None}
pending: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Register the first user as the owner."""
    cfg = ctx.bot_data["config"]
    chat_id = update.effective_chat.id

    if cfg.get("chat_id") is None:
        cfg["chat_id"] = chat_id
        save_config(cfg)
        await update.message.reply_text(
            f"✅ Registered! Chat ID: {chat_id}\n"
            "You will now receive permission requests here."
        )
        log.info("Owner registered: chat_id=%s", chat_id)
    elif cfg["chat_id"] == chat_id:
        await update.message.reply_text("You are already registered as the owner.")
    else:
        await update.message.reply_text("⛔ Another owner is already registered.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show the number of pending requests."""
    cfg = ctx.bot_data["config"]
    if update.effective_chat.id != cfg.get("chat_id"):
        return

    n = len(pending)
    if n == 0:
        text = "No pending permission requests."
    else:
        text = f"⏳ {n} pending permission request(s)."
    await update.message.reply_text(text)


async def resolve_request(request_id: str, action: str, app: Application):
    """Resolve a pending request and update its Telegram message."""
    entry = pending.get(request_id)
    if entry is None:
        return False

    entry["decision"] = action
    entry["event"].set()

    now = datetime.now(timezone.utc).strftime("%H:%M")
    emoji = "✅" if action == "allow" else "❌"
    label = "Allowed" if action == "allow" else "Denied"

    cfg = app.bot_data["config"]
    chat_id = cfg.get("chat_id")
    msg_id = entry.get("tg_message_id")
    if chat_id and msg_id:
        try:
            orig = entry.get("tg_message_text", "")
            await app.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=msg_id, reply_markup=None,
            )
            await app.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=orig + f"\n\n→ {emoji} {label} at {now}",
            )
        except Exception:
            pass
    return True


def get_oldest_pending_request_id() -> str | None:
    """Return the request_id of the oldest pending (undecided) request."""
    for rid, entry in pending.items():
        if entry["decision"] is None:
            return rid
    return None


# Text patterns for Apple Watch / quick replies
ALLOW_PATTERNS = {"да", "yes", "ок", "ok", "👍", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿", "✅"}
DENY_PATTERNS = {"нет", "no", "👎", "👎🏻", "👎🏼", "👎🏽", "👎🏾", "👎🏿", "❌"}


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle Allow / Deny button presses."""
    query = update.callback_query
    cfg = ctx.bot_data["config"]

    # Only the owner can respond
    if query.from_user.id != cfg.get("chat_id"):
        await query.answer("⛔ Not authorized", show_alert=True)
        return

    data = query.data  # "allow:<request_id>" or "deny:<request_id>"
    if ":" not in data:
        await query.answer("Invalid callback")
        return

    action, request_id = data.split(":", 1)
    if action not in ("allow", "deny"):
        await query.answer("Invalid action")
        return

    ok = await resolve_request(request_id, action, ctx.application)
    emoji = "✅" if action == "allow" else "❌"
    label = "Allowed" if action == "allow" else "Denied"

    if ok:
        await query.answer(f"{emoji} {label}")
    else:
        await query.answer("Request expired or already handled")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle text messages (Apple Watch / quick replies)."""
    cfg = ctx.bot_data["config"]
    if update.effective_chat.id != cfg.get("chat_id"):
        return

    text = (update.message.text or "").strip().lower()

    if text in ALLOW_PATTERNS:
        action = "allow"
    elif text in DENY_PATTERNS:
        action = "deny"
    else:
        return

    request_id = get_oldest_pending_request_id()
    if request_id is None:
        await update.message.reply_text("No pending requests.")
        return

    ok = await resolve_request(request_id, action, ctx.application)
    if ok:
        emoji = "✅" if action == "allow" else "❌"
        await update.message.reply_text(f"{emoji} Done")
    else:
        await update.message.reply_text("Request already handled.")


# ---------------------------------------------------------------------------
# Format the Telegram message for a permission request
# ---------------------------------------------------------------------------

def format_request_message(req: dict) -> str:
    tool = req.get("tool_name", "Unknown")
    cwd = req.get("cwd", "")
    session = req.get("session_id", "")[:8]
    tool_input = req.get("tool_input", {})

    # Build a human-readable snippet of the tool input
    if tool == "Bash":
        snippet = tool_input.get("command", "")
    elif tool == "Edit":
        fp = tool_input.get("file_path", "")
        old = (tool_input.get("old_string", "") or "")[:120]
        new = (tool_input.get("new_string", "") or "")[:120]
        snippet = f"{fp}\n-  {old}\n+  {new}"
    elif tool == "Write":
        fp = tool_input.get("file_path", "")
        content_preview = (tool_input.get("content", "") or "")[:200]
        snippet = f"{fp}\n{content_preview}"
    else:
        snippet = json.dumps(tool_input, ensure_ascii=False)[:300]

    lines = [
        "🔐 Permission Request",
        "",
        f"📂 {cwd}" if cwd else "",
        f"🔧 {tool}",
        "",
        snippet,
    ]
    if session:
        lines += ["", f"Session: {session}"]

    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------
# Unix socket server — IPC with stopkran_hook.py
# ---------------------------------------------------------------------------

async def handle_hook_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    app: Application,
    timeout: int,
):
    """Handle a single connection from stopkran_hook.py."""
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        if not line:
            return

        req = json.loads(line.decode("utf-8"))
        request_id = req["request_id"]
        log.info("Received request %s for tool=%s", request_id, req.get("tool_name"))

        cfg = app.bot_data["config"]
        chat_id = cfg.get("chat_id")

        if chat_id is None:
            log.warning("No owner registered — auto-denying request %s", request_id)
            response = json.dumps({"decision": "deny"}) + "\n"
            writer.write(response.encode("utf-8"))
            await writer.drain()
            return

        # Register the pending request
        event = asyncio.Event()
        # Send Telegram message with inline keyboard
        text = format_request_message(req)

        pending[request_id] = {
            "event": event,
            "decision": None,
            "tg_message_id": None,
            "tg_message_text": text,
        }
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Allow", callback_data=f"allow:{request_id}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"deny:{request_id}"),
            ]
        ])

        msg = await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
        )
        pending[request_id]["tg_message_id"] = msg.message_id

        # Wait for user decision or timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            decision = pending[request_id]["decision"] or "deny"
        except asyncio.TimeoutError:
            decision = "deny"
            log.info("Request %s timed out — auto-denied", request_id)
            try:
                await app.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=msg.message_id,
                    reply_markup=None,
                )
                await app.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg.message_id,
                    text=text + "\n\n→ ⏰ Timed out — auto-denied",
                )
            except Exception:
                pass

        # Clean up
        pending.pop(request_id, None)

        # Send decision back to hook
        response = json.dumps({"decision": decision}) + "\n"
        writer.write(response.encode("utf-8"))
        await writer.drain()
        log.info("Request %s resolved: %s", request_id, decision)

    except Exception as e:
        log.error("Error handling hook connection: %s", e)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def run_socket_server(app: Application, timeout: int):
    """Run the Unix domain socket server."""
    # Clean up stale socket
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    async def client_connected(reader, writer):
        await handle_hook_connection(reader, writer, app, timeout)

    server = await asyncio.start_unix_server(client_connected, path=SOCKET_PATH)
    # Restrict socket permissions to owner only
    os.chmod(SOCKET_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0o600

    log.info("Unix socket server listening on %s", SOCKET_PATH)

    async with server:
        await server.serve_forever()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    cfg = load_config()
    token = cfg.get("token")
    if not token:
        log.error("No bot token in config — run stopkran_setup.py first")
        sys.exit(1)

    timeout = cfg.get("timeout", DEFAULT_TIMEOUT)

    # Build the Telegram application
    app = Application.builder().token(token).build()
    app.bot_data["config"] = cfg

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Initialize the application (sets up the bot)
    await app.initialize()
    await app.start()

    # Start polling in background
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot started (polling)")

    # Run socket server (blocks until cancelled)
    try:
        await run_socket_server(app, timeout)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down…")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        # Clean up socket
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)


def main_sync():
    """Synchronous entry point for console_scripts."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted — exiting")


if __name__ == "__main__":
    main_sync()
