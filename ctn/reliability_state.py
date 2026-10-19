"""v0.5.2 local state. No platform publication state or raw content is stored."""
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3

from .review_state import ReviewStore, ReadCooldown


class WorkerBusy(RuntimeError):
    pass


class ServiceBlocked(RuntimeError):
    pass


class CircuitOpen(ReadCooldown):
    pass


class ReliabilityStore(ReviewStore):
    def __init__(self, path, **kwargs):
        # Resolve aliases so symlinked DB paths share a single lock.
        super().__init__(Path(path).resolve(), **kwargs)
        self.connection.executescript('''
          CREATE TABLE IF NOT EXISTS candidate_state (
            post_key TEXT PRIMARY KEY, status TEXT NOT NULL,
            classification INTEGER, proposed INTEGER NOT NULL DEFAULT 0,
            reviewed INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0, length_chars INTEGER NOT NULL,
            error_code TEXT
          );
          CREATE TABLE IF NOT EXISTS service_guard (
            service TEXT PRIMARY KEY, next_at REAL NOT NULL DEFAULT 0,
            failures INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0
          );
        ''')
        self.connection.commit()

    @contextmanager
    def worker_lock(self):
        """OS releases lock on process death. Never expire a live model's lock.

        All v052 workers using the same DB serialize full cycles; no SQLite
        transaction is held across networking. Independent DBs are NOT coordinated.
        """
        lock_path = self.path.with_name(self.path.name + '.worker.lock')
        with lock_path.open('a+b') as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                raise WorkerBusy('Another local worker owns this database') from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def guard(self, service):
        row = self.connection.execute(
            'SELECT next_at,blocked,failures FROM service_guard WHERE service=?', (service,)).fetchone()
        if row:
            if row[1]:
                raise ServiceBlocked('Service requires operator review')
            if row[0] > self.clock():
                raise (CircuitOpen if row[2] >= 3 else ReadCooldown)(row[0] - self.clock())

    def defer(self, service, seconds=0., *, failure=False, blocked=False):
        """Extend, never shorten, authoritative/local cooldowns. No retries here."""
        with self.connection:
            self.connection.execute('INSERT OR IGNORE INTO service_guard(service) VALUES (?)', (service,))
            row = self.connection.execute('SELECT failures FROM service_guard WHERE service=?', (service,)).fetchone()
            failures = row[0] + int(failure)
            wait = max(seconds, 300. if failures >= 3 else 0.)
            self.connection.execute('''UPDATE service_guard SET next_at=MAX(next_at,?),
                failures=?,blocked=MAX(blocked,?) WHERE service=?''',
                (self.clock() + wait, failures, int(blocked), service))

    def success(self, service):
        with self.connection:
            self.connection.execute('UPDATE service_guard SET failures=0 WHERE service=?', (service,))

    def claim(self, ranked, namespace):
        """Called under worker_lock, including recovery after a killed worker."""
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            for post in ranked:
                key = f'{namespace}:{post.id}'
                if self.is_reviewed(key):
                    continue
                row = self.connection.execute(
                    'SELECT status,retry_at,attempts FROM candidate_state WHERE post_key=?', (key,)).fetchone()
                if row and (row[0] in ('reviewed', 'classified', 'proposed', 'failed_terminal')
                            or row[1] > self.clock() or row[2] >= 3):
                    continue
                self.connection.execute('''INSERT INTO candidate_state(post_key,status,length_chars,attempts)
                    VALUES (?,'processing',?,1) ON CONFLICT(post_key) DO UPDATE
                    SET status='processing',attempts=attempts+1''', (key, len(post.text)))
                self.connection.commit()
                return post, key
            self.connection.commit()
            return None, None
        except BaseException:
            self.connection.rollback()
            raise

    def complete(self, key, *, classification, proposed, length, source):
        with self.connection:
            self.connection.execute('''UPDATE candidate_state SET status=?,classification=?,
                proposed=?,reviewed=1,error_code=NULL WHERE post_key=?''',
                ('proposed' if proposed else ('classified' if classification is not None else 'reviewed'),
                 classification, int(proposed), key))
            self.connection.execute('''INSERT OR IGNORE INTO reviewed_posts
                VALUES (?,datetime('now'),?,?)''', (key, length, source))

    def fail_candidate(self, key, code, *, delay=60., terminal=False):
        with self.connection:
            self.connection.execute('''UPDATE candidate_state SET status=CASE WHEN attempts>=3
                THEN 'failed_terminal' ELSE ? END,error_code=?,retry_at=?
                WHERE post_key=?''', ('failed_terminal' if terminal else 'failed_retryable',
                                     code, self.clock() + delay, key))

    def release(self, key, code, delay=0.):
        # No external attempt occurred; waiting/STOP must not exhaust attempts.
        with self.connection:
            self.connection.execute('''UPDATE candidate_state SET status='failed_retryable',
                error_code=?,retry_at=?,attempts=MAX(0,attempts-1) WHERE post_key=?''',
                (code, self.clock() + delay, key))

    def record_event(self, output):
        # SQLite is the authoritative structured audit; JSONL is a local export.
        import json
        with self.connection:
            self.connection.execute('CREATE TABLE IF NOT EXISTS reliability_audit '
                                    '(event_id INTEGER PRIMARY KEY, event_json TEXT NOT NULL)')
            self.connection.execute('INSERT INTO reliability_audit(event_json) VALUES (?)',
                                    (json.dumps(output, ensure_ascii=True),))
