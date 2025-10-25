from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import List, Set

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# ─────────────────────────────── Config ────────────────────────────────
BOT_TOKEN = "8295867344:AAGlpBgBCkjiIC7HckYaGafh9pyj7dXYcwY"
IMAGE_PATH = Path("image.jpg")
CAPTION = "Привет"
TEXT_MESSAGES: List[str] = [
    "Здравствуйте",
    "Куку",
    "Хорошего дня",
    "Доброго дня!",
    "Привет всем!",
]
MESSAGES_PER_CYCLE_RANGE = (4, 4)
POST_EVERY_SECONDS = 24 * 60 * 60
CHANNELS_FILE = Path("channels.json")
VERSION = "v2.7"

# ───────────────────────────── Helpers ────────────────────────────────

def _log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True)


def _load_channels() -> Set[str]:
    if CHANNELS_FILE.exists():
        try:
            return set(json.loads(CHANNELS_FILE.read_text()))
        except Exception as e:  # noqa: BLE001
            _log("error", f"Failed to read channels.json: {e}")
    return set()


def _save_channels(channels: Set[str]) -> None:
    try:
        CHANNELS_FILE.write_text(json.dumps(sorted(channels), ensure_ascii=False, indent=2))
    except Exception as e:  # noqa: BLE001
        _log("error", f"Failed to write channels.json: {e}")


def _normalize_arg(raw: str) -> str:
    return raw.strip()

HELP_TEXT = (
    "<b>Команды:</b>\n"
    "/reg &lt;id|@username&gt; — добавить канал (бот = админ).\n"
    "/unreg &lt;id|@username&gt; — удалить канал.\n"
    "/list — список каналов.\n"
    "/post — мгновенный пост.\n"
    "/help — помощь.\n\n"
    "<b>Примечание:</b> ID может быть любым числом (часто отрицательный). Можно @username.\n"
)

# ─────────────────────────── Bulk ID ingest ───────────────────────────

async def _bulk_id_ingest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Private chat: пользователь может прислать список ID/username построчно.
    Пример сообщения:
        1\n2\n-1001234567890\n@mychannel
    Добавляем ВСЕ допустимые каналы. Для username делаем get_chat.
    Игнорируем пустые строки и дубликаты. Отчёт одной ответкой.
    """
    if update.effective_chat.type != "private":
        return
    msg = update.effective_message
    text = msg.text or ""


    if not any(ch.isdigit() for ch in text) and "@" not in text:
        return


    raw_items = [line.strip() for line in text.replace(',', '\n').splitlines()]
    raw_items = [r for r in raw_items if r]
    if not raw_items:
        return

    bot = context.bot
    added: List[str] = []
    skipped: List[str] = []
    channels = _load_channels()

    async def resolve_and_add(token: str) -> None:
        # Username
        if token.startswith('@') and len(token) > 1:
            try:
                chat_obj = await bot.get_chat(token)
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{token} (err: {e})")
                return
            if chat_obj.type != 'channel':
                skipped.append(f"{token} (not channel)")
                return
            real_id = str(chat_obj.id)
            if real_id in channels:
                skipped.append(f"{token} (dup)")
            else:
                channels.add(real_id)
                added.append(f"{token}→{real_id}")
            return
        # Numeric ID
        if token.lstrip('-').isdigit():
            if token in channels:
                skipped.append(f"{token} (dup)")
            else:
                channels.add(token)
                added.append(token)
            return
        skipped.append(f"{token} (format)")

    for item in raw_items[:500]:  # ограничение 500
        await resolve_and_add(item)

    _save_channels(channels)

    if not added and not skipped:
        return

    report_lines: List[str] = []
    if added:
        report_lines.append("✅ Добавлены: " + ", ".join(added[:50]) + (" …" if len(added) > 50 else ""))
    if skipped:
        report_lines.append("⚠️ Пропущены: " + ", ".join(skipped[:50]) + (" …" if len(skipped) > 50 else ""))
    report_lines.append(f"Всего в базе: {len(channels)}")
    await msg.reply_text("\n".join(report_lines))

# ─────────────────────────── Command handlers ──────────────────────────

async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_reg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    if not context.args:
        await update.message.reply_text("Использование: /reg <channel_id или @username>")
        return

    raw = _normalize_arg(context.args[0])
    bot = context.bot

    if raw.startswith("@"):
        candidate = raw
    elif raw.lstrip("-").isdigit():
        candidate = raw
    else:
        await update.message.reply_text("📛 Неверный формат. Пример: -1001234567890 или @mychannel")
        return

    try:
        chat_obj = await bot.get_chat(candidate)
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"❌ Не смог получить чат {candidate}: {e}")
        _log("error", f"get_chat failed for {candidate}: {e}")
        return

    if chat_obj.type != "channel":
        await update.message.reply_text("⚠️ Это не канал.")
        _log("warn", f"Rejected {candidate}: type={chat_obj.type}")
        return

    real_id = str(chat_obj.id)
    channels = _load_channels()
    if real_id in channels:
        await update.message.reply_text(f"ℹ️ Уже в списке: {chat_obj.title} ({real_id})")
        return

    channels.add(real_id)
    _save_channels(channels)
    await update.message.reply_text(f"✅ Добавлен: {chat_obj.title} ({real_id}). Всего: {len(channels)}")
    _log("info", f"Added channel {real_id} ({chat_obj.title}) via /reg")


async def cmd_unreg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    if not context.args:
        await update.message.reply_text("Использование: /unreg <channel_id или @username>")
        return

    raw = _normalize_arg(context.args[0])
    bot = context.bot

    if raw.startswith("@"):
        try:
            chat_obj = await bot.get_chat(raw)
            raw = str(chat_obj.id)
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"❌ Не смог получить чат: {e}")
            return

    if not raw.lstrip('-').isdigit():
        await update.message.reply_text("📛 ID должен быть числом или @username.")
        return

    channels = _load_channels()
    if raw in channels:
        channels.remove(raw)
        _save_channels(channels)
        await update.message.reply_text(f"❌ Удалён: {raw}. Осталось: {len(channels)}")
        _log("info", f"Removed channel {raw} via /unreg")
    else:
        await update.message.reply_text("⚠️ Этого канала нет в базе.")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    channels = sorted(_load_channels())
    if not channels:
        await update.message.reply_text("Пока нет ни одного канала.")
        return
    await update.message.reply_text(
        "📄 Зарегистрированные каналы (" + str(len(channels)) + ")\n" + "\n".join(channels)
    )


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    await _broadcast(context)
    await update.message.reply_text("📤 Пакет отправлен во все каналы.")

# ────────────────────────────── Broadcast ─────────────────────────────

async def _broadcast(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not IMAGE_PATH.exists():
        _log("warn", "image.jpg not found – broadcast skipped")
        return

    channels = _load_channels()
    if not channels:
        _log("info", "No channels registered; broadcast skipped")
        return

    n_texts = random.randint(*MESSAGES_PER_CYCLE_RANGE)
    texts = random.sample(TEXT_MESSAGES, k=min(n_texts, len(TEXT_MESSAGES)))
    photo_bytes = IMAGE_PATH.read_bytes()

    for chat_id in list(channels):
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=photo_bytes, caption=CAPTION)
            for text in texts:
                await context.bot.send_message(chat_id, text)
        except Exception as exc:  # noqa: BLE001
            _log("error", f"Failed to post to {chat_id}: {exc}")

    _log("info", f"Broadcast complete to {len(channels)} channels")

# ────────────────────────────── Startup hook ──────────────────────────

async def _post_init(app: Application) -> None:
    """Async hook executed by run_polling before polling starts."""
    app.job_queue.run_once(lambda ctx: ctx.application.create_task(_broadcast(ctx)), when=0)
    app.job_queue.run_repeating(_broadcast, interval=POST_EVERY_SECONDS, first=POST_EVERY_SECONDS)
    _log("info", "Scheduling done (immediate + daily)")

# ────────────────────────────── Main ───────────────────────────────────

def main() -> None:
    print(f"[startup] Running bot version {VERSION}")
    if not BOT_TOKEN:
        print("[fatal] Environment variable BOT_TOKEN is not set", file=sys.stderr)
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # Private commands
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("reg", cmd_reg))
    app.add_handler(CommandHandler("unreg", cmd_unreg))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CommandHandler("start", cmd_help))

    # Bulk plain-text ID ingest (ПОСЛЕ команд)
    from telegram.ext import MessageHandler, filters
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _bulk_id_ingest))

    print("[info] Bot is running… (Ctrl+C to stop)")
    app.run_polling(allowed_updates=["message", "edited_message"])


if __name__ == "__main__":
    main()
