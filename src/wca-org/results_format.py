"""Decode WCA multiblind (333mbf) encoded ``best`` / ``average`` values."""


def mbld_decode(encoded: int) -> tuple[int, int, int] | None:
    """
    Decode WCA MBLD encoded result to (solved, attempted, time_seconds).

    Encoding: (99 - (solved - missed)) * 10_000_000 + time_seconds * 100 + missed
    where missed = attempted - solved.
    """
    if encoded is None or encoded <= 0 or encoded >= 1_000_000_000:
        return None
    missed = encoded % 100
    time_seconds = (encoded % 10_000_000) // 100
    dd = encoded // 10_000_000
    solved = (99 - dd) + missed
    attempted = solved + missed
    if solved <= 0 or attempted <= 0 or time_seconds < 0:
        return None
    return solved, attempted, time_seconds


def mbld_format(encoded: int) -> str:
    """Format MBLD encoded result as 'X/Y M:SS'."""
    if encoded is None or encoded <= 0:
        return "—"
    decoded = mbld_decode(encoded)
    if decoded is None:
        return str(encoded)
    solved, attempted, time_seconds = decoded
    mins, secs = divmod(time_seconds, 60)
    return f"{solved}/{attempted} {mins}:{secs:02d}"


def mbld_solved_count(encoded: int) -> int | None:
    """Solved cubes from WCA MBLD encoded result."""
    if encoded is None or encoded <= 0:
        return None
    decoded = mbld_decode(encoded)
    return decoded[0] if decoded is not None else None
