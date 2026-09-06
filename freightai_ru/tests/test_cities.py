import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.cities import normalize_city


def test_spelling_variants_resolve_to_canonical():
    assert normalize_city("бишкек") == "Бишкек"
    assert normalize_city("Бишкек") == "Бишкек"
    assert normalize_city("Bishkek") == "Бишкек"
    assert normalize_city("Джалалабад") == "Джалал-Абад"
    assert normalize_city("Джалал-Абад") == "Джалал-Абад"


def test_unknown_city_passes_through_trimmed():
    assert normalize_city("  Чолпон-Ата  ") == "Чолпон-Ата"


def test_none_and_empty():
    assert normalize_city(None) is None
    assert normalize_city("") is None
