"""
Canonical Kyrgyzstan city list + normalization, so spelling variants
("Бишкек"/"бишкек"/"Bishkek") all resolve to one canonical spelling
before matching. No Google Maps / geo API in this MVP -- a static
table is enough for the corridors that actually matter, per project
scope. Localized for the Kyrgyzstan/Central Asia market (Russian is
the shared administrative/business language across Kyrgyzstan,
Tajikistan, and -- to a lesser extent -- Turkmenistan).
"""

# canonical -> list of accepted variant spellings (typos, alt transliteration)
_CITY_VARIANTS: dict[str, list[str]] = {
    "Бишкек": ["Бишкек", "бишкек", "Bishkek", "Фрунзе"],  # Frunze = Soviet-era name, still colloquially used by older drivers
    "Ош": ["Ош", "ош", "Osh", "Оше", "Ошь"],
    "Джалал-Абад": ["Джалал-Абад", "Джалалабад", "Жалал-Абад", "Jalal-Abad", "Jalalabad"],
    "Каракол": ["Каракол", "карakol", "Karakol", "Пржевальск"],  # Przhevalsk = Soviet-era name
    "Токмок": ["Токмок", "Токмак", "Tokmok", "Tokmak"],
    "Кара-Балта": ["Кара-Балта", "Карабалта", "Kara-Balta"],
    "Нарын": ["Нарын", "нарын", "Naryn"],
    "Талас": ["Талас", "талас", "Talas"],
    "Баткен": ["Баткен", "батken", "Batken"],
    "Кызыл-Кия": ["Кызыл-Кия", "Кызылкия", "Kyzyl-Kiya"],
    "Узген": ["Узген", "узген", "Uzgen"],
    "Кант": ["Кант", "кант", "Kant"],
    "Балыкчы": ["Балыкчы", "Балыкчи", "Balykchy", "Рыбачье"],  # Rybachye = Soviet-era name
    "Кербен": ["Кербен", "керben", "Kerben"],
}

_LOOKUP: dict[str, str] = {}
for _canonical, _variants in _CITY_VARIANTS.items():
    for _v in _variants:
        _LOOKUP[_v.strip().lower()] = _canonical

CANONICAL_CITIES = sorted(_CITY_VARIANTS.keys())


def normalize_city(raw: str | None) -> str | None:
    """Returns the canonical spelling for a known city, or the
    whitespace-trimmed original if it isn't recognized (so an unusual
    but real city/region isn't silently dropped -- it just won't
    auto-match on spelling variants)."""
    if not raw:
        return None
    stripped = raw.strip()
    return _LOOKUP.get(stripped.lower(), stripped)


def is_known_city(raw: str | None) -> bool:
    if not raw:
        return False
    return raw.strip().lower() in _LOOKUP
