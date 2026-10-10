"""
turso_db.py
Lets SmartLab keep its data in a Turso cloud database instead of a local file.

How it works:
- Turso's Python library (libsql) keeps a local copy ("embedded replica") of
  the cloud database and sends every change to Turso, so the data survives
  even when the server (e.g. Render free) restarts and wipes its disk.
- libsql returns plain tuples, but SmartLab's code reads rows by column name
  (row["status"]), like sqlite3.Row. The small wrapper classes below make
  libsql look like sqlite3, so none of the queries in database.py change.
"""

import os
import time

TURSO_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

_SYNC_EVERY_SECONDS = 5
_last_sync = 0.0


def is_enabled():
    """True when both Turso settings are present, so SmartLab should use Turso."""
    return bool(TURSO_URL and TURSO_TOKEN)


class Row:
    """Acts like sqlite3.Row: row[0], row["name"], dict(row), row.keys()."""

    __slots__ = ("_cols", "_vals", "_index")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = tuple(vals)
        self._index = {c.lower(): i for i, c in enumerate(cols)}

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        return self._vals[self._index[key.lower()]]

    def keys(self):
        return list(self._cols)

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __repr__(self):
        return f"Row({dict(zip(self._cols, self._vals))})"


class Cursor:
    def __init__(self, raw):
        self._raw = raw

    def _cols(self):
        desc = self._raw.description or ()
        return [d[0] for d in desc]

    def execute(self, sql, params=()):
        self._raw.execute(sql, tuple(params))
        return self

    def executemany(self, sql, seq):
        for params in seq:
            self._raw.execute(sql, tuple(params))
        return self

    def fetchone(self):
        r = self._raw.fetchone()
        return None if r is None else Row(self._cols(), r)

    def fetchall(self):
        cols = self._cols()
        return [Row(cols, r) for r in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def lastrowid(self):
        return self._raw.lastrowid

    @property
    def rowcount(self):
        return self._raw.rowcount

    @property
    def description(self):
        return self._raw.description

    def close(self):
        pass


class Connection:
    def __init__(self, raw, sync=True):
        self._raw = raw
        self._sync = sync

    def cursor(self):
        return Cursor(self._raw.cursor())

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def executemany(self, sql, seq):
        return self.cursor().executemany(sql, seq)

    def commit(self):
        self._raw.commit()
        if self._sync:
            _do_sync(self._raw, force=True)

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def _do_sync(raw, force=False):
    """Pull the latest data from Turso (at most every few seconds unless forced)."""
    global _last_sync
    now = time.time()
    if force or now - _last_sync >= _SYNC_EVERY_SECONDS:
        raw.sync()
        _last_sync = now


def connect(local_path):
    """Open the local replica of the Turso database and bring it up to date."""
    import libsql  # Turso's Python library (only needed when Turso is turned on)

    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    raw = libsql.connect(local_path, sync_url=TURSO_URL, auth_token=TURSO_TOKEN)
    _do_sync(raw)
    return Connection(raw)
