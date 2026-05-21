from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any

from telegram_scrapper_service.rental_taxonomy import infer_listing_family, slugify


def load_items_from_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    raise ValueError(f"Unsupported JSON structure in {path}")


def export_telegram_assets(
    item: dict[str, Any],
    output_dir: Path,
    source_handle: str,
) -> dict[str, Any]:
    listing_id = str(item.get("listing_id") or "unknown_listing")
    metadata = resolve_listing_metadata(item)
    item.setdefault("listing_family", metadata["listing_family"])
    item.setdefault("listing_type", metadata["listing_type"])
    item.setdefault("listing_type_label", metadata["listing_type_label"])
    item.setdefault("listing_folder", metadata["listing_folder"])

    family_slug = slugify(str(item.get("listing_family") or "general"))
    subtype_slug = slugify(str(item.get("listing_folder") or "general"))
    record_dir = output_dir / family_slug / subtype_slug / sanitize_filename(listing_id)
    record_dir.mkdir(parents=True, exist_ok=True)

    post_html = build_telegram_post_html(item, source_handle, include_id=True)
    send_html = build_send_html(item, source_handle)
    post_path = record_dir / "telegram_post.html"
    send_path = record_dir / "telegram_send.html"
    post_path.write_text(post_html, encoding="utf-8")
    send_path.write_text(send_html, encoding="utf-8")

    image_paths = [str(Path(path)) for path in item.get("telegram_image_paths") or []]

    item["telegram_post_html"] = post_html
    item["telegram_post_path"] = str(post_path)
    item["telegram_send_html"] = send_html
    item["telegram_send_path"] = str(send_path)
    item["telegram_image_paths"] = image_paths
    item["telegram_asset_dir"] = str(record_dir)
    return item


def build_send_html(item: dict[str, Any], source_handle: str) -> str:
    for max_lines in [5, 4, 3, 2, 1, 0]:
        send_html = build_telegram_post_html(
            item,
            source_handle,
            include_id=False,
            max_description_lines=max_lines,
            link_as_anchor=True,
        )
        if len(send_html) <= 1024:
            return send_html
    return build_telegram_post_html(
        item,
        source_handle,
        include_id=False,
        max_description_lines=0,
        link_as_anchor=True,
    )[:1024]


def build_telegram_post_html(
    item: dict[str, Any],
    source_handle: str,
    include_id: bool = True,
    max_description_lines: int | None = None,
    link_as_anchor: bool = False,
) -> str:
    metadata = resolve_listing_metadata(item)
    listing_label = escape_html(
        metadata["listing_type_label"] or item.get("property_type") or "Rental Listing"
    )
    location_line = format_location_line(item)
    price_line = format_price_line(item)
    description_lines = format_description_lines(
        item.get("description"),
        max_lines=max_description_lines,
    )
    listing_url = str(item.get("listing_url") or "N/A")
    listing_link_line = format_listing_link_line(listing_url, link_as_anchor=link_as_anchor)
    detail_lines, used_attribute_keys = format_detail_lines(item)
    extra_lines = format_attribute_summary_lines(item.get("attributes") or {}, used_attribute_keys)

    lines: list[str] = []
    if include_id:
        listing_id = escape_html(str(item.get("listing_id") or "unknown_listing"))
        lines.extend([f"<b>ID:</b> <code>{listing_id}</code>", ""])

    lines.extend(
        [
            f"<b>🏢 {listing_label}</b>",
            location_line,
            "",
            *([f"▫️CODE {escape_html(str(item['listing_code']))}"] if item.get("listing_code") else []),
            price_line,
            "",
            *detail_lines,
            *extra_lines,
            "",
            "✨ <b>ዝርዝር:</b>",
            *description_lines,
            "",
            "🔗 <b>ዝርዝር ሊንክ:</b>",
            listing_link_line,
            "",
            f"From: <b>{escape_html(source_handle)}</b>",
        ]
    )
    return "\n".join(lines)


def format_location_line(item: dict[str, Any]) -> str:
    parts = [part for part in [item.get("city"), item.get("district")] if part]
    location = ", ".join(escape_html(str(part)) for part in parts) or "N/A"
    property_address = item.get("property_address")
    if property_address:
        location = f"{location} ({escape_html(str(property_address))})"
    return f"📍 <b>{location}</b>"


def format_price_line(item: dict[str, Any]) -> str:
    price = escape_html(item.get("price_text") or "N/A")
    if item.get("price_etb") not in (None, ""):
        price = f"{price} (<code>{escape_html(str(item['price_etb']))}</code> ETB)"
    return f"💰 <b>ዋጋ:</b> {price}"


def format_description_lines(description: Any, max_lines: int | None = None) -> list[str]:
    if description in (None, ""):
        return ["N/A"]
    lines = [escape_html(line.strip()) for line in str(description).splitlines() if line.strip()]
    if max_lines is not None:
        lines = lines[:max_lines]
    return lines or ["N/A"]


def format_listing_link_line(listing_url: str, link_as_anchor: bool = False) -> str:
    if not listing_url or listing_url == "N/A":
        return "N/A"
    if link_as_anchor:
        escaped = escape_html(listing_url)
        return f'<a href="{escaped}">Open source post</a>'
    return escape_html(listing_url)


def format_detail_lines(item: dict[str, Any]) -> tuple[list[str], set[str]]:
    metadata = resolve_listing_metadata(item)
    family = str(item.get("listing_family") or metadata["listing_family"] or "general")
    used_keys: set[str] = set()
    lines: list[str] = []

    if family == "vehicle_rental":
        lines.extend(
            [
                f"🚘 <b>Type:</b> {display_value(item.get('listing_type_label') or item.get('title'))}",
                f"⚙️ <b>Condition:</b> {display_value(_pick_field(item, ['condition', 'Condition']))}",
                f"🕹 <b>Transmission:</b> {display_value(_pick_field(item, ['transmission', 'Transmission', 'gearbox', 'Gearbox']))}",
                f"⛽ <b>Fuel:</b> {display_value(_pick_field(item, ['fuel', 'Fuel', 'fuel type', 'Fuel Type']))}",
                f"📏 <b>Mileage:</b> {display_value(_pick_field(item, ['mileage', 'Mileage', 'odometer', 'Odometer']))}",
            ]
        )
        return [line for line in lines if not line.endswith("N/A")], used_keys

    lines.extend(
        [
            f"🛏 <b>Bedrooms:</b> {display_value(item.get('bedrooms'))}",
            f"🚿 <b>Bathrooms:</b> {display_value(item.get('bathrooms'))}",
            f"🚽 <b>Toilets:</b> {display_value(item.get('toilets'))}",
            f"📐 <b>Size:</b> {display_value(format_size(item.get('property_size_sqm')))}",
            f"🪑 <b>Furnishing:</b> {display_value(item.get('furnishing'))}",
            f"🏠 <b>Property type:</b> {display_value(item.get('property_type') or item.get('listing_type_label'))}",
            f"⏳ <b>Minimum rental period:</b> {display_value(item.get('minimum_rental_period'))}",
            f"🧭 <b>Location text:</b> {display_value(item.get('location_text'))}",
        ]
    )
    return [line for line in lines if not line.endswith("N/A")], used_keys


def format_attribute_summary_lines(attributes: dict[str, Any], used_keys: set[str]) -> list[str]:
    lines: list[str] = []
    for key, value in attributes.items():
        if key in used_keys or value in (None, ""):
            continue
        lines.append(f"• <b>{escape_html(str(key))}:</b> {escape_html(str(value))}")
    return lines


def format_size(size_sqm: Any) -> str:
    if size_sqm in (None, ""):
        return "N/A"
    return f"{size_sqm} sqm"


def display_value(value: Any) -> str:
    if value in (None, ""):
        return "N/A"
    return escape_html(str(value))


def escape_html(value: str) -> str:
    return html.escape(value, quote=False)


def resolve_listing_metadata(item: dict[str, Any]) -> dict[str, str]:
    return infer_listing_family(
        title=str(item.get("title") or "") or None,
        description=str(item.get("description") or "") or None,
        category=str(item.get("category") or "") or None,
        attributes={str(key): str(value) for key, value in (item.get("attributes") or {}).items()},
        listing_url=str(item.get("listing_url") or "") or None,
    )


def sanitize_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    return safe.strip("_.") or "listing"


def copy_media_files(source_paths: list[str], target_dir: Path) -> list[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for index, source_path in enumerate(source_paths, start=1):
        path = Path(source_path)
        if not path.exists():
            continue
        target_path = target_dir / f"image_{index:02d}{path.suffix or '.jpg'}"
        shutil.copy2(path, target_path)
        copied.append(str(target_path))
    return copied


def _pick_field(item: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        if item.get(key) not in (None, ""):
            return item.get(key)
    return None
