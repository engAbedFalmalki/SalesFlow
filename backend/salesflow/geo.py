"""Saudi geography reference: the 13 administrative regions and the cities SalesFlow knows.

One source of truth for three consumers:
- the synthetic generator picks customer cities from ``CITIES`` (weights roughly follow population);
- the warehouse load derives ``dw.DimCustomer.Region`` from the customer's city;
- the API serves the region/city filter lists with English and Arabic names.

Cities are matched by their English name (case-insensitive in SQL Server). A
city that is not listed keeps a NULL region and shows as "Unknown region".
"""

from __future__ import annotations

# code -> (English, Arabic). Ordered as listed by the national statistics authority.
REGIONS: dict[str, tuple[str, str]] = {
    "riyadh": ("Riyadh", "الرياض"),
    "makkah": ("Makkah", "مكة المكرمة"),
    "madinah": ("Madinah", "المدينة المنورة"),
    "qassim": ("Qassim", "القصيم"),
    "eastern": ("Eastern Province", "المنطقة الشرقية"),
    "asir": ("Asir", "عسير"),
    "tabuk": ("Tabuk", "تبوك"),
    "hail": ("Hail", "حائل"),
    "northern_borders": ("Northern Borders", "الحدود الشمالية"),
    "jazan": ("Jazan", "جازان"),
    "najran": ("Najran", "نجران"),
    "bahah": ("Al Bahah", "الباحة"),
    "jouf": ("Al Jouf", "الجوف"),
}

# (English city, Arabic city, region code, generator weight)
CITIES: list[tuple[str, str, str, int]] = [
    ("Riyadh", "الرياض", "riyadh", 240), ("Al Kharj", "الخرج", "riyadh", 14), ("Ad Diriyah", "الدرعية", "riyadh", 6),
    ("Al Majmaah", "المجمعة", "riyadh", 5), ("Az Zulfi", "الزلفي", "riyadh", 4), ("Wadi ad-Dawasir", "وادي الدواسر", "riyadh", 4),
    ("Shaqra", "شقراء", "riyadh", 3), ("Afif", "عفيف", "riyadh", 3),
    ("Jeddah", "جدة", "makkah", 150), ("Makkah", "مكة المكرمة", "makkah", 70), ("Taif", "الطائف", "makkah", 25),
    ("Rabigh", "رابغ", "makkah", 4), ("Al Qunfudhah", "القنفذة", "makkah", 5), ("Al Lith", "الليث", "makkah", 3),
    ("Madinah", "المدينة المنورة", "madinah", 55), ("Yanbu", "ينبع", "madinah", 14), ("AlUla", "العلا", "madinah", 3),
    ("Badr", "بدر", "madinah", 2), ("Al Hinakiyah", "الحناكية", "madinah", 2),
    ("Buraidah", "بريدة", "qassim", 25), ("Unaizah", "عنيزة", "qassim", 10), ("Ar Rass", "الرس", "qassim", 7),
    ("Al Bukayriyah", "البكيرية", "qassim", 3),
    ("Dammam", "الدمام", "eastern", 45), ("Khobar", "الخبر", "eastern", 25), ("Dhahran", "الظهران", "eastern", 8),
    ("Al Ahsa", "الأحساء", "eastern", 30), ("Jubail", "الجبيل", "eastern", 12), ("Qatif", "القطيف", "eastern", 12),
    ("Hafr Al Batin", "حفر الباطن", "eastern", 10), ("Ras Tanura", "رأس تنورة", "eastern", 3), ("Khafji", "الخفجي", "eastern", 3),
    ("Abha", "أبها", "asir", 18), ("Khamis Mushait", "خميس مشيط", "asir", 18), ("Bisha", "بيشة", "asir", 6),
    ("An Namas", "النماص", "asir", 3),
    ("Tabuk", "تبوك", "tabuk", 18), ("Duba", "ضباء", "tabuk", 3), ("Al Wajh", "الوجه", "tabuk", 2), ("Umluj", "أملج", "tabuk", 2),
    ("Hail", "حائل", "hail", 16), ("Baqaa", "بقعاء", "hail", 2),
    ("Arar", "عرعر", "northern_borders", 7), ("Rafha", "رفحاء", "northern_borders", 3), ("Turaif", "طريف", "northern_borders", 2),
    ("Jazan", "جازان", "jazan", 10), ("Sabya", "صبيا", "jazan", 6), ("Abu Arish", "أبو عريش", "jazan", 5),
    ("Samtah", "صامطة", "jazan", 3),
    ("Najran", "نجران", "najran", 10), ("Sharurah", "شرورة", "najran", 3),
    ("Al Bahah", "الباحة", "bahah", 6), ("Baljurashi", "بلجرشي", "bahah", 2), ("Al Mikhwah", "المخواة", "bahah", 2),
    ("Sakaka", "سكاكا", "jouf", 7), ("Al Qurayyat", "القريات", "jouf", 5), ("Dumat Al Jandal", "دومة الجندل", "jouf", 2),
]

_BY_CITY = {c[0].casefold(): c for c in CITIES}


def region_of(city: str | None) -> str | None:
    """English region name for a city, or None when the city is unknown."""
    c = _BY_CITY.get((city or "").strip().casefold())
    return REGIONS[c[2]][0] if c else None


def city_region_pairs() -> list[dict[str, str]]:
    """City -> English region name, as rows for an OPENJSON parameter."""
    return [{"city": c[0], "region": REGIONS[c[2]][0]} for c in CITIES]


def reference() -> dict[str, list[dict[str, str]]]:
    """Region and city lists for the UI, in reference order, with both languages."""
    return {
        "regions": [{"value": en, "en": en, "ar": ar} for en, ar in REGIONS.values()],
        "cities": [{"value": en, "en": en, "ar": ar, "region": REGIONS[r][0]} for en, ar, r, _ in CITIES],
    }


_AR = {en: ar for en, ar in REGIONS.values()} | {c[0]: c[1] for c in CITIES}


def label(name: str | None, lang: str) -> str | None:
    """Arabic name for a known region or city when ``lang`` is "ar"; the stored name otherwise."""
    if name is None or lang != "ar":
        return name
    return _AR.get(name, name)
