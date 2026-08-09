# APB Fight Club Bot

A Telegram monitoring bot for APB Reloaded that tracks live district activity and alerts users when key Fight Club districts cross a configured threshold.

## Overview

This project is built for players who want to monitor high-value districts such as Asylum and Baylan without constantly refreshing the game status page manually. The bot polls the APB status page, extracts relevant district values, compares them against the previous snapshot, and sends Telegram notifications when important changes occur.

It is designed for quick operational use: a user can enable monitoring in a chat, select a region, set the sensitivity threshold, and receive alerts when the district population crosses their chosen level.

## Features

- monitor both `EU` and `NA` region status pages;
- detect and track target districts:
  - `EU PGAsylum`, `EU PGCrate`
  - `US West PGAsylum`, `US West PGCrate`
- poll the public status page on a configurable interval;
- compare current values with the previous state to detect meaningful changes;
- notify chat members when values cross the configured threshold;
- support per-chat settings and persistent state across restarts;
- run as a containerized service with Docker Compose.

## How it works

1. The bot fetches the APB status page from GamersFirst.
2. The HTML is parsed for district names and current online counts.
3. The script filters the relevant Fight Club districts for the selected region.
4. It compares the current values to the last known snapshot.
5. If the value crosses the threshold, a Telegram alert is sent.
6. The state is stored so the bot can restore chat configuration after restarts.

This lets the bot act as a lightweight status watcher for APB district activity while keeping the implementation simple and easy to run in a container or local environment.

## Bot commands

- `/start` — start monitoring for the chat
- `/stop` — stop monitoring
- `/status` — display the current region values
- `/region EU` — select Europe
- `/region NA` — select North America
- `/threshold 4` — set the alert threshold
- `/help` — display a list of available commands

## Default configuration

- region: `EU`
- polling interval: `60` seconds
- threshold: `4`

## Environment configuration

The bot expects a Telegram bot token in the `TELEGRAM_TOKEN` environment variable.

A file-based alternative is also supported:

- `TELEGRAM_TOKEN_FILE` — path to a file containing the bot token

## Docker deployment

1. Generate a token file:

```bash
echo "YOUR_TELEGRAM_BOT_TOKEN" > telegram_token.txt
```

2. Build and start the container:

```bash
docker compose up -d --build
```

3. Stop the service:

```bash
docker compose down
```

## Project structure

```text
.
├── main.py
├── main_local.py
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── telegram_token.txt
├── README.md
└── data/
```

## Dependencies

- Python 3
- `aiogram==3.13.0`

## Use case

This project is useful for players who want a passive, real-time indicator for district activity without constantly opening the APB status page. It is especially helpful for tracking high-interest PvP or Fight Club locations where district population changes matter for timing and activity planning.

## Notes

The bot is intentionally lightweight and focused on monitoring and notification. It is designed to be simple to deploy, easy to extend, and reliable for ongoing tracking in Telegram chats.
