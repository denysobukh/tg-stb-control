#!/usr/bin/env python3

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncssh
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tg-stb-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]

BASE_WEBHOOK_URL = os.environ["BASE_WEBHOOK_URL"].rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/tg-stb-webhook")
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

ALLOWED_CHAT_IDS = {
    int(x.strip())
    for x in os.environ["ALLOWED_CHAT_IDS"].split(",")
    if x.strip()
}

MIKROTIK_HOST = os.environ["MIKROTIK_HOST"]
MIKROTIK_PORT = int(os.getenv("MIKROTIK_PORT", "22"))
MIKROTIK_USER = os.environ["MIKROTIK_USER"]
MIKROTIK_SSH_KEY = os.environ["MIKROTIK_SSH_KEY"]

SCHEDULER_NAME = os.getenv("SCHEDULER_NAME", "stb-timer-autooff")
ROUTEROS_DATE_FORMAT = os.getenv("ROUTEROS_DATE_FORMAT", "iso")
ROUTER_TIMEZONE = os.getenv("ROUTER_TIMEZONE", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes", "on"}

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()

COMMAND_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="/tvon"),
            KeyboardButton(text="/tvoff"),
            KeyboardButton(text="/status"),
        ],
        [
            KeyboardButton(text="/timer5"),
            KeyboardButton(text="/timer15"),
            KeyboardButton(text="/timer30"),
        ],
        [
            KeyboardButton(text="/timer45"),
            KeyboardButton(text="/timer60"),
            KeyboardButton(text="/timer90"),
        ],
        [
            KeyboardButton(text="/help"),
        ],
    ],
    resize_keyboard=True,
    input_field_placeholder="Choose a TV command",
)


def is_allowed(message: Message) -> bool:
    return message.chat is not None and message.chat.id in ALLOWED_CHAT_IDS


async def deny_if_needed(message: Message) -> bool:
    if is_allowed(message):
        return False

    chat_id = message.chat.id if message.chat else "unknown"
    log.warning("Denied Telegram chat_id=%s text=%r", chat_id, message.text)
    await message.answer("Access denied")
    return True


async def ros(command: str) -> str:
    """
    Execute one RouterOS command over SSH.

    Use a dedicated low-privilege MikroTik user.
    Avoid passing unvalidated user input into this function.
    """
    if DRY_RUN:
        log.warning("DRY_RUN: would execute RouterOS command: %s", command)
        return ""

    log.info("RouterOS command: %s", command)

    async with asyncssh.connect(
        MIKROTIK_HOST,
        port=MIKROTIK_PORT,
        username=MIKROTIK_USER,
        client_keys=[MIKROTIK_SSH_KEY],
        known_hosts=None,
    ) as conn:
        result = await conn.run(command, check=False)

    if result.exit_status != 0:
        log.error(
            "RouterOS command failed: exit=%s stdout=%r stderr=%r",
            result.exit_status,
            result.stdout,
            result.stderr,
        )
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "RouterOS command failed")

    return result.stdout.strip()


async def tv_on() -> None:
    await ros(f'/system scheduler remove [find name="{SCHEDULER_NAME}"]')
    await ros("/system script run stbon")


async def tv_off() -> None:
    await ros(f'/system scheduler remove [find name="{SCHEDULER_NAME}"]')
    await ros("/system script run stboff")


def routeros_date(dt: datetime) -> str:
    if ROUTEROS_DATE_FORMAT == "legacy":
        # RouterOS v6 style example: jul/04/2025
        return dt.strftime("%b/%d/%Y").lower()

    # RouterOS v7 style example: 2025-07-04
    return dt.strftime("%Y-%m-%d")


def router_now() -> datetime:
    if not ROUTER_TIMEZONE:
        return datetime.now().astimezone()

    try:
        return datetime.now(ZoneInfo(ROUTER_TIMEZONE))
    except ZoneInfoNotFoundError as e:
        raise RuntimeError(f"Invalid ROUTER_TIMEZONE: {ROUTER_TIMEZONE}") from e


def router_timezone_label() -> str:
    return ROUTER_TIMEZONE or datetime.now().astimezone().tzname() or "local time"


async def schedule_off_after(minutes: int) -> datetime:
    """
    Keep the actual auto-off on MikroTik.

    Reason: if the VPS/bot dies after /timer30, RouterOS still turns the TV off.
    """
    if not 1 <= minutes <= 24 * 60:
        raise ValueError("Timer must be from 1 to 1440 minutes")

    await ros(f'/system scheduler remove [find name="{SCHEDULER_NAME}"]')
    await ros("/system script run stbon")

    off_at = router_now() + timedelta(minutes=minutes)
    start_date = routeros_date(off_at)
    start_time = off_at.strftime("%H:%M:%S")

    # User input is validated as int. Scheduler name is controlled by env.
    on_event = (
        f'/system script run stboff; '
        f'/system scheduler remove [find name=\\"{SCHEDULER_NAME}\\"]'
    )

    await ros(
        f'/system scheduler add '
        f'name="{SCHEDULER_NAME}" '
        f'interval=0s disabled=no '
        f'start-date={start_date} '
        f'start-time={start_time} '
        f'on-event="{on_event}"'
    )

    return off_at


async def read_tv_status() -> str:
    """
    Current logic copied from your RouterOS script:
    firewall rules with comment=block-stb enabled => TV off;
    if any of them is disabled => TV on.
    """
    command = (
        ':local allEnabled true; '
        ':foreach rule in=[/ip firewall filter find comment="block-stb"] do={ '
        ':if ([/ip firewall filter get $rule disabled]) do={ :set allEnabled false } '
        '}; '
        ':if ($allEnabled) do={ :put "off" } else={ :put "on" }'
    )

    if DRY_RUN:
        await ros(command)
        return "dry-run"

    output = await ros(command)

    return output.strip().splitlines()[-1] if output else "unknown"


async def read_timer_status() -> str | None:
    command = (
        f':local sched [/system scheduler find name="{SCHEDULER_NAME}"]; '
        f':if ([:len $sched] > 0) do={{ '
        f':put ([/system scheduler get $sched start-date] . " " . [/system scheduler get $sched start-time]) '
        f'}}'
    )

    if DRY_RUN:
        await ros(command)
        return None

    output = await ros(command)

    output = output.strip()
    return output or None


@router.message(Command("start", "help"))
async def help_cmd(message: Message) -> None:
    if await deny_if_needed(message):
        return

    await message.answer(
        "Commands:\n"
        "/tvon - turn TV on\n"
        "/tvoff - turn TV off\n"
        "/status - show current status\n"
        "/timer5, /timer15, /timer30, /timer45, /timer60, /timer90 - turn TV on and schedule off later",
        reply_markup=COMMAND_KEYBOARD,
    )


@router.message(Command("tvon"))
async def tvon_cmd(message: Message) -> None:
    if await deny_if_needed(message):
        return

    try:
        await tv_on()
        await message.answer("TV is on; nothing scheduled", reply_markup=COMMAND_KEYBOARD)
    except Exception as e:
        log.exception("Failed to turn TV on")
        await message.answer(f"Failed to turn TV on: {e}", reply_markup=COMMAND_KEYBOARD)


@router.message(Command("tvoff"))
async def tvoff_cmd(message: Message) -> None:
    if await deny_if_needed(message):
        return

    try:
        await tv_off()
        await message.answer("TV is off; nothing scheduled", reply_markup=COMMAND_KEYBOARD)
    except Exception as e:
        log.exception("Failed to turn TV off")
        await message.answer(f"Failed to turn TV off: {e}", reply_markup=COMMAND_KEYBOARD)


@router.message(Command("status"))
async def status_cmd(message: Message) -> None:
    if await deny_if_needed(message):
        return

    try:
        status = await read_tv_status()
        timer = await read_timer_status()

        if timer:
            await message.answer(
                f"TV is {status}; scheduled to turn off at {timer}",
                reply_markup=COMMAND_KEYBOARD,
            )
        else:
            await message.answer(f"TV is {status}; nothing scheduled", reply_markup=COMMAND_KEYBOARD)
    except Exception as e:
        log.exception("Failed to read status")
        await message.answer(f"Failed to read status: {e}", reply_markup=COMMAND_KEYBOARD)


@router.message(F.text.regexp(r"^/timer\d+$"))
async def timer_cmd(message: Message) -> None:
    if await deny_if_needed(message):
        return

    text = message.text or ""
    minutes = int(text.removeprefix("/timer"))

    try:
        off_at = await schedule_off_after(minutes)
        await message.answer(
            f"TV is on for {minutes} minutes. "
            f"Scheduled to turn off at {off_at:%Y-%m-%d %H:%M:%S} {router_timezone_label()}",
            reply_markup=COMMAND_KEYBOARD,
        )
    except ValueError as e:
        await message.answer(str(e), reply_markup=COMMAND_KEYBOARD)
    except Exception as e:
        log.exception("Failed to set timer")
        await message.answer(f"Failed to set timer: {e}", reply_markup=COMMAND_KEYBOARD)


@router.message()
async def fallback(message: Message) -> None:
    if await deny_if_needed(message):
        return

    await message.answer(
        "Unknown command. Use /tvon, /tvoff, /status, /timer30, or /help",
        reply_markup=COMMAND_KEYBOARD,
    )


async def on_startup(bot: Bot) -> None:
    webhook_url = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(
        webhook_url,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
        allowed_updates=["message"],
    )
    log.info("Webhook set to %s", webhook_url)


async def on_shutdown(bot: Bot) -> None:
    await bot.session.close()


def create_app() -> web.Application:
    dp.include_router(router)
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="127.0.0.1", port=8080)
