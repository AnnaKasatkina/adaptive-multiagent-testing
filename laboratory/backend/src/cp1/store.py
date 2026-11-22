import json
import sqlite3
import threading
import zlib
from pathlib import Path
from .recording import canonical


class RunStore:
    def __init__(self, path):
        self.path = Path(path) if path is not None else None
        self._memory = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._memory = sqlite3.connect(":memory:", check_same_thread=False)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript(
                "PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, metadata TEXT NOT NULL); CREATE TABLE IF NOT EXISTS ticks(run_id TEXT,tick INTEGER,data BLOB NOT NULL,PRIMARY KEY(run_id,tick)); CREATE TABLE IF NOT EXISTS observations(run_id TEXT,tick INTEGER,data BLOB NOT NULL,PRIMARY KEY(run_id,tick)); CREATE TABLE IF NOT EXISTS spaces(id TEXT PRIMARY KEY,data TEXT NOT NULL); CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,data TEXT NOT NULL);"
            )

    def connection(self):
        return (
            self._memory
            if self._memory is not None
            else sqlite3.connect(self.path, timeout=30)
        )

    def replace_record(self, metadata, ticks):
        ident = metadata["id"]
        with self.lock, self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO runs VALUES(?,?)", (ident, canonical(metadata))
            )
            db.execute("DELETE FROM ticks WHERE run_id=?", (ident,))
            db.execute("DELETE FROM observations WHERE run_id=?", (ident,))
            db.executemany(
                "INSERT INTO ticks VALUES(?,?,?)",
                [
                    (ident, row["tick"], zlib.compress(canonical(row).encode(), 3))
                    for row in ticks
                ],
            )
            db.executemany(
                "INSERT INTO observations VALUES(?,?,?)",
                [
                    (
                        ident,
                        row["tick"],
                        zlib.compress(canonical(self._observation(row)).encode(), 3),
                    )
                    for row in ticks
                    if self._is_observation(row)
                ],
            )

    def delete(self, ident):
        with self.lock, self.connection() as db:
            db.execute("DELETE FROM ticks WHERE run_id=?", (ident,))
            db.execute("DELETE FROM observations WHERE run_id=?", (ident,))
            db.execute("DELETE FROM runs WHERE id=?", (ident,))

    def comparisons(self):
        with self.lock, self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM comparisons ORDER BY rowid DESC"
                )
            ]

    def create(self, run_id, metadata):
        with self.lock, self.connection() as db:
            db.execute("INSERT INTO runs VALUES(?,?)", (run_id, canonical(metadata)))

    def update(self, run_id, changes):
        with self.lock, self.connection() as db:
            row = db.execute(
                "SELECT metadata FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            data = json.loads(row[0])
            data.update(changes)
            db.execute(
                "UPDATE runs SET metadata=? WHERE id=?", (canonical(data), run_id)
            )

    def append(self, run_id, rec):
        with self.lock, self.connection() as db:
            db.execute(
                "INSERT INTO ticks VALUES(?,?,?)",
                (run_id, rec["tick"], zlib.compress(canonical(rec).encode(), 3)),
            )
            if self._is_observation(rec):
                db.execute(
                    "INSERT INTO observations VALUES(?,?,?)",
                    (
                        run_id,
                        rec["tick"],
                        zlib.compress(canonical(self._observation(rec)).encode(), 3),
                    ),
                )
            row = db.execute(
                "SELECT metadata FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            data = json.loads(row[0])
            data.update(tick=rec["tick"])
            db.execute(
                "UPDATE runs SET metadata=? WHERE id=?", (canonical(data), run_id)
            )

    def ticks(self, run_id):
        with self.lock, self.connection() as db:
            return [
                json.loads(zlib.decompress(r[0]))
                for r in db.execute(
                    "SELECT data FROM ticks WHERE run_id=? ORDER BY tick", (run_id,)
                )
            ]

    def state(self, run_id, tick):
        if self.metadata(run_id).get("configuration", {}).get("schema_version") == 2:
            from .event_engine import expand_trace, display_record

            start = tick - tick % 250
            with self.lock, self.connection() as db:
                records = [
                    json.loads(zlib.decompress(r[0]))
                    for r in db.execute(
                        "SELECT data FROM ticks WHERE run_id=? AND tick>=? AND tick<=? ORDER BY tick",
                        (run_id, start, tick),
                    )
                ]
            if not records or records[-1]["tick"] != tick:
                raise KeyError(tick)
            for reconstructed in expand_trace(records, copy_state=False):
                latest = reconstructed
            return display_record(latest)
        with self.lock, self.connection() as db:
            row = db.execute(
                "SELECT data FROM ticks WHERE run_id=? AND tick=?", (run_id, tick)
            ).fetchone()
            if row is None:
                raise KeyError(tick)
            return json.loads(zlib.decompress(row[0]))

    def metadata(self, run_id):
        with self.lock, self.connection() as db:
            row = db.execute(
                "SELECT metadata FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            return json.loads(row[0])

    @staticmethod
    def _is_observation(row):
        return "time_ms" in row and (
            row["tick"] == 0
            or any(e["kind"] in ("update", "finish") for e in row["events"])
        )

    @staticmethod
    def _observation(row):
        return dict(
            tick=row["tick"],
            time_ms=row["time_ms"],
            time=row["time"],
            z=row["z"],
            **row["metrics"],
        )

    def series(self, run_id):
        with self.lock, self.connection() as db:
            return [
                json.loads(zlib.decompress(r[0]))
                for r in db.execute(
                    "SELECT data FROM observations WHERE run_id=? ORDER BY tick",
                    (run_id,),
                )
            ]

    def list(self):
        with self.lock, self.connection() as db:
            return [
                json.loads(r[0])
                for r in db.execute("SELECT metadata FROM runs ORDER BY rowid DESC")
            ]

    def bundle(self, run_id):
        meta = self.metadata(run_id)
        return {
            "manifest": meta["manifest"],
            "configuration": meta["configuration"],
            "trace": self.ticks(run_id),
            "summary": meta.get("summary", {}),
        }
