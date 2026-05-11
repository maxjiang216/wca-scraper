"""Decode WCA multiblind (333mbf) encoded ``best`` / ``average`` values to a solved count."""


def mbld_solved_count(encoded: int) -> int | None:
    """
    Solved cubes from WCA multiblind encoded result (API / export style).

    Old format (``value < 1_000_000_000``): ``99 - (value // 10_000_000) + (value % 100)``.
    See WCA multi-blind result encoding (e.g. SpeedSolving / WCA sources).
    """
    if encoded is None or encoded <= 0:
        return None
    if encoded >= 1_000_000_000:
        # Newer packed format — not decoded here; extend when needed.
        return None
    dd = encoded // 10_000_000
    missed = encoded % 100
    if dd > 99 or dd < 0:
        return None
    return 99 - dd + missed
