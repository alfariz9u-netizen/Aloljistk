"""
Matching engine tests, per project rule 39. Origin/destination match is
mandatory -- score alone is never enough (a Karakol->Osh truck must NOT
match a Bishkek->Osh load even though destination matches).
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.models.models import Load, Truck, TruckStatus
from app.services.matching import score_match


def _load(origin, destination, truck_type=None):
    return Load(id=uuid.uuid4(), user_id=uuid.uuid4(), origin_city=origin,
                destination_city=destination, truck_type=truck_type, truck_count=1)


def _truck(current_city, desired_destination, truck_type=None, available=True,
           status=TruckStatus.AVAILABLE):
    return Truck(id=uuid.uuid4(), user_id=uuid.uuid4(), current_city=current_city,
                 desired_destination=desired_destination, truck_type=truck_type,
                 available=available, status=status)


def test_exact_route_match():
    load = _load("Бишкек", "Ош")
    truck = _truck("Бишкек", "Ош")
    result = score_match(load, truck)
    assert result is not None
    assert result.score >= 80


def test_different_origin_is_never_a_match_even_with_same_destination():
    load = _load("Бишкек", "Ош")
    truck = _truck("Каракол", "Ош")
    assert score_match(load, truck) is None


def test_spelling_variants_still_match():
    load = _load("ош", "Джалалабад")
    truck = _truck("Ош", "Джалал-Абад")
    assert score_match(load, truck) is not None


def test_truck_type_mismatch_lowers_score_but_still_matches_on_route():
    load = _load("Бишкек", "Ош", truck_type="Тент")
    truck = _truck("Бишкек", "Ош", truck_type="Рефрижератор")
    result = score_match(load, truck)
    assert result is not None  # route is mandatory-and-satisfied; type is optional signal only
    matching_type = _truck("Бишкек", "Ош", truck_type="Тент")
    better = score_match(load, matching_type)
    assert better.score > result.score


def test_unavailable_truck_scores_lower_but_is_excluded_upstream():
    load = _load("Бишкек", "Ош")
    truck = _truck("Бишкек", "Ош", available=False, status=TruckStatus.UNAVAILABLE)
    result = score_match(load, truck)
    # score_match itself doesn't filter by availability (that's the
    # candidate-selection query's job, see find_best_match_for_load) --
    # it should still be a lower score than an available truck.
    available_truck = _truck("Бишкек", "Ош", available=True)
    assert result.score < score_match(load, available_truck).score
