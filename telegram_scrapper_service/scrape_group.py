from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from telethon import TelegramClient

from telegram_scrapper_service.config import get_env, get_env_bool, get_env_int, load_env_file
from telegram_scrapper_service.rental_taxonomy import infer_listing_family, slugify
from telegram_scrapper_service.telegram_assets import export_telegram_assets, sanitize_filename


RENTAL_HINTS = (
    "rent",
    "rental",
    "for rent",
    "bedroom",
    "apartment",
    "house",
    "villa",
    "studio",
    "furnished",
    "unfurnished",
    "lease",
    "monthly",
    "birr",
    "etb",
    "price",
    "ኪራይ",
    "የሚከራይ",
    "የሚከራዩ",
    "ለኪራይ",
    "ለማከራየት",
    "ማከራየት",
    "ቤት",
    "ኮንዶሚንየም",
    "አፓርታማ",
    "ክፍል",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape rental posts from the LiveEthio Telegram group and export structured JSON."
    )
    parser.add_argument(
        "--group",
        "--channel",
        dest="group",
        default=get_env("TELEGRAM_SOURCE_GROUP", get_env("TELEGRAM_CHANNEL", "https://t.me/LiveEthio")),
        help="Telegram group username or public link to scrape.",
    )
    parser.add_argument(
        "--start-id",
        type=int,
        default=get_env_int("TELEGRAM_START_ID", None),
        help="Start from messages older than this message id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=get_env_int("TELEGRAM_LIMIT", None),
        help="Stop after exporting this many rental posts.",
    )
    parser.add_argument(
        "--output",
        default=get_env("TELEGRAM_OUTPUT_JSON", "live_ethio_rental_listings.json"),
        help="Path to the structured JSON output.",
    )
    parser.add_argument(
        "--assets-dir",
        default=get_env("TELEGRAM_OUTPUT_DIR", "live_ethio_exports"),
        help="Directory where post HTML and downloaded images are stored.",
    )
    parser.add_argument(
        "--session-name",
        default=get_env("TELEGRAM_SESSION_NAME", "live_ethio_scraper"),
        help="Telethon session name to use.",
    )
    parser.add_argument(
        "--source-handle",
        default=get_env("TELEGRAM_SOURCE_HANDLE", "@LiveEthio"),
        help="Handle shown in exported post templates, for example @LiveEthio.",
    )
    parser.add_argument(
        "--no-download-images",
        action="store_true",
        help="Skip downloading Telegram media to local files.",
    )
    return parser.parse_args()


async def scrape_group(
    group_ref: str,
    *,
    start_id: int | None,
    limit: int | None,
    output_json: Path,
    assets_dir: Path,
    session_name: str,
    source_handle: str | None,
    download_images: bool,
) -> list[dict[str, Any]]:
    load_env_file()
    api_id = get_env_int("TELEGRAM_API_ID")
    api_hash = get_env("TELEGRAM_API_HASH")
    if api_id is None or not api_hash:
        raise ValueError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env.")

    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()
    try:
        group = await client.get_entity(group_ref)
        source_handle = source_handle or _build_source_handle(group_ref, group)
        assets_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        async for bundle in iter_rental_bundles(
            client,
            group,
            start_id=start_id,
            limit=limit,
            download_images=download_images,
            assets_dir=assets_dir,
            source_handle=source_handle,
            group_ref=group_ref,
        ):
            records.append(bundle)

        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        return records
    finally:
        await client.disconnect()


async def iter_rental_bundles(
    client: TelegramClient,
    group: Any,
    *,
    start_id: int | None,
    limit: int | None,
    download_images: bool,
    assets_dir: Path,
    source_handle: str,
    group_ref: str,
) -> AsyncIterator[dict[str, Any]]:
    current_group_id: int | None = None
    current_bundle: list[Any] = []
    exported_count = 0

    async for message in client.iter_messages(group, offset_id=start_id or 0):
        group_id = getattr(message, "grouped_id", None)
        if current_bundle and group_id != current_group_id:
            item = await build_listing_item(
                client,
                group,
                current_bundle,
                assets_dir=assets_dir,
                source_handle=source_handle,
                group_ref=group_ref,
                download_images=download_images,
            )
            current_bundle = []
            current_group_id = None
            if item is not None:
                yield item
                exported_count += 1
                if limit is not None and exported_count >= limit:
                    return

        current_bundle.append(message)
        current_group_id = group_id

        if group_id is None:
            item = await build_listing_item(
                client,
                group,
                current_bundle,
                assets_dir=assets_dir,
                source_handle=source_handle,
                group_ref=group_ref,
                download_images=download_images,
            )
            current_bundle = []
            current_group_id = None
            if item is not None:
                yield item
                exported_count += 1
                if limit is not None and exported_count >= limit:
                    return

    if current_bundle:
        item = await build_listing_item(
            client,
            group,
            current_bundle,
            assets_dir=assets_dir,
            source_handle=source_handle,
            group_ref=group_ref,
            download_images=download_images,
        )
        if item is not None:
            yield item


async def build_listing_item(
    client: TelegramClient,
    group: Any,
    bundle: list[Any],
    *,
    assets_dir: Path,
    source_handle: str,
    group_ref: str,
    download_images: bool,
) -> dict[str, Any] | None:
    if not bundle:
        return None

    sorted_bundle = sorted(bundle, key=lambda message: message.id)
    text = combine_bundle_text(sorted_bundle)
    if not text:
        return None

    parsed = parse_rental_message(text)
    if not parsed["is_rental"]:
        return None

    first_message = sorted_bundle[0]
    source_link = build_message_link(group_ref, first_message.id, group)
    listing_id = f"{_group_slug(group_ref, group)}_{first_message.id}"
    record_dir = (
        assets_dir
        / slugify(parsed["listing_family"] or "general")
        / slugify(parsed["listing_folder"] or "general")
        / sanitize_filename(listing_id)
    )
    image_dir = record_dir
    image_paths: list[str] = []
    if download_images:
        image_paths = await download_bundle_media(client, sorted_bundle, image_dir)

    item: dict[str, Any] = {
        "listing_id": listing_id,
        "listing_url": source_link,
        "source_search_url": normalize_group_ref(group_ref),
        "title": parsed["title"],
        "category": "rental",
        "listing_family": parsed["listing_family"],
        "listing_type": parsed["listing_type"],
        "listing_type_label": parsed["listing_type_label"],
        "listing_folder": parsed["listing_folder"],
        "property_type": parsed["property_type"],
        "city": parsed["city"],
        "district": parsed["district"],
        "posted_time": first_message.date.isoformat() if getattr(first_message, "date", None) else None,
        "location_text": parsed["location_text"],
        "currency": parsed["currency"],
        "price_text": parsed["price_text"],
        "price_etb": parsed["price_etb"],
        "price_period": parsed["price_period"],
        "price_type": parsed["price_type"],
        "description": text,
        "image_urls": [],
        "bedrooms": parsed["bedrooms"],
        "bathrooms": parsed["bathrooms"],
        "property_address": parsed["property_address"],
        "estate_name": parsed["estate_name"],
        "property_size_sqm": parsed["property_size_sqm"],
        "condition": parsed["condition"],
        "furnishing": parsed["furnishing"],
        "toilets": parsed["toilets"],
        "minimum_rental_period": parsed["minimum_rental_period"],
        "attributes": parsed["attributes"],
        "telegram_image_paths": image_paths,
        "telegram_asset_dir": str(record_dir),
        "source_message_ids": [message.id for message in sorted_bundle],
        "source_handle": source_handle,
    }

    return export_telegram_assets(item, assets_dir, source_handle)


async def download_bundle_media(client: TelegramClient, bundle: list[Any], image_dir: Path) -> list[str]:
    image_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for message in bundle:
        if not _message_has_image(message):
            continue
        target_path = image_dir / _guess_media_filename(message, len(paths) + 1)
        try:
            downloaded = await client.download_media(message, file=target_path)
        except Exception:
            continue
        if downloaded:
            paths.append(str(Path(downloaded)))
    return paths


def parse_rental_message(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = extract_title(lines, text)
    attributes = extract_attributes(lines)
    price_info = extract_price(text)
    listing_code = extract_listing_code(text, attributes)
    location_text = extract_location(lines, text)
    bedrooms = _pop_attribute(attributes, "bedrooms", "beds", "bed room", "bedroom")
    bathrooms = _pop_attribute(attributes, "bathrooms", "baths", "bathroom")
    toilets = _pop_attribute(attributes, "toilets", "toilet")
    listing_code = listing_code or _pop_attribute(attributes, "listing_code", "code", "listing code")
    property_size = _pop_attribute(attributes, "property_size_sqm", "size", "sqm", "m2", "square meter", "area")
    furnishing = _pop_attribute(attributes, "furnishing", "furnished", "semi furnished", "unfurnished")
    condition = _pop_attribute(attributes, "condition")
    property_address = _pop_attribute(attributes, "property_address", "address", "location")
    minimum_rental_period = _pop_attribute(attributes, "minimum_rental_period", "min rent", "rent period")

    family = infer_listing_family(
        title=title,
        description=text,
        category="rental",
        attributes=attributes,
    )

    price_display = price_info["price_display"] or _extract_price_from_attributes(attributes)
    property_type = title or family["listing_type_label"]
    city, district = split_location(location_text)

    return {
        "is_rental": any(keyword in text.lower() for keyword in RENTAL_HINTS) or family["listing_family"] != "unknown",
        "title": title,
        "listing_family": family["listing_family"],
        "listing_type": family["listing_type"],
        "listing_type_label": family["listing_type_label"],
        "listing_folder": family["listing_folder"],
        "listing_code": listing_code,
        "property_type": property_type,
        "city": city,
        "district": district,
        "location_text": location_text,
        "currency": price_info["currency"],
        "price_text": price_info["price_text"],
        "price_display": price_display,
        "price_etb": price_info["price_etb"],
        "price_period": price_info["price_period"],
        "price_type": price_info["price_type"],
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "toilets": toilets,
        "property_size_sqm": property_size,
        "furnishing": furnishing,
        "condition": condition,
        "property_address": property_address,
        "estate_name": None,
        "minimum_rental_period": minimum_rental_period,
        "attributes": attributes,
    }


def extract_title(lines: list[str], fallback_text: str) -> str:
    for line in lines:
        cleaned = strip_markers(normalize_line(line))
        if not cleaned:
            continue
        if extract_price(cleaned)["price_text"]:
            continue
        if len(cleaned) <= 4:
            continue
        if looks_like_label_line(cleaned):
            continue
        return cleaned
    return strip_markers(first_line(fallback_text) or "Rental listing")


def extract_price(text: str) -> dict[str, Any]:
    patterns = [
        r"(?P<label>ኪራይ)\s*[:፡]?\s*(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<currency>ብር|ETB|Birr|Br|birr|etb|USD|US\$)?",
        r"(?P<currency>ETB|Birr|Br|birr|etb|ብር|USD|US\$)\s*[:\-]?\s*(?P<amount>[\d,]+(?:\.\d+)?)",
        r"(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<currency>ETB|Birr|Br|birr|etb|ብር|USD|US\$)",
    ]
    price_text = None
    amount = None
    currency = None
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount = match.group("amount")
            currency = match.group("currency")
            price_text = match.group(0)
            break

    if not price_text:
        for line in text.splitlines():
            normalized = normalize_line(line)
            lowered = normalized.lower()
            if "price" not in lowered and "ኪራይ" not in lowered:
                continue
            if ":" not in normalized and "፡" not in normalized and "-" not in normalized and "=" not in normalized:
                continue
            key, value = split_label_value(normalized, return_key=True)
            if not key or not value:
                continue
            if "price" in key.lower() or "ኪራይ" in key.lower():
                amount_match = re.search(r"[\d,]+(?:\.\d+)?", value)
                if amount_match:
                    amount = amount_match.group(0)
                    currency = _detect_currency_from_text(key) or _detect_currency_from_text(value)
                    price_text = f"{strip_markers(key)}: {value}"
                    break

    price_period = None
    lowered = text.lower()
    if any(marker in lowered for marker in ("per month", "monthly", "month")):
        price_period = "per month"
    elif any(marker in lowered for marker in ("per day", "daily", "day")):
        price_period = "per day"
    elif any(marker in lowered for marker in ("per year", "yearly", "annual", "annually")):
        price_period = "per year"

    price_type = None
    if any(marker in lowered for marker in ("negotiable", "nego", "slightly negotiable")):
        price_type = "negotiable"
    elif any(marker in lowered for marker in ("fixed", "non negotiable", "firm")):
        price_type = "fixed"

    price_etb = None
    if amount:
        try:
            price_etb = int(float(amount.replace(",", "")))
        except ValueError:
            price_etb = None

    return {
        "price_text": price_text,
        "price_etb": price_etb,
        "currency": normalize_currency(currency),
        "price_period": price_period,
        "price_type": price_type,
        "price_display": _build_price_display(price_text, amount, currency),
    }


def extract_listing_code(text: str, attributes: dict[str, str]) -> str | None:
    existing = _pop_attribute(attributes, "listing_code", "code", "listing code")
    if existing:
        return existing

    patterns = [
        r"\bCODE\s+(?P<code>[A-Z]{1,5}\s*\d{1,6})\b",
        r"\bCODE[:፡]?\s*(?P<code>[A-Z]{1,5}\s*\d{1,6})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return normalize_line(match.group("code")).upper()
    return None


def extract_location(lines: list[str], text: str) -> str | None:
    location_labels = (
        "location",
        "located at",
        "address",
        "area",
        "district",
        "site",
        "place",
        "around",
        "ሰፈር",
        "ሰፈሩ",
        "አካባቢ",
        "ቦታ",
        "የሚገኘው",
    )
    for line in lines:
        lowered = line.lower()
        if any(label in lowered for label in location_labels):
            value = split_label_value(line)
            if value:
                return value

    candidate_lines = [
        line
        for line in lines
        if any(
            marker in line.lower()
            for marker in (
                "bole",
                "megenagna",
                "ayat",
                "kera",
                "piassa",
                "lideta",
                "arada",
                "addis",
                "hawassa",
                "mekelle",
                "adama",
                "ሜክሲኮ",
                "መገናኛ",
                "አያት",
                "ቦሌ",
                "ለገሀር",
            )
        )
    ]
    if candidate_lines:
        return candidate_lines[0]
    return None


def extract_attributes(lines: list[str]) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for line in lines:
        if ":" not in line and "-" not in line and "=" not in line:
            continue
        key, value = split_label_value(line, return_key=True)
        if not key or not value:
            continue
        normalized_key = normalize_attribute_key(key)
        if not normalized_key:
            continue
        attributes.setdefault(normalized_key, value)
    return attributes


def split_location(location_text: str | None) -> tuple[str | None, str | None]:
    if not location_text:
        return None, None
    parts = [part.strip() for part in re.split(r"[,/|>-]", location_text) if part.strip()]
    if not parts:
        return location_text, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def combine_bundle_text(bundle: list[Any]) -> str:
    parts: list[str] = []
    for message in bundle:
        text = getattr(message, "raw_text", None) or getattr(message, "message", None)
        if text:
            parts.append(str(text).strip())
    return "\n".join(part for part in parts if part)


def build_message_link(group_ref: str, message_id: int, group: Any) -> str:
    username = getattr(group, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"

    parsed = urlparse(normalize_group_ref(group_ref))
    if parsed.path.strip("/"):
        return f"https://t.me/{parsed.path.strip('/')}/{message_id}"

    group_id = str(getattr(group, "id", ""))
    if group_id:
        return f"https://t.me/c/{group_id}/{message_id}"
    return normalize_group_ref(group_ref)


def normalize_group_ref(group_ref: str) -> str:
    ref = group_ref.strip()
    if ref.startswith("@"):
        return ref
    if ref.startswith("https://t.me/") or ref.startswith("http://t.me/"):
        return ref.rstrip("/")
    return f"@{ref}"


def _build_source_handle(group_ref: str, group: Any) -> str:
    username = getattr(group, "username", None)
    if username:
        return f"@{username}"
    normalized = normalize_group_ref(group_ref)
    return normalized if normalized.startswith("@") else str(normalized)


def _group_slug(group_ref: str, group: Any) -> str:
    username = getattr(group, "username", None)
    if username:
        return slugify(username)
    parsed = urlparse(normalize_group_ref(group_ref))
    if parsed.path.strip("/"):
        return slugify(parsed.path.strip("/").split("/")[-1])
    return slugify(str(getattr(group, "id", "telegram_group")))


def _message_has_image(message: Any) -> bool:
    if getattr(message, "photo", None):
        return True
    document = getattr(message, "document", None)
    mime_type = getattr(document, "mime_type", "") if document else ""
    return bool(mime_type and str(mime_type).startswith("image/"))


def _guess_media_filename(message: Any, index: int) -> str:
    document = getattr(message, "document", None)
    suffix = ".jpg"
    if document and getattr(document, "mime_type", None):
        mime_type = str(document.mime_type).lower()
        if "png" in mime_type:
            suffix = ".png"
        elif "webp" in mime_type:
            suffix = ".webp"
        elif "jpeg" in mime_type or "jpg" in mime_type:
            suffix = ".jpg"
    return f"image_{index:02d}{suffix}"


def normalize_line(value: str) -> str:
    return " ".join(value.split()).strip()


def first_line(text: str) -> str | None:
    for line in text.splitlines():
        cleaned = normalize_line(line)
        if cleaned:
            return cleaned
    return None


def looks_like_label_line(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("contact", "call", "price", "location", "address", "bedroom", "bathroom"))


def split_label_value(line: str, return_key: bool = False) -> tuple[str | None, str | None] | str | None:
    for separator in (":", "፡", "-", "="):
        if separator in line:
            key, value = line.split(separator, 1)
            key = normalize_line(key)
            value = normalize_line(value)
            if return_key:
                return key or None, value or None
            return value or None
    return (None, None) if return_key else None


def normalize_attribute_key(key: str) -> str | None:
    normalized = normalize_line(key).lower()
    replacements = {
        "code": "listing_code",
        "code bt": "listing_code",
        "listing code": "listing_code",
        "መኝታ ክፍል": "bedrooms",
        "bed room": "bedrooms",
        "bedrooms": "bedrooms",
        "bedroom": "bedrooms",
        "beds": "bedrooms",
        "ሻወር ቤት": "bathrooms",
        "bath room": "bathrooms",
        "bathrooms": "bathrooms",
        "bathroom": "bathrooms",
        "baths": "bathrooms",
        "ሽንት ቤት": "toilets",
        "toilet": "toilets",
        "toilets": "toilets",
        "ሰፈሩ": "location_text",
        "ሰፈር": "location_text",
        "ቦታ": "location_text",
        "አካባቢ": "location_text",
        "የሚገኘው": "location_text",
        "መኪና ማቆሚያ": "parking",
        "parking": "parking",
        "ዉሃ": "water",
        "water": "water",
        "ታንከር": "tanker",
        "መብራት": "electricity",
        "ማብሰያ ክፍል": "kitchen",
        "kitchen": "kitchen",
        "ትምህርት ቤት": "schools",
        "size": "property_size_sqm",
        "sqm": "property_size_sqm",
        "m2": "property_size_sqm",
        "square meter": "property_size_sqm",
        "ካሬ": "property_size_sqm",
        "furnished": "furnishing",
        "furnishing": "furnishing",
        "ሙሉ የተሟላ": "furnishing",
        "condition": "condition",
        "address": "property_address",
        "location": "location_text",
        "minimum rental period": "minimum_rental_period",
        "rent period": "minimum_rental_period",
        "ፎቅ": "floor",
        "floor": "floor",
    }
    return replacements.get(normalized, normalized or None)


def _pop_attribute(attributes: dict[str, str], *names: str) -> str | None:
    for name in names:
        if name in attributes:
            return attributes.pop(name)
    return None


def normalize_currency(currency: str | None) -> str | None:
    if not currency:
        return None
    lowered = currency.lower()
    if lowered in {"etb", "birr", "br", "ብር"}:
        return "ETB"
    if lowered in {"usd", "us$", "$"}:
        return "USD"
    return currency.upper()


def _detect_currency_from_text(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    if "usd" in lowered or "$" in text:
        return "USD"
    if any(marker in lowered for marker in ("etb", "birr", "br", "ብር")):
        return "ETB"
    return None


def _build_price_display(price_text: str | None, amount: str | None, currency: str | None) -> str | None:
    if price_text:
        return price_text
    if amount:
        normalized_currency = normalize_currency(currency)
        if normalized_currency:
            return f"{normalized_currency} {amount}"
        return amount
    return None


def _extract_price_from_attributes(attributes: dict[str, str]) -> str | None:
    for key, value in attributes.items():
        key_text = str(key).lower()
        value_text = normalize_line(str(value))
        if not value_text:
            continue
        if "price" not in key_text and "rent" not in key_text and "ኪራይ" not in key_text:
            continue
        amount_match = re.search(r"[\d,]+(?:\.\d+)?", value_text)
        if amount_match:
            currency = _detect_currency_from_text(key_text) or _detect_currency_from_text(value_text)
            prefix = str(key).replace("_", " ").strip().title()
            if currency:
                return f"{prefix}: {currency} {amount_match.group(0)}"
            return f"{prefix}: {amount_match.group(0)}"
        return f"{str(key).replace('_', ' ').strip().title()}: {value_text}"
    return None


def strip_markers(value: str) -> str:
    cleaned = value.strip()
    cleaned = re.sub(r"^[^\w\u1200-\u137f]+", "", cleaned)
    cleaned = re.sub(r"[^\w\u1200-\u137f]+$", "", cleaned)
    return normalize_line(cleaned)


def main() -> None:
    args = parse_args()
    output_json = Path(args.output)
    assets_dir = Path(args.assets_dir)
    records = asyncio.run(
        scrape_group(
            args.group,
            start_id=args.start_id,
            limit=args.limit,
            output_json=output_json,
            assets_dir=assets_dir,
            session_name=args.session_name,
            source_handle=args.source_handle,
            download_images=not args.no_download_images and get_env_bool("DOWNLOAD_TELEGRAM_IMAGES", True),
        )
    )
    print(f"exported {len(records)} rental posts to {output_json}")
