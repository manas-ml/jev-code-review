"""Money helpers. Amounts are stored as integer cents."""

from __future__ import annotations


def to_cents(amount: float) -> int:
    return int(amount * 100)


def from_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def split(total_cents: int, ways: int) -> list[int]:
    if ways <= 0:
        raise ValueError("ways must be positive")
    share = total_cents // ways
    return [share] * ways
