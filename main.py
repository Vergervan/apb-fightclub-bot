#!/usr/bin/env python3
import asyncio
import os
import re
import urllib.parse
import urllib.request
from html import unescape
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

SITE_URL = "https://www.gamersfirst.com/apb/status"
DEFAULT_REGION = "EU"
ALERT_THRESHOLD = 3
CHECK_INTERVAL_SECONDS = 60
TARGET_DISTRICTS = {
    "EU": ["EU PGAsylum", "EU PGCrate"],
    "NA": ["US West PGAsylum", "US West PGCrate"],
}

ACTIVE_CHATS = set()
LAST_ALERT_STATE = {}
USER_REGIONS = {}


def get_telegram_token():
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if token:
        return token

    token_file = os.getenv("TELEGRAM_TOKEN_FILE", "").strip()
    if token_file:
        try:
            data = Path(token_file).read_text(encoding="utf-8").strip()
            if data:
                return data
        except OSError:
            pass

    raise RuntimeError("TELEGRAM_TOKEN is not set")


def sync_fetch_status_html(region: str = DEFAULT_REGION) -> str:
    local_file = Path(__file__).with_name("District Status - APB Reloaded - GamersFirst.html")
    if local_file.exists():
        return local_file.read_text(encoding="utf-8", errors="replace")

    params = {"region": region}
    url = f"{SITE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


async def fetch_status_html(region: str = DEFAULT_REGION) -> str:
    return await asyncio.to_thread(sync_fetch_status_html, region)


def extract_district_counts(html: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    row_pattern = re.compile(r'<div class="status-row status-district"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
    for row in row_pattern.finditer(html):
        block = row.group(1)
        name_html = re.search(r'<span class="d-name"[^>]*>(.*?)</span>', block, re.DOTALL)
        total_html = re.search(r'<span class="d-total">\s*(\d+)\s*</span>', block, re.DOTALL)
        if not name_html or not total_html:
            continue
        name = re.sub(r"<.*?>", "", name_html.group(1))
        name = unescape(name).strip()
        if name:
            counts[name] = int(total_html.group(1))
    return counts


def get_target_names(region: str) -> list[str]:
    normalized = (region or DEFAULT_REGION).upper()
    return TARGET_DISTRICTS.get(normalized, TARGET_DISTRICTS[DEFAULT_REGION])


def current_status_text(region: str, counts: dict[str, int]) -> str:
    return "\n".join(f"{district}: {counts.get(district, 0)}" for district in get_target_names(region))


def alert_state_for_region(region: str, counts: dict[str, int]) -> str:
    values = [counts.get(name, 0) for name in get_target_names(region)]
    if all(value > ALERT_THRESHOLD for value in values):
        return "high"
    if all(value < ALERT_THRESHOLD for value in values):
        return "low"
    return "stable"


async def send_message(bot: Bot, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
        return True
    except Exception as exc:
        print(f"Telegram send failed for {chat_id}: {exc}")
        return False


async def check_and_notify(bot: Bot, chat_id: int, region: str):
    try:
        html = await fetch_status_html(region)
        counts = extract_district_counts(html)
    except Exception as exc:
        await send_message(bot, chat_id, f"Не удалось получить статус: {exc}")
        return

    state = alert_state_for_region(region, counts)
    prev_state = LAST_ALERT_STATE.get(chat_id)

    if state == "high" and prev_state != "high":
        text = f"🔥 {region} alert: оба района выше {ALERT_THRESHOLD}!\n{current_status_text(region, counts)}"
        await send_message(bot, chat_id, text)
        LAST_ALERT_STATE[chat_id] = "high"
        return

    if state == "low" and prev_state != "low":
        text = f"📉 {region} alert: оба района меньше {ALERT_THRESHOLD}.\n{current_status_text(region, counts)}"
        await send_message(bot, chat_id, text)
        LAST_ALERT_STATE[chat_id] = "low"
        return

    if state == "stable":
        LAST_ALERT_STATE[chat_id] = prev_state if prev_state in {"high", "low"} else None


async def command_status(bot: Bot, chat_id: int, region: str):
    try:
        html = await fetch_status_html(region)
        counts = extract_district_counts(html)
    except Exception as exc:
        await send_message(bot, chat_id, f"Не удалось получить статус: {exc}")
        return

    await send_message(bot, chat_id, f"Status for {region}:\n{current_status_text(region, counts)}")


async def scheduler(bot: Bot):
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        for chat_id in list(ACTIVE_CHATS):
            region = USER_REGIONS.get(chat_id, DEFAULT_REGION)
            await check_and_notify(bot, chat_id, region)


async def start_command(message: types.Message):
    ACTIVE_CHATS.add(message.chat.id)
    LAST_ALERT_STATE.setdefault(message.chat.id, None)
    USER_REGIONS.setdefault(message.chat.id, DEFAULT_REGION)
    await message.answer(
        "Бот запущен. Слежение идёт за Europe по умолчанию.\n\n"
        "Команды:\n/start — включить таймер\n/stop — остановить\n/status — текущее число\n/region EU — выбрать Europe\n/region NA — выбрать North America"
    )


async def stop_command(message: types.Message):
    ACTIVE_CHATS.discard(message.chat.id)
    LAST_ALERT_STATE.pop(message.chat.id, None)
    await message.answer("Отслеживание остановлено.")


async def status_command(message: types.Message):
    region = USER_REGIONS.get(message.chat.id, DEFAULT_REGION)
    await command_status(message.bot, message.chat.id, region)


async def region_command(message: types.Message):
    args = message.text.split(maxsplit=1)
    region = (args[1].strip().upper() if len(args) > 1 else "EU")
    if region not in TARGET_DISTRICTS:
        await message.answer("Неизвестный регион. Используйте EU или NA.")
        return
    USER_REGIONS[message.chat.id] = region
    await message.answer(f"Регион установлен: {region}")


async def help_command(message: types.Message):
    await message.answer(
        "Команды:\n/start — запустить\n/stop — остановить\n/status — текущее состояние\n/region EU — Europe\n/region NA — North America"
    )


async def main():
    token = get_telegram_token()
    bot = Bot(token=token)
    dp = Dispatcher()

    dp.message(Command("start"))(start_command)
    dp.message(Command("stop"))(stop_command)
    dp.message(Command("status"))(status_command)
    dp.message(Command("region"))(region_command)
    dp.message(Command("help"))(help_command)

    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
