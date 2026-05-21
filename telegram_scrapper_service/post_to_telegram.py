from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from telethon import TelegramClient

from telegram_scrapper_service.config import get_env, get_env_int, load_env_file
from telegram_scrapper_service.telegram_assets import load_items_from_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Post exported rental listings to a Telegram channel."
    )
    parser.add_argument(
        "--input",
        default=get_env("TELEGRAM_OUTPUT_JSON", "live_ethio_rental_listings.json"),
        help="Path to the exported JSON file.",
    )
    parser.add_argument(
        "--channel",
        default=get_env("TELEGRAM_TARGET_CHANNEL", "https://t.me/yenekiray_ethio"),
        help="Destination channel username or link.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Post at most this many new items.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore posted state and repost all items from the input file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Prepare the messages without sending anything.",
    )
    parser.add_argument(
        "--use-bot",
        action="store_true",
        help="Authenticate the Telethon client as a bot using TELEGRAM_BOT_TOKEN.",
    )
    parser.add_argument(
        "--session-name",
        default=get_env("TELEGRAM_SESSION_NAME", "live_ethio_scraper"),
        help="Telethon session name to use.",
    )
    parser.add_argument(
        "--state-file",
        default=get_env("TELEGRAM_POSTED_STATE_FILE", "live_ethio_exports/posted_ids.json"),
        help="File used to track already-posted listing IDs.",
    )
    return parser.parse_args()


class TelegramPoster:
    def __init__(self, client: TelegramClient, channel: str):
        self.client = client
        self.channel = channel

    async def send_message(self, text: str) -> None:
        await self.client.send_message(self.channel, text, parse_mode="html", link_preview=False)

    async def send_media(self, media_paths: list[Path], caption_html: str | None) -> None:
        existing_paths = [path for path in media_paths if path.exists()]
        if not existing_paths:
            await self.send_message(caption_html or "")
            return

        existing_paths = existing_paths[:10]
        if len(existing_paths) == 1:
            await self.client.send_file(
                self.channel,
                existing_paths[0],
                caption=caption_html,
                parse_mode="html",
            )
            return

        await self.client.send_file(
            self.channel,
            existing_paths,
            caption=caption_html,
            parse_mode="html",
        )


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data}


def save_state(path: Path, posted_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(posted_ids), ensure_ascii=False, indent=2), encoding="utf-8")


async def post_listings(
    input_path: Path,
    channel: str,
    *,
    limit: int | None,
    force: bool,
    dry_run: bool,
    use_bot: bool,
    session_name: str,
    state_file: Path,
) -> None:
    load_env_file()
    api_id = get_env_int("TELEGRAM_API_ID")
    api_hash = get_env("TELEGRAM_API_HASH")
    if api_id is None or not api_hash:
        raise ValueError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env.")
    if not channel:
        raise ValueError("Set TELEGRAM_TARGET_CHANNEL or pass --channel.")

    items = load_items_from_json(input_path)
    posted_ids: set[str] = set() if force else load_state(state_file)

    client = TelegramClient(session_name, api_id, api_hash)
    bot_token = get_env("TELEGRAM_BOT_TOKEN")
    if use_bot:
        if not bot_token:
            raise ValueError("Set TELEGRAM_BOT_TOKEN in .env when using --use-bot.")
        await client.start(bot_token=bot_token)
    else:
        await client.start()

    try:
        poster = TelegramPoster(client, channel)
        posted_count = 0
        for item in items:
            listing_id = str(item.get("listing_id") or "")
            if not listing_id or listing_id in posted_ids:
                continue

            send_html = item.get("telegram_send_html") or item.get("telegram_post_html")
            if not send_html:
                continue

            image_paths = [Path(path) for path in item.get("telegram_image_paths") or []]
            if dry_run:
                print(f"would_post listing_id={listing_id} images={min(10, len(image_paths))}")
            else:
                if image_paths:
                    await poster.send_media(image_paths, send_html)
                else:
                    await poster.send_message(send_html)
                posted_ids.add(listing_id)
                save_state(state_file, posted_ids)
                print(f"posted listing_id={listing_id}")
                await asyncio.sleep(2)

            posted_count += 1
            if limit is not None and posted_count >= limit:
                break
    finally:
        await client.disconnect()


def main() -> None:
    args = parse_args()
    asyncio.run(
        post_listings(
            Path(args.input),
            args.channel,
            limit=args.limit,
            force=args.force,
            dry_run=args.dry_run,
            use_bot=args.use_bot,
            session_name=args.session_name,
            state_file=Path(args.state_file),
        )
    )
