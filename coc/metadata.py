"""Timestamps reported by the source filesystem.

Read once, at ingest, before the file is vaulted — after that the vault copy has
its own times and the original's are gone.

Every value here is a **claim made by the source system**, not a fact this tool
can verify. File times are trivially set by anyone with the file (`touch -t`,
`os.utime`, any number of GUI tools). They are recorded because they are useful
and because dropping them silently would be worse, but they must never be
presented as proof of when something was created. Only the hash is evidence.

The naming trap this module exists to avoid: ``st_ctime`` means **creation time**
on Windows and **inode change time** on Unix. Those are entirely different facts.
Reporting both as "created" would put a falsehood in a document someone relies
on, so the platform that produced the reading travels with it.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: How the values were obtained. Stored alongside them so a reader can tell a
#: filesystem reading from something a browser volunteered.
SOURCE_FILESYSTEM = "filesystem"
SOURCE_BROWSER = "browser"


def _utc(epoch: float | None) -> str | None:
    """An epoch seconds value as an ISO-8601 UTC string, or None."""
    if epoch is None:
        return None
    try:
        return (
            datetime.fromtimestamp(epoch, tz=UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (OSError, OverflowError, ValueError):
        # Some filesystems report 0 or absurd values for times they do not keep.
        return None


@dataclass(frozen=True)
class SourceTimestamps:
    """What the source system said about a file's times."""

    modified_at: str | None = None
    created_at: str | None = None
    accessed_at: str | None = None
    reported_by: str = SOURCE_FILESYSTEM
    platform_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_modified_at": self.modified_at,
            "source_created_at": self.created_at,
            "source_accessed_at": self.accessed_at,
            "source_time_reported_by": self.reported_by,
            "source_time_platform": self.platform_name,
        }

    @property
    def is_empty(self) -> bool:
        return not any((self.modified_at, self.created_at, self.accessed_at))


def read_source_timestamps(path: str | Path) -> SourceTimestamps:
    """Stat a file and report its times, being honest about what they mean."""
    try:
        stat = Path(path).stat()
    except OSError:
        return SourceTimestamps(platform_name=platform.system())

    system = platform.system()

    # Creation time is only genuinely available on some platforms:
    #   * macOS/BSD expose st_birthtime
    #   * Windows puts creation time in st_ctime
    #   * Linux has no portable creation time at all — st_ctime is the inode
    #     change time, which is NOT when the file was created.
    created = getattr(stat, "st_birthtime", None)
    if created is None and system == "Windows":
        created = stat.st_ctime

    return SourceTimestamps(
        modified_at=_utc(stat.st_mtime),
        created_at=_utc(created),
        accessed_at=_utc(stat.st_atime),
        reported_by=SOURCE_FILESYSTEM,
        platform_name=system,
    )


def from_browser(last_modified_ms: str | float | None) -> SourceTimestamps:
    """Build timestamps from a browser's ``File.lastModified``.

    Marked as browser-reported rather than read from a filesystem, because that
    is what it is: a value the client volunteered, from a machine this
    application never saw. It is still worth keeping — it is usually the only
    trace of the original file's age left after an upload — but a report should
    not imply the server verified it.
    """
    if last_modified_ms in (None, ""):
        return SourceTimestamps(reported_by=SOURCE_BROWSER)
    try:
        seconds = float(last_modified_ms) / 1000.0
    except (TypeError, ValueError):
        return SourceTimestamps(reported_by=SOURCE_BROWSER)

    return SourceTimestamps(
        modified_at=_utc(seconds),
        reported_by=SOURCE_BROWSER,
        platform_name="",
    )
