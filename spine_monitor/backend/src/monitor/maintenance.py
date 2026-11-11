"""Periodic maintenance of the store: a backup copy and the retention of raw chunks.

Every `interval_s`: a consistent copy of the database (sqlite3 backup API) into `backup_dir`, keeping the newest `keep`;
then, once that copy holds them, the raw chunks of the `processed` sessions started more than `chunk_retention_days` ago are
purged (None keeps them forever). Feature sets, assessments and self-reports are never deleted. A failure is logged by type
only and the next round tries again.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from monitor.db import Store

_log = logging.getLogger(__name__)

BACKUP_PREFIX = "backup-"


class Maintenance:
    def __init__(
        self,
        store: Store,
        backup_dir: Path,
        *,
        interval_s: float,
        keep: int,
        chunk_retention_days: int | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._dir = backup_dir
        self._interval_s = interval_s
        self._keep = keep
        self._retention = None if chunk_retention_days is None else timedelta(days=chunk_retention_days)
        self._clock = clock
        self._sleep = sleep

    def run_once(self) -> Path | None:
        """One round: the copy, the pruning of old copies and the purge. Returns the new copy, or None when it failed."""
        now = self._clock()
        try:
            copy = self._store.backup(self._dir / f"{BACKUP_PREFIX}{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}.db")
        except Exception as exc:
            # No fresh copy: nothing is purged this round either.
            _log.error("store backup failed: %s", type(exc).__name__)
            return None
        for old in sorted(self._dir.glob(f"{BACKUP_PREFIX}*.db"))[: -self._keep]:
            old.unlink(missing_ok=True)
        if self._retention is not None:
            purged = self._store.purge_chunks(now - self._retention, now)
            if purged:
                _log.info("raw chunks purged for %d processed sessions", len(purged))
        return copy

    async def run(self) -> None:
        while True:
            await self._sleep(self._interval_s)
            self.run_once()
