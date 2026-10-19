"""Durable *local review* state; NEVER a record of platform actions.

No passwords, Team Codes, raw posts, prompts, model responses, or session tokens
are stored. A reviewed post was selected for local review, NOT replied to.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from typing import Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReadCooldown(RuntimeError):
    def __init__(self, seconds: float):
        self.seconds = max(0., seconds)
        super().__init__("Locally reserved feed cooldown has not elapsed")


class ModelCooldown(ReadCooldown):
    """Local official-model cooldown has not elapsed."""


class ReviewStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.connection = sqlite3.connect(str(self.path), timeout=5)
        self.connection.execute('PRAGMA busy_timeout=5000')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.executescript('''
            CREATE TABLE IF NOT EXISTS reviewed_posts (
                post_id TEXT PRIMARY KEY,
                reviewed_utc TEXT NOT NULL,
                length_chars INTEGER NOT NULL,
                source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_utc TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                observed_count INTEGER NOT NULL,
                eligible_count INTEGER NOT NULL,
                selected_post_id TEXT,
                error_type TEXT
            );
            CREATE TABLE IF NOT EXISTS feed_read_guard (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                next_allowed_epoch REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_read_guard (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                next_allowed_epoch REAL NOT NULL
            );
        ''')
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ReviewStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def is_reviewed(self, post_id: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM reviewed_posts WHERE post_id = ?", (str(post_id),)
        ).fetchone() is not None

    def mark_reviewed(self, post_id: str, length: int, source: str) -> bool:
        """Review acknowledgement only, not publication or execution."""
        with self.connection:
            cursor = self.connection.execute('''
                INSERT OR IGNORE INTO reviewed_posts(post_id,reviewed_utc,length_chars,source)
                VALUES (?,?,?,?)
            ''', (str(post_id), utc_now(), length, source))
        return cursor.rowcount == 1

    def count_reviewed(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM reviewed_posts").fetchone()[0]

    def audit(self, *, source: str, status: str, observed: int = 0,
              eligible: int = 0, selected_id: str | None = None,
              error_type: str | None = None) -> None:
        with self.connection:
            self.connection.execute('''
                INSERT INTO audit(timestamp_utc,source,status,observed_count,
                                  eligible_count,selected_post_id,error_type)
                VALUES (?,?,?,?,?,?,?)
            ''', (utc_now(), source, status, observed, eligible, selected_id, error_type))

    def reserve_feed_read(self, minimum_gap_seconds: float = 65.) -> None:
        """Cross-process conservative cooldown, claimed before network access.

        This covers only THIS local SQLite file. It cannot see other teammates'
        requests or other SDK clients. Server rate limits remain authoritative.
        """
        if minimum_gap_seconds < 0:
            raise ValueError('minimum_gap_seconds must be nonnegative')
        now = self.clock()
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            row = self.connection.execute(
                'SELECT next_allowed_epoch FROM feed_read_guard WHERE singleton=1'
            ).fetchone()
            if row is not None and row[0] > now:
                raise ReadCooldown(row[0] - now)
            self.connection.execute('''
                INSERT INTO feed_read_guard(singleton,next_allowed_epoch)
                VALUES (1,?)
                ON CONFLICT(singleton)
                DO UPDATE SET next_allowed_epoch=excluded.next_allowed_epoch
            ''', (now + minimum_gap_seconds,))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def reserve_model_call(self, minimum_gap_seconds: float = 25.) -> None:
        """Reserve a local official-model attempt across restarts/processes.

        Does NOT see teammates or guarantee team-level rate-limit compliance.
        """
        if minimum_gap_seconds < 0:
            raise ValueError('minimum_gap_seconds must be nonnegative')
        now = self.clock()
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            row = self.connection.execute(
                'SELECT next_allowed_epoch FROM model_read_guard WHERE singleton=1'
            ).fetchone()
            if row is not None and row[0] > now:
                raise ModelCooldown(row[0] - now)
            self.connection.execute(
                'INSERT INTO model_read_guard(singleton,next_allowed_epoch) VALUES (1,?) '
                'ON CONFLICT(singleton) DO UPDATE SET next_allowed_epoch=excluded.next_allowed_epoch',
                (now + minimum_gap_seconds,)
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
