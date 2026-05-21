# LiveEthio Telegram Rental Scraper

This project uses `Telethon` to scrape rental-style posts from the `LiveEthio` Telegram group, extract structured fields, download post images, export JSON, and repost the result to `yenekiray_ethio`.

## Source and Destination

- Source group: `https://t.me/LiveEthio`
- Destination channel: `https://t.me/yenekiray_ethio`

## What It Does

- Scrapes Telegram message history from a group
- Filters and structures rental-related posts
- Extracts text, pricing, location, and images
- Exports structured JSON
- Generates Telegram-ready HTML assets
- Tracks posted listings in `posted_ids.json`
- Posts exported listings to your destination Telegram channel

## Project Files

- [`main.py`](./main.py): scraper entrypoint
- [`post_to_telegram.py`](./post_to_telegram.py): repost exported listings
- [`telegram_scrapper_service/scrape_group.py`](./telegram_scrapper_service/scrape_group.py): group scraping logic
- [`telegram_scrapper_service/post_to_telegram.py`](./telegram_scrapper_service/post_to_telegram.py): Telegram posting logic
- [`telegram_scrapper_service/telegram_assets.py`](./telegram_scrapper_service/telegram_assets.py): post formatting and asset export
- [`telegram_scrapper_service/rental_taxonomy.py`](./telegram_scrapper_service/rental_taxonomy.py): listing classification helpers

## Environment

Create a `.env` file with your credentials and defaults:

```env
TELEGRAM_SOURCE_GROUP=https://t.me/LiveEthio
TELEGRAM_TARGET_CHANNEL=https://t.me/yenekiray_ethio
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_SESSION_NAME=live_ethio_scraper
TELEGRAM_OUTPUT_JSON=live_ethio_rental_listings.json
TELEGRAM_OUTPUT_DIR=live_ethio_exports
TELEGRAM_POSTED_STATE_FILE=live_ethio_exports/posted_ids.json
TELEGRAM_SOURCE_HANDLE=@LiveEthio
DOWNLOAD_TELEGRAM_IMAGES=true
```

## Install

```bash
pip install -r requirements.txt
```

## Scrape Commands

Scrape the default group:

```bash
python main.py
```

Scrape a specific group:

```bash
python main.py --group https://t.me/LiveEthio
```

Start from a specific message id:

```bash
python main.py --start-id 12345
```

Limit how many rental posts are exported:

```bash
python main.py --limit 20
```

Skip image downloads:

```bash
python main.py --no-download-images
```

Custom output paths:

```bash
python main.py --output live_ethio_rental_listings.json --assets-dir live_ethio_exports
```

## Post Commands

Post exported listings to your destination Telegram channel:

```bash
python post_to_telegram.py --channel @yenekiray_ethio
```

Post only a few items:

```bash
python post_to_telegram.py --channel @yenekiray_ethio --limit 3
```

Preview without sending:

```bash
python post_to_telegram.py --channel @yenekiray_ethio --dry-run
```

Repost everything from the input file:

```bash
python post_to_telegram.py --channel @yenekiray_ethio --force
```

Use the bot token configured in `.env`:

```bash
python post_to_telegram.py --channel @yenekiray_ethio --use-bot
```

## Output

The scraper writes:

- Structured JSON listing data
- Per-post HTML files for Telegram formatting
- Downloaded post images

The exported records are stored under:

`live_ethio_exports/<listing_family>/<listing_folder>/<listing_id>/`

## Notes

- `--start-id` is useful when you want to resume history scraping from a known message id.
- `--limit` is useful when testing a small portion of the group history.
- The Telegram bot must be an admin in the destination channel if you post with `--use-bot`.
- The first run may create a Telethon session file for authentication.
