"""
Deterministic, rule-based matching engine. NEVER driven by AI (see
project security rules) -- purely backend logic so it stays auditable
and testable.

Mandatory condition: origin and destination must match (after city
normalization). Optional signals (truck type, count, availability,
current trip) only adjust the score -- they never turn a geographic
mismatch into a match.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Load, LoadStatus, Match, MatchStatus, Truck, TruckStatus
from app.services.cities import normalize_city

WEIGHTS = {
    "origin": 40,
    "destination": 40,
    "truck_type": 10,
    "availability": 10,
}


@dataclass
class MatchResult:
    score: int
    reasons: list[str] = field(default_factory=list)


def _norm(s: str | None) -> str:
    return (normalize_city(s) or "").strip().lower()


def score_match(load: Load, truck: Truck) -> MatchResult | None:
    """Returns None if the mandatory origin/destination condition isn't
    met -- such a pair is NOT a match regardless of score."""
    reasons: list[str] = []

    origin_ok = _norm(load.origin_city) == _norm(truck.current_city)
    dest_ok = _norm(load.destination_city) == _norm(
        truck.desired_destination or truck.trip_destination
    )
    if not (origin_ok and dest_ok):
        return None

    score = WEIGHTS["origin"] + WEIGHTS["destination"]
    reasons.append("✓ Пункт отправления и назначения совпадают")

    if load.truck_type and truck.truck_type:
        if load.truck_type.strip().lower() == truck.truck_type.strip().lower():
            score += WEIGHTS["truck_type"]
            reasons.append("✓ Тип машины совпадает")
    else:
        score += WEIGHTS["truck_type"] // 2  # unspecified type: partial credit, not a mismatch

    if truck.available and truck.status in (TruckStatus.AVAILABLE, TruckStatus.ARRIVING_SOON):
        score += WEIGHTS["availability"]
        reasons.append("✓ Перевозчик доступен")

    return MatchResult(score=min(score, 100), reasons=reasons)


def find_candidate_trucks(load: Load, trucks: list[Truck]) -> list[tuple[Truck, MatchResult]]:
    scored = []
    for truck in trucks:
        result = score_match(load, truck)
        if result is not None:
            scored.append((truck, result))
    scored.sort(key=lambda pair: pair[1].score, reverse=True)
    return scored


async def create_match_safely(
    db: AsyncSession, load: Load, truck: Truck, result: MatchResult, is_proactive: bool = False
) -> tuple[Match, bool]:
    """
    Race-safe match creation. Several requests can try to match the same
    (load, truck) pair concurrently (e.g. a new truck registering while
    the proactive job also scans it) -- the DB unique constraint on
    (load_id, truck_id) is the actual source of truth, not an in-memory
    check. On conflict, fetch and return the match someone else already
    created instead of erroring or duplicating.

    Returns (match, created).
    """
    existing = await db.execute(
        select(Match).where(Match.load_id == load.id, Match.truck_id == truck.id)
    )
    row = existing.scalar_one_or_none()
    if row is not None:
        return row, False

    match = Match(
        load_id=load.id,
        truck_id=truck.id,
        score=result.score,
        reason="\n".join(result.reasons),
        is_proactive=is_proactive,
        status=MatchStatus.PENDING,
    )
    try:
        async with db.begin_nested():
            db.add(match)
            await db.flush()
        if load.status in (LoadStatus.NEW, LoadStatus.WAITING_FOR_MATCH):
            load.status = LoadStatus.MATCHED
        return match, True
    except IntegrityError:
        await db.rollback()
        existing = await db.execute(
            select(Match).where(Match.load_id == load.id, Match.truck_id == truck.id)
        )
        return existing.scalar_one(), False


async def find_best_match_for_load(db: AsyncSession, load: Load) -> tuple[Truck, MatchResult] | None:
    result = await db.execute(
        select(Truck).where(
            Truck.is_deleted.is_(False),
            Truck.available.is_(True),
            Truck.status.in_([TruckStatus.AVAILABLE, TruckStatus.ARRIVING_SOON]),
        )
    )
    trucks = result.scalars().all()
    candidates = find_candidate_trucks(load, trucks)
    return candidates[0] if candidates else None
