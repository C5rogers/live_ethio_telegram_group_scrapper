from __future__ import annotations

import re
from urllib.parse import urlparse


PROPERTY_HINTS: dict[str, tuple[str, ...]] = {
    "condominium": ("condominium", "condo", "ኮንዶሚንየም", "ኮንዶ"),
    "apartment": ("apartment", "flat", "studio", "አፓርታማ"),
    "house": ("house", "home", "villa", "bungalow", "mansion", "ቤት"),
    "office": ("office", "workspace", "work space", "commercial", "ቢሮ"),
    "shop": ("shop", "store", "retail", "showroom", "ሱቅ"),
    "land": ("land", "plot", "site", "መሬት"),
    "warehouse": ("warehouse", "depot", "storage", "ማከማቻ"),
    "room": ("room", "bedspace", "bed room", "single room", "ክፍል"),
}

VEHICLE_HINTS: dict[str, tuple[str, ...]] = {
    "car": ("car", "sedan", "suv", "jeep", "van", "minivan", "wagon"),
    "motorcycle": ("motorcycle", "bike", "bajaj", "tuk tuk", "tricycle"),
    "bus": ("bus", "shuttle", "coaster", "minibus", "seater"),
    "truck": ("truck", "pickup", "lorry", "cargo"),
}


def build_text_blob(*parts: object) -> str:
    values = [str(part).strip().lower() for part in parts if part not in (None, "")]
    return " ".join(value for value in values if value)


def slugify(value: str | None, default: str = "general") -> str:
    if not value:
        return default
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or default


def infer_listing_family(
    *,
    title: str | None = None,
    description: str | None = None,
    category: str | None = None,
    attributes: dict[str, str] | None = None,
    listing_url: str | None = None,
) -> dict[str, str]:
    attribute_text = " ".join(f"{key} {value}" for key, value in (attributes or {}).items())
    url_path = urlparse(listing_url or "").path
    blob = build_text_blob(title, description, category, attribute_text, url_path)

    property_subtype = _match_hint(blob, PROPERTY_HINTS)
    vehicle_subtype = _match_hint(blob, VEHICLE_HINTS)
    is_rental = any(
        keyword in blob
        for keyword in (
            "rent",
            "rental",
            "for rent",
            "let",
            "ኪራይ",
            "የሚከራይ",
            "የሚከራዩ",
            "ለኪራይ",
            "ለማከራየት",
            "ማከራየት",
        )
    )

    if property_subtype:
        return {
            "listing_family": "property_rental",
            "listing_type": property_subtype,
            "listing_type_label": f"{property_subtype.replace('_', ' ').title()} Rental",
            "listing_folder": slugify(property_subtype),
            "rental_channel": "properties",
        }

    if vehicle_subtype:
        return {
            "listing_family": "vehicle_rental",
            "listing_type": vehicle_subtype,
            "listing_type_label": f"{vehicle_subtype.replace('_', ' ').title()} Rental",
            "listing_folder": slugify(vehicle_subtype),
            "rental_channel": "vehicles",
        }

    if is_rental:
        return {
            "listing_family": "general_rental",
            "listing_type": "rental",
            "listing_type_label": "Rental Listing",
            "listing_folder": "general",
            "rental_channel": "general",
        }

    return {
        "listing_family": "unknown",
        "listing_type": "unknown",
        "listing_type_label": "Rental Listing",
        "listing_folder": "general",
        "rental_channel": "general",
    }


def _match_hint(blob: str, hints: dict[str, tuple[str, ...]]) -> str | None:
    for label, keywords in hints.items():
        for keyword in keywords:
            if keyword in blob:
                return label
    return None
