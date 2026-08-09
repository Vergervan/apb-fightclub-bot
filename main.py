#!/usr/bin/env python3
import argparse
import asyncio
import json
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
DEFAULT_CHECK_INTERVAL_SECONDS = 300
DEFAULT_DISTRICT_THRESHOLD = 4
TARGET_DISTRICTS = {
    "EU": ["EU PGAsylum", "EU PGCrate"],
    "NA": ["US West PGAsylum", "US West PGCrate"],
}
FRIENDLY_DISTRICT_NAMES = {
    "EU PGAsylum": "EU Asylum",
    "EU PGCrate": "EU Baylan",
    "US West PGAsylum": "NA Asylum",
    "US West PGCrate": "NA Baylan",
}
STATE_FILE = Path("/app/data/bot_state.json")

ACTIVE_CHATS = set()
LAST_ALERT_STATE = {}
USER_REGIONS = {}
LAST_DISTRICT_COUNTS = {}
USER_THRESHOLDS = {}


def load_state() -> dict[str, object]:
    if not STATE_FILE.exists():
        return {"active_chats": [], "user_regions": {}}

    try:
        data = STATE_FILE.read_text(encoding="utf-8")
        if not data.strip():
            return {"active_chats": [], "user_regions": {}}
        parsed = __import__("json").loads(data)
        if isinstance(parsed, dict):
            return parsed
    except (OSError, ValueError):
        pass

    return {"active_chats": [], "user_regions": {}}


def save_state() -> None:
    payload = {
        "active_chats": sorted(ACTIVE_CHATS),
        "user_regions": {str(chat_id): region for chat_id, region in USER_REGIONS.items()},
        "user_thresholds": {str(chat_id): value for chat_id, value in USER_THRESHOLDS.items()},
    }
    STATE_FILE.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def restore_state() -> None:
    state = load_state()
    active_chats = state.get("active_chats", [])
    user_regions = state.get("user_regions", {})
    user_thresholds = state.get("user_thresholds", {})

    ACTIVE_CHATS.clear()
    USER_REGIONS.clear()
    USER_THRESHOLDS.clear()
    for chat_id in active_chats:
        try:
            ACTIVE_CHATS.add(int(chat_id))
        except (TypeError, ValueError):
            continue

    for chat_id, region in user_regions.items():
        try:
            chat_id_int = int(chat_id)
        except (TypeError, ValueError):
            continue
        if region in TARGET_DISTRICTS:
            USER_REGIONS[chat_id_int] = region
        else:
            USER_REGIONS[chat_id_int] = DEFAULT_REGION

    for chat_id, value in user_thresholds.items():
        try:
            chat_id_int = int(chat_id)
            threshold = int(value)
        except (TypeError, ValueError):
            continue
        USER_THRESHOLDS[chat_id_int] = max(0, threshold)

    for chat_id in list(ACTIVE_CHATS):
        LAST_ALERT_STATE.setdefault(chat_id, None)
        USER_REGIONS.setdefault(chat_id, DEFAULT_REGION)
        USER_THRESHOLDS.setdefault(chat_id, DEFAULT_DISTRICT_THRESHOLD)


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


def sync_fetch_status_html() -> str:
    request = urllib.request.Request(
        SITE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


async def fetch_status_html(region: str = DEFAULT_REGION) -> str:
    del region
    return await asyncio.to_thread(sync_fetch_status_html)


def detect_active_region(html: str) -> str:
    patterns = [
        r'class="[^"]*status-tab[^"]*is-active[^"]*"[^>]*data-region="(EU|NA)"',
        r'class="[^"]*is-active[^"]*status-tab[^"]*"[^>]*data-region="(EU|NA)"',
        r'data-region="(EU|NA)"[^>]*aria-selected="true"',
        r'data-region="(EU|NA)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return DEFAULT_REGION


def extract_district_counts(html: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name_match, total_match in re.findall(
        r'<span class="d-name"[^>]*>(.*?)</span>.*?<span class="d-total">\s*(\d+)\s*</span>',
        html,
        re.DOTALL,
    ):
        name = unescape(re.sub(r"<.*?>", "", name_match)).strip()
        if not name:
            continue
        counts[name] = int(total_match)
    return counts


def get_target_names(region: str) -> list[str]:
    normalized = (region or DEFAULT_REGION).upper()
    return TARGET_DISTRICTS.get(normalized, TARGET_DISTRICTS[DEFAULT_REGION])


def current_status_text(region: str, counts: dict[str, int]) -> str:
    target_region = (region or DEFAULT_REGION).upper()
    if target_region not in TARGET_DISTRICTS:
        target_region = DEFAULT_REGION

    lines = []
    for district in get_target_names(target_region):
        display_name = FRIENDLY_DISTRICT_NAMES.get(district, district)
        lines.append(f"{display_name}: {counts.get(district, 0)}")
    return "\n".join(lines)


def pick_region(region: str, html: str) -> str:
    del html
    normalized = (region or DEFAULT_REGION).upper()
    if normalized in TARGET_DISTRICTS:
        return normalized
    return DEFAULT_REGION


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="APB Fight Club bot")
    parser.add_argument("-i", "--interval", type=int, default=DEFAULT_CHECK_INTERVAL_SECONDS, help="Polling interval in seconds")
    parser.add_argument("-t", "--threshold", type=int, default=DEFAULT_DISTRICT_THRESHOLD, help="Threshold value for district comparison")
    return parser.parse_args()


async def send_message(bot: Bot, chat_id: int, text: str) -> bool:
    try:
        await bot.send_message(chat_id, text, parse_mode="HTML")
        return True
    except Exception as exc:
        print(f"Telegram send failed for {chat_id}: {exc}")
        return False


async def check_and_notify(bot: Bot, chat_id: int, region: str, threshold: int = DEFAULT_DISTRICT_THRESHOLD):
    try:
        html = await fetch_status_html(region)
        counts = extract_district_counts(html)
        target_region = pick_region(region, html)
        target_names = get_target_names(target_region)
    except Exception as exc:
        await send_message(bot, chat_id, f"Failed to fetch status: {exc}")
        return

    previous = LAST_DISTRICT_COUNTS.get(chat_id, {})
    current = {district: counts.get(district, 0) for district in target_names}
    print(f"[auto-check] chat={chat_id} region={target_region} threshold={threshold} previous={previous} current={current}")

    changed_districts = []
    for district in target_names:
        prev_value = previous.get(district)
        curr_value = current.get(district, 0)

        if prev_value is None:
            if curr_value > threshold:
                changed_districts.append(district)
            continue

        if prev_value <= threshold < curr_value:
            changed_districts.append(district)
            continue

        if prev_value > threshold and curr_value <= threshold:
            changed_districts.append(district)

    print(f"[auto-check] chat={chat_id} target={target_region} changed={changed_districts}")

    LAST_DISTRICT_COUNTS[chat_id] = current

    if changed_districts:
        lines = [
            f"{FRIENDLY_DISTRICT_NAMES.get(district, district)}: {counts.get(district, 0)}"
            for district in changed_districts
        ]
        print(f"[auto-notify] chat={chat_id} region={target_region} sending={lines}")
        await send_message(bot, chat_id, "\n".join(lines))


async def command_status(bot: Bot, chat_id: int, region: str):
    try:
        html = await fetch_status_html(region)
        counts = extract_district_counts(html)
        region = pick_region(region, html)
    except Exception as exc:
        await send_message(bot, chat_id, f"Failed to fetch status: {exc}")
        return

    await send_message(bot, chat_id, current_status_text(region, counts))


async def scheduler(bot: Bot, check_interval: int, default_threshold: int):
    while True:
        for chat_id in list(ACTIVE_CHATS):
            region = USER_REGIONS.get(chat_id, DEFAULT_REGION)
            threshold = USER_THRESHOLDS.get(chat_id, default_threshold)
            await check_and_notify(bot, chat_id, region, threshold)

        if check_interval <= 0:
            break
        await asyncio.sleep(check_interval)


async def start_command(message: types.Message):
    ACTIVE_CHATS.add(message.chat.id)
    LAST_ALERT_STATE.setdefault(message.chat.id, None)
    USER_REGIONS.setdefault(message.chat.id, DEFAULT_REGION)
    USER_THRESHOLDS.setdefault(message.chat.id, DEFAULT_DISTRICT_THRESHOLD)
    LAST_DISTRICT_COUNTS.pop(message.chat.id, None)
    save_state()
    await message.answer(
        "Bot started. Monitoring Europe by default.\n\n"
        "Commands:\n/stop — stop monitoring\n/status — current values\n/region EU — select Europe\n/region NA — select North America\n/threshold [number] — set the threshold"
    )


async def stop_command(message: types.Message):
    ACTIVE_CHATS.discard(message.chat.id)
    LAST_ALERT_STATE.pop(message.chat.id, None)
    LAST_DISTRICT_COUNTS.pop(message.chat.id, None)
    save_state()
    await message.answer("Monitoring stopped.")


async def threshold_command(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        current = USER_THRESHOLDS.get(message.chat.id, DEFAULT_DISTRICT_THRESHOLD)
        await message.answer(f"Current threshold: {current}")
        return

    try:
        value = int(args[1].strip())
    except ValueError:
        await message.answer("Please send a number. Example: /threshold 4")
        return

    USER_THRESHOLDS[message.chat.id] = max(0, value)
    save_state()
    await message.answer(f"Threshold set to: {USER_THRESHOLDS[message.chat.id]}")


async def status_command(message: types.Message):
    region = USER_REGIONS.get(message.chat.id, DEFAULT_REGION)
    await command_status(message.bot, message.chat.id, region)


async def region_command(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        region = USER_REGIONS.get(message.chat.id, DEFAULT_REGION)
        await message.answer(f"Current region: {region}")
        return

    region = args[1].strip().upper()
    if region not in TARGET_DISTRICTS:
        await message.answer("Unknown region. Use EU or NA.")
        return

    USER_REGIONS[message.chat.id] = region
    LAST_DISTRICT_COUNTS.pop(message.chat.id, None)
    save_state()
    await message.answer(f"Region set to: {region}")


async def help_command(message: types.Message):
    await message.answer(
        "Commands:\n/start — start monitoring\n/stop — stop monitoring\n/status — current district status\n/region EU — Europe\n/region NA — North America\n/threshold 4 — set threshold"
    )


async def main():
    args = parse_args()
    restore_state()
    token = get_telegram_token()
    bot = Bot(token=token)
    dp = Dispatcher()

    dp.message(Command("start"))(start_command)
    dp.message(Command("stop"))(stop_command)
    dp.message(Command("status"))(status_command)
    dp.message(Command("region"))(region_command)
    dp.message(Command("threshold"))(threshold_command)
    dp.message(Command("help"))(help_command)

    asyncio.create_task(scheduler(bot, args.interval, args.threshold))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
