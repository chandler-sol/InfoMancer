from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones


COMMON_TIMEZONES = (
    ("UTC", "Coordinated Universal Time"),
    ("America/New_York", "Eastern Time — New York"),
    ("America/Chicago", "Central Time — Chicago"),
    ("America/Denver", "Mountain Time — Denver"),
    ("America/Phoenix", "Arizona Time — Phoenix"),
    ("America/Los_Angeles", "Pacific Time — Los Angeles"),
    ("America/Anchorage", "Alaska Time — Anchorage"),
    ("Pacific/Honolulu", "Hawaii Time — Honolulu"),
    ("America/Halifax", "Atlantic Time — Halifax"),
    ("America/St_Johns", "Newfoundland Time — St. John's"),
    ("Europe/London", "United Kingdom — London"),
    ("Europe/Paris", "Central Europe — Paris"),
    ("Asia/Tokyo", "Japan — Tokyo"),
    ("Australia/Sydney", "Australia — Sydney"),
)


def _offset_label(zone: str, moment: datetime) -> str:
    offset = moment.astimezone(ZoneInfo(zone)).utcoffset()
    seconds = int(offset.total_seconds()) if offset else 0
    sign = "+" if seconds >= 0 else "−"
    seconds = abs(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _friendly_location(zone: str) -> str:
    return " / ".join(part.replace("_", " ") for part in zone.split("/"))


def timezone_groups(
    moment: datetime | None = None,
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Return grouped IANA values with labels intended for a native select."""
    reference = moment or datetime.now(timezone.utc)
    available = available_timezones()
    common_values = {value for value, _label in COMMON_TIMEZONES}
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    common = [
        (value, f"{label} ({_offset_label(value, reference)} currently)")
        for value, label in COMMON_TIMEZONES if value in available
    ]
    groups.append(("Common choices", common))

    definitions = (
        ("Americas", ("America/",)),
        ("Europe", ("Europe/",)),
        ("Africa", ("Africa/",)),
        ("Asia", ("Asia/",)),
        (
            "Australia and Pacific",
            ("Australia/", "Pacific/", "Indian/", "Antarctica/"),
        ),
        ("Atlantic", ("Atlantic/",)),
    )
    assigned = set(common_values)
    for group_name, prefixes in definitions:
        values = sorted(
            zone for zone in available
            if zone not in assigned and zone.startswith(prefixes)
        )
        assigned.update(values)
        groups.append((
            group_name,
            [
                (
                    zone,
                    f"{_friendly_location(zone)} "
                    f"({_offset_label(zone, reference)} currently)",
                )
                for zone in values
            ],
        ))

    other = sorted(
        zone for zone in available
        if zone not in assigned and not zone.startswith(("posix/", "right/"))
    )
    if other:
        groups.append((
            "Other time zones",
            [
                (
                    zone,
                    f"{_friendly_location(zone)} "
                    f"({_offset_label(zone, reference)} currently)",
                )
                for zone in other
            ],
        ))
    return groups
