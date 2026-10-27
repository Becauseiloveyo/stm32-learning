"""Host-only transactional stores. Missing/corrupt files never auto-provision."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3


class ContractError(ValueError):
    pass


class InjectedCrash(RuntimeError):
    pass


def fail_at(requested, point):
    if requested == point:
        raise InjectedCrash(point)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path, *, create=False, schema=""):
        self.path = Path(path)
        if create:
            self.path.touch(exist_ok=False)
        elif not self.path.is_file():
            raise ContractError("missing storage: explicit provisioning required")
        try:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            if not create and (self.db.execute("PRAGMA user_version").fetchone()[0] != 7
                               or self.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"):
                raise ContractError("unrecognized or corrupt storage")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            if create:
                self.db.executescript(schema + "\nPRAGMA user_version=7;")
        except (sqlite3.Error, ContractError) as exc:
            if hasattr(self, "db"):
                self.db.close()
            raise ContractError("storage unavailable/corrupt; do not silently reset") from exc

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def inspect(self):
        names = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: [dict(row) for row in self.db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')] for name in names}
