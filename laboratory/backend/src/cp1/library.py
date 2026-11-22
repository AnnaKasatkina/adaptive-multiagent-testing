"""Working traces are in memory. Only explicit saves reach the disk library."""

import sqlite3
import time

from .recording import canonical, digest
from .store import RunStore


class SessionStore(RunStore):
    def __init__(self, path):
        super().__init__(None)
        self.library = RunStore(path)
        self.retained = set()
        self._archive_legacy()
        references = self._references()
        for metadata in self.library.list():
            if metadata.get("saved") or metadata["id"] in references:
                self.replace_record(metadata, self.library.ticks(metadata["id"]))
                self.retained.add(metadata["id"])

    def _archive_legacy(self):
        old = [r for r in self.library.list() if "saved" not in r]
        if not old:
            return
        archive = self.library.path.parent / "archive"
        archive.mkdir(exist_ok=True)
        target = archive / f"automatic-history-{time.time_ns()}.sqlite"
        with self.library.connection() as source, sqlite3.connect(target) as backup:
            source.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Не удалось проверить резервную копию прежней истории")
        for record in old:
            self.library.delete(record["id"])

    def update(self, ident, changes):
        with self.lock:
            super().update(ident, changes)
            if ident in self.retained:
                self.library.update(ident, changes)

    def create(self, ident, metadata):
        super().create(ident, {"saved": False, **metadata})

    def append(self, ident, record):
        with self.lock:
            super().append(ident, record)
            if ident in self.retained:
                self.library.append(ident, record)

    def _persist(self, ident):
        metadata = self.metadata(ident)
        if not self.ticks(ident):
            raise ValueError("Сначала выполните хотя бы начальный такт")
        self.library.replace_record(metadata, self.ticks(ident))
        self.retained.add(ident)

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("Название должно содержать от 1 до 120 символов")
        return name.strip()

    def save_run(self, ident, name):
        name = self._name(name)
        with self.lock:
            if not self.ticks(ident):
                raise ValueError("Запись ещё не готова")
            self.update(ident, {"name": name, "saved": True})
            self._persist(ident)
            return self.metadata(ident)

    def _references(self):
        return {ident for c in self.library.comparisons() for ident in c["run_ids"]}

    def _release(self, ident):
        if ident not in self._references() and not self.metadata(ident).get("saved"):
            self.library.delete(ident)
            self.retained.discard(ident)

    def unsave_run(self, ident):
        with self.lock:
            self.update(ident, {"saved": False})
            self._release(ident)
            return self.metadata(ident)

    def comparisons(self):
        return self.library.comparisons()

    def save_comparison(self, values):
        name = self._name(values.get("name"))
        ids = values.get("run_ids")
        metric = values.get("metric", "error_mean")
        if (
            not isinstance(ids, list)
            or len(ids) != 2
            or not all(isinstance(i, str) for i in ids)
            or len(set(ids)) != 2
        ):
            raise ValueError("Для сравнения нужны два разных эксперимента")
        if metric not in ("Q", "E", "error_mean"):
            raise ValueError("Неизвестный показатель сравнения")
        with self.lock:
            versions = {
                self.metadata(i)["configuration"].get("schema_version", 1) for i in ids
            }
            if len(versions) > 1:
                raise ValueError(
                    "Разные модели времени: такты и секунды нельзя накладывать на одну ось"
                )
            for ident in ids:
                self.metadata(ident)
                if not self.ticks(ident):
                    raise ValueError("Одна из записей ещё не готова")
            for ident in ids:
                self._persist(ident)
            ident = digest({"run_ids": sorted(ids), "metric": metric})[:32]
            comparison = dict(
                id=ident, name=name, run_ids=ids, metric=metric, created=time.time()
            )
            with self.library.connection() as db:
                db.execute(
                    "INSERT OR REPLACE INTO comparisons VALUES(?,?)",
                    (ident, canonical(comparison)),
                )
            return comparison

    def remove_comparison(self, ident):
        with self.lock:
            comparison = next((c for c in self.comparisons() if c["id"] == ident), None)
            if comparison is None:
                raise KeyError(ident)
            with self.library.connection() as db:
                db.execute("DELETE FROM comparisons WHERE id=?", (ident,))
            for run_id in comparison["run_ids"]:
                self._release(run_id)
