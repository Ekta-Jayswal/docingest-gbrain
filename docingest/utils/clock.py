# What this file does: gives every timestamp in docingest one time zone, IST (UTC+05:30). It does
# not read the machine's clock setting, so a server in UTC still writes India time.
"""Timestamps in IST.

India has no daylight saving time, so a fixed offset is exact. A fixed offset also avoids
needing the operating system's time zone database, which a slim container image may not have.
"""

from __future__ import annotations

import datetime as dt

IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")


# The current time in IST, as an aware datetime.
def now() -> dt.datetime:
    """The current time in IST."""
    return dt.datetime.now(IST)


# An ISO 8601 timestamp in IST, such as 2026-10-22T20:13:04+0530. Used in the data files.
def iso() -> str:
    """The current time as an ISO 8601 string with the IST offset."""
    return now().strftime("%Y-%m-%dT%H:%M:%S%z")


# A compact stamp for file names, such as 20261022-201304.
def compact() -> str:
    """The current time as a compact stamp for file names, in IST."""
    return now().strftime("%Y%m%d-%H%M%S")
