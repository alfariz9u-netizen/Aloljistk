"""
Canonical city list + normalization, so spelling variants
("Бишкек"/"бишкек"/"Bishkek") all resolve to one canonical spelling
before matching. No Google Maps / geo API in this MVP -- a static
table is enough for the corridors that actually matter, per project
scope.

Covers Kyrgyzstan plus its four bordering countries/regions --
Kazakhstan, Uzbekistan, Tajikistan, and the Chinese border-crossing
hubs (Kashgar/Urumqi, relevant via the Irkeshtam and Torugart passes).
Cross-border freight is the norm here, not the exception: Osh sits
inside the Fergana Valley shared with Uzbekistan and Tajikistan, and
Almaty (Kazakhstan) is closer to Bishkek than most cities inside
Kyrgyzstan itself. Russia is deliberately NOT included yet despite
being a very common destination for CIS freight -- it doesn't share a
border with Kyrgyzstan, so it's a separate, larger addition (many major
hub cities across an enormous country) better scoped as its own
follow-up if real demand shows up for it, rather than folded in here.
"""

# canonical -> list of accepted variant spellings (typos, alt transliteration,
# Soviet-era names still used colloquially by older drivers)
_CITY_VARIANTS: dict[str, list[str]] = {
    # --- Kyrgyzstan ---
    "Бишкек": ["Бишкек", "бишкек", "Bishkek", "Фрунзе"],  # Frunze = Soviet-era name
    "Ош": ["Ош", "ош", "Osh", "Оше", "Ошь"],
    "Джалал-Абад": ["Джалал-Абад", "Джалалабад", "Жалал-Абад", "Jalal-Abad", "Jalalabad"],
    "Каракол": ["Каракол", "каракол", "Karakol", "Пржевальск"],  # Przhevalsk = Soviet-era name
    "Токмок": ["Токмок", "Токмак", "Tokmok", "Tokmak"],
    "Кара-Балта": ["Кара-Балта", "Карабалта", "Kara-Balta"],
    "Нарын": ["Нарын", "нарын", "Naryn"],
    "Талас": ["Талас", "талас", "Talas"],
    "Баткен": ["Баткен", "баткен", "Batken"],
    "Кызыл-Кия": ["Кызыл-Кия", "Кызылкия", "Kyzyl-Kiya"],
    "Узген": ["Узген", "узген", "Uzgen"],
    "Кант": ["Кант", "кант", "Kant"],
    "Балыкчы": ["Балыкчы", "Балыкчи", "Balykchy", "Рыбачье"],  # Rybachye = Soviet-era name
    "Кербен": ["Кербен", "кербен", "Kerben"],

    # --- Kazakhstan (north/northwest border -- Almaty is a major
    # regional freight hub closer to Bishkek than most Kyrgyz cities) ---
    "Алматы": ["Алматы", "алматы", "Almaty", "Алма-Ата", "Алма-ата"],  # Alma-Ata = Soviet-era name
    "Шымкент": ["Шымкент", "шымкент", "Shymkent", "Чимкент", "чимкент"],  # Chimkent = old spelling
    "Тараз": ["Тараз", "тараз", "Taraz", "Джамбул", "джамбул"],  # Dzhambul = Soviet-era name
    "Астана": ["Астана", "астана", "Astana", "Нур-Султан", "нур-султан", "Nur-Sultan"],
    "Туркестан": ["Туркестан", "туркестан", "Turkestan"],
    "Кызылорда": ["Кызылорда", "кызылорда", "Kyzylorda"],

    # --- Uzbekistan (west/southwest border -- Andijan/Fergana/Namangan
    # are inside the same Fergana Valley as Osh, extremely common
    # cross-border freight corridor) ---
    "Ташкент": ["Ташкент", "ташкент", "Tashkent"],
    "Самарканд": ["Самарканд", "самарканд", "Samarkand"],
    "Андижан": ["Андижан", "андижан", "Andijan", "Andijon"],
    "Фергана": ["Фергана", "фергана", "Fergana", "Farg'ona"],
    "Наманган": ["Наманган", "наманган", "Namangan"],
    "Бухара": ["Бухара", "бухара", "Bukhara", "Buxoro"],

    # --- Tajikistan (south border -- Khujand sits in the same Fergana
    # Valley corridor as Osh/Andijan) ---
    "Душанбе": ["Душанбе", "душанбе", "Dushanbe"],
    "Худжанд": ["Худжанд", "худжанд", "Khujand", "Ленинабад", "ленинабад"],  # Leninabad = Soviet-era name
    "Куляб": ["Куляб", "куляб", "Kulob", "Kulyab"],

    # --- China border-crossing hubs (east border -- Irkeshtam and
    # Torugart mountain passes connect directly to Kashgar/Xinjiang;
    # relevant for import/export freight, not just passenger routes) ---
    "Кашгар": ["Кашгар", "кашгар", "Kashgar", "Kashi"],
    "Урумчи": ["Урумчи", "урумчи", "Urumqi", "Урумци"],
}

_LOOKUP: dict[str, str] = {}
for _canonical, _variants in _CITY_VARIANTS.items():
    for _v in _variants:
        _LOOKUP[_v.strip().lower()] = _canonical

# Country-name aliases: sometimes a driver/shipper writes just the
# country ("Казахстан", "в Узбекистан") instead of a specific city.
# Rather than leaving that as an unrecognized string that can only
# ever match another identical mention of the same country, each
# country name resolves to that country's main FREIGHT hub -- the
# busiest logistics city nearest Kyrgyzstan, which is not always the
# official political capital. Kazakhstan is the clearest example:
# it maps to Алматы (the actual trade/logistics hub, and much closer
# to the Kyrgyz border) rather than Астана/Нур-Султан, which is far to
# the north and rarely the real destination for cross-border freight.
#
# Trade-off worth knowing: two people who both just wrote "Казахстан"
# meaning different specific areas of that huge country will now match
# each other (both resolve to Алматы) even though they may have meant
# different actual cities. This is a deliberate simplification --
# matching on "somewhere in that country" beats not matching a vague
# mention at all, and the admin still verifies real details with both
# sides before connecting anyone (see project rule: the admin always
# mediates first contact).
_COUNTRY_TO_HUB: dict[str, str] = {
    "кыргызстан": "Бишкек", "киргизия": "Бишкек", "kyrgyzstan": "Бишкек",
    "казахстан": "Алматы", "kazakhstan": "Алматы",
    "узбекистан": "Ташкент", "uzbekistan": "Ташкент",
    "таджикистан": "Душанбе", "tajikistan": "Душанбе",
    "китай": "Кашгар", "china": "Кашгар",
}
_LOOKUP.update(_COUNTRY_TO_HUB)

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
