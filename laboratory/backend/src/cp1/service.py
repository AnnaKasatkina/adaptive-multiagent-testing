from __future__ import annotations
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from math import isfinite
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, UploadFile, Request, HTTPException
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from .models import preset
from .event_models import parse_configuration, event_preset, REGISTRY as EVENT_REGISTRY
from .parameters import registry, materialize
from .recording import pack, unpack, digest, fingerprint
from .library import SessionStore
from .validator import validate_trace
from .algorithms import model_version, metadata as algorithm_metadata

ROOT = Path(__file__).resolve().parents[3]


class RunManager:
    def __init__(self, data):
        self.data = Path(data)
        self.store = SessionStore(self.data / "laboratory.sqlite")
        self.workers = {}
        self.tasks = set()
        for meta in self.store.list():
            if meta["status"] in ("running", "paused", "preparing", "replaying"):
                self.store.update(meta["id"], {"status": "interrupted"})

    async def create(
        self,
        config,
        seed=0,
        runtime="core",
        prefix=None,
        parent=None,
        initial_action="pause",
        speed=10,
    ):
        config = parse_configuration(config)
        if config.algorithm == "homework-original":
            raise ValueError("Для новых экспериментов используйте Локальный алгоритм")
        if runtime not in ("core", "spade"):
            raise ValueError("Неизвестный способ исполнения")
        if not isinstance(seed, int) or not 0 <= seed < 2**63:
            raise ValueError("Seed должен быть целым от 0 до 2^63-1")
        if initial_action not in ("pause", "start", "step", "step_update"):
            raise ValueError("Неизвестное начальное действие")
        if (
            not isinstance(speed, (int, float))
            or not isfinite(speed)
            or not 0.25 <= speed <= 120
        ):
            raise ValueError("Темп должен быть числом от 0.25 до 120")
        if runtime == "spade":
            from .spade_runtime import ensure_server

            await asyncio.to_thread(ensure_server)
        ident = uuid.uuid4().hex
        manifest = {
            "id": ident,
            "schema_version": config.schema_version,
            "model_version": model_version(config),
            "algorithm": algorithm_metadata(config),
            "runtime": runtime,
            "seed": seed,
            "parent": parent,
            "configuration_hash": digest(config.model_dump()),
            "environment": fingerprint(),
        }
        meta = {
            "id": ident,
            "name": config.name,
            "status": "preparing",
            "tick": 0,
            "runtime": runtime,
            "seed": seed,
            "created": time.time(),
            "configuration": config.model_dump(),
            "parent": parent,
            "manifest": manifest,
            "summary": {},
            "error": None,
            "saved": False,
            "initial_action": initial_action,
        }
        self.store.create(ident, meta)
        env = {**os.environ, "PYTHONUTF8": "1", "CP1_DATA": str(self.data.resolve())}
        if runtime == "spade":
            env["CP1_XMPP_EXTERNAL"] = "1"
        log = open(self.data / f"worker-{ident}.log", "ab")
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "cp1.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            encoding="utf-8",
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        log.close()
        self.workers[ident] = process
        init = {
            "id": ident,
            "configuration": config.model_dump(),
            "seed": seed,
            "runtime": runtime,
            "prefix": prefix,
            "parent": parent,
        }
        init.update(initial_action=initial_action, speed=speed)
        await asyncio.to_thread(self._write, process, init)
        task = asyncio.create_task(self.listen(ident, process))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return meta

    @staticmethod
    def _write(process, value):
        process.stdin.write(
            json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n"
        )
        process.stdin.flush()

    async def listen(self, ident, process):
        try:
            while line := await asyncio.to_thread(process.stdout.readline):
                item = json.loads(line)
                if item["kind"] == "tick":
                    row = item["record"]
                    self.store.append(ident, row)
                    self.store.update(
                        ident,
                        {
                            "tick": row["tick"],
                            "time_ms": row.get("time_ms"),
                            "summary": {
                                **row["metrics"],
                                "time_ms": row.get("time_ms"),
                            },
                        },
                    )
                elif item["kind"] == "status":
                    self.store.update(
                        ident, {k: item[k] for k in ("status", "manifest", "summary")}
                    )
                else:
                    self.store.update(
                        ident,
                        {
                            "status": "failed",
                            "error": item.get("error", "Ошибка исполнителя"),
                        },
                    )
            code = await asyncio.to_thread(process.wait)
            meta = self.store.metadata(ident)
            if meta["status"] not in ("completed", "stopped", "failed"):
                self.store.update(
                    ident,
                    {"status": "failed", "error": f"Исполнитель завершился: {code}"},
                )
        except Exception as exc:
            self.store.update(ident, {"status": "failed", "error": str(exc)})
        finally:
            if process.stdin:
                process.stdin.close()
            self.workers.pop(ident, None)

    async def command(self, ident, value):
        meta = self.store.metadata(ident)
        action = value.get("action")
        if action not in ("start", "pause", "step", "step_update", "stop", "speed"):
            raise ValueError("Неизвестная команда")
        if ident not in self.workers or meta["status"] in (
            "completed",
            "stopped",
            "failed",
            "interrupted",
        ):
            raise ValueError("Выполнение завершено; используйте повтор или продолжение")
        if action in ("start", "step", "step_update") and meta["status"] not in (
            "paused",
            "running",
        ):
            raise ValueError("Дождитесь готовности исполнителя")
        await asyncio.to_thread(self._write, self.workers[ident], value)
        return {"accepted": True}

    async def close(self):
        for process in list(self.workers.values()):
            try:
                self._write(process, {"action": "stop"})
            except Exception:
                pass
        if self.tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*list(self.tasks)), 10)
            except TimeoutError:
                for p in list(self.workers.values()):
                    if p.poll() is None:
                        p.terminate()
        from .spade_runtime import stop_server

        stop_server()


def create_app(data=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.manager = RunManager(
            data or os.environ.get("CP1_DATA", ROOT.parent / "artifacts/library")
        )
        yield
        experiment = getattr(app.state, "experiment_process", None)
        if experiment is not None and experiment.poll() is None:
            experiment.terminate()
            await asyncio.to_thread(experiment.wait)
        await app.state.manager.close()

    app = FastAPI(title="RAPS Laboratory", version="0.1.0", lifespan=lifespan)

    def manager():
        return app.state.manager

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def key_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=404, content={"detail": "Запись не найдена"})

    @app.get("/api/health")
    def health():
        return {"ready": True, "environment": fingerprint()}

    @app.get("/api/parameters")
    def parameters(version: int = 2):
        return EVENT_REGISTRY if version == 2 else registry()

    @app.get("/api/presets")
    def presets(n: int = 6, version: int = 2):
        return [
            {
                "id": kind,
                "configuration": (
                    event_preset(kind, n) if version == 2 else preset(kind, n)
                ).model_dump(),
            }
            for kind in (
                "bridge",
                "ring",
                "directed",
                "path",
                "star",
                "complete",
                "random",
            )
        ]

    @app.post("/api/configurations/validate")
    def validate(value: dict):
        configuration = parse_configuration(value).model_dump()
        if configuration.get("algorithm") == "homework-original":
            raise ValueError("Для новых экспериментов используйте Локальный алгоритм")
        return {
            "valid": True,
            "configuration": configuration,
            "configuration_hash": digest(configuration),
            "model_version": model_version(parse_configuration(configuration)),
        }

    @app.get("/api/configurations")
    def configurations():
        with manager().store.library.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS configurations(id TEXT PRIMARY KEY,data TEXT NOT NULL)"
            )
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM configurations ORDER BY rowid DESC"
                )
            ]

    @app.post("/api/configurations")
    def save_configuration(value: dict):
        c = parse_configuration(value["configuration"])
        name = manager().store._name(value.get("name", c.name))
        c.name = name
        data = c.model_dump()
        ident = digest(data)
        item = dict(id=ident, name=name, configuration=data)
        with manager().store.library.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS configurations(id TEXT PRIMARY KEY,data TEXT NOT NULL)"
            )
            db.execute(
                "INSERT OR REPLACE INTO configurations VALUES(?,?)",
                (ident, json.dumps(item, ensure_ascii=False)),
            )
        return item

    @app.post("/api/search-spaces/materialize")
    def apply_space(value: dict):
        result = materialize(
            value["configuration"], value.get("values", {}), value["spec"]
        )
        return {
            "configuration": result.model_dump(),
            "configuration_hash": digest(result.model_dump()),
        }

    @app.post("/api/search-spaces")
    def save_space(value: dict):
        spec = value["spec"]
        materialize(spec["base_configuration"], {}, spec)
        ident = digest(spec)
        with manager().store.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO spaces VALUES(?,?)",
                (ident, json.dumps(spec, ensure_ascii=False)),
            )
        return {"id": ident, "spec": spec}

    @app.get("/api/search-spaces")
    def spaces():
        with manager().store.connection() as db:
            return [
                {"id": r[0], "spec": json.loads(r[1])}
                for r in db.execute("SELECT id,data FROM spaces")
            ]

    @app.get("/api/runs")
    def runs():
        return manager().store.list()

    @app.post("/api/runs")
    async def create(value: dict):
        return await manager().create(
            value["configuration"],
            value.get("seed", 0),
            value.get("runtime", "core"),
            initial_action=value.get("initial_action", "pause"),
            speed=value.get("speed", 10),
        )

    @app.get("/api/runs/{ident}")
    def metadata(ident: str):
        return manager().store.metadata(ident)

    @app.post("/api/runs/{ident}/save")
    def save_run(ident: str, value: dict):
        return manager().store.save_run(ident, value.get("name"))

    @app.post("/api/runs/{ident}/unsave")
    def unsave_run(ident: str):
        return manager().store.unsave_run(ident)

    @app.get("/api/comparisons")
    def comparisons():
        return manager().store.comparisons()

    @app.post("/api/comparisons")
    def save_comparison(value: dict):
        return manager().store.save_comparison(value)

    @app.post("/api/comparisons/{ident}/remove")
    def remove_comparison(ident: str):
        manager().store.remove_comparison(ident)
        return {"removed": True}

    @app.post("/api/runs/{ident}/commands")
    async def command(ident: str, value: dict):
        return await manager().command(ident, value)

    @app.get("/api/runs/{ident}/state")
    def state(
        ident: str,
        tick: int | None = None,
        event_id: int | None = None,
        time_ms: int | None = None,
    ):
        cursor = event_id if event_id is not None else tick
        if cursor is None and time_ms is not None:
            rows = manager().store.ticks(ident)
            cursor = max(
                (r["tick"] for r in rows if r.get("time_ms", r["tick"]) <= time_ms),
                default=0,
            )
        if cursor is None:
            raise ValueError("Укажите event_id или time_ms")
        return manager().store.state(ident, cursor)

    @app.get("/api/runs/{ident}/series")
    def series(ident: str):
        if manager().store.metadata(ident)["configuration"].get("schema_version") == 2:
            return manager().store.series(ident)
        return [
            {
                "tick": r["tick"],
                **r["metrics"],
                "time_ms": r.get("time_ms"),
                "time": r.get("time", r["tick"]),
                "z": r["z"]
                if "z" in r
                else {k: s["z"] for k, s in r["state"]["agents"].items()},
            }
            for r in manager().store.ticks(ident)
            if "time_ms" not in r
            or r["tick"] == 0
            or any(e["kind"] in ("update", "finish") for e in r["events"])
        ]

    @app.get("/api/runs/{ident}/events")
    async def events(ident: str, request: Request, after: int = -1):
        manager().store.metadata(ident)

        async def stream():
            last = after
            status = None
            while not await request.is_disconnected():
                meta = manager().store.metadata(ident)
                if meta["tick"] > last:
                    # One compact notification; the history endpoint supplies all intermediate points.
                    last = meta["tick"]
                    yield f"id: {last}\ndata: {json.dumps({'kind': 'tick', 'tick': last})}\n\n"
                if meta["status"] != status:
                    status = meta["status"]
                    yield f"data: {json.dumps({'kind': 'status', 'status': status, 'error': meta.get('error')})}\n\n"
                await asyncio.sleep(0.12)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/runs/{ident}/fork")
    async def fork(ident: str, value: dict):
        meta = manager().store.metadata(ident)
        n = int(
            value["after_event_id"]
            if "after_event_id" in value
            else value["after_tick"]
        )
        if not 0 <= n <= meta["tick"]:
            raise ValueError("Граница префикса вне сохранённого исполнения")
        prefix = manager().store.ticks(ident)[: n + 1]
        return await manager().create(
            meta["configuration"],
            value.get("seed", 1000),
            value.get("runtime", meta["runtime"]),
            prefix,
            {"run_id": ident, "after_tick": n, "kind": "fork"},
        )

    @app.post("/api/runs/{ident}/replay")
    async def replay(ident: str, value: dict):
        meta = manager().store.metadata(ident)
        prefix = manager().store.ticks(ident)
        if not prefix:
            raise ValueError("Запись ещё не готова")
        return await manager().create(
            meta["configuration"],
            meta["seed"],
            value.get("runtime", meta["runtime"]),
            prefix,
            {"run_id": ident, "after_tick": meta["tick"], "kind": "replay"},
        )

    @app.get("/api/runs/{ident}/export")
    def export(ident: str):
        run = manager().store.bundle(ident)
        if not run["trace"]:
            raise ValueError("Нет сохранённых тактов")
        return Response(
            pack(run),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="run-{ident[:8]}.zip"'
            },
        )

    @app.post("/api/runs/import")
    async def import_run(file: UploadFile):
        run = unpack(await file.read())
        if run["configuration"].get("algorithm") == "homework-original":
            raise ValueError(
                "Архивный обработчик не используется; выберите Локальный алгоритм"
            )
        audit = validate_trace(run)
        if not audit["valid"]:
            raise ValueError(str(audit["errors"]))
        ident = uuid.uuid4().hex
        run["manifest"]["id"] = ident
        manager().store.create(
            ident,
            {
                "id": ident,
                "name": run["configuration"]["name"],
                "status": "completed" if audit["complete"] else "interrupted",
                "tick": run["trace"][-1]["tick"],
                "configuration": run["configuration"],
                "manifest": run["manifest"],
                "summary": run["summary"],
                "seed": run["manifest"]["seed"],
                "runtime": run["manifest"]["runtime"],
                "parent": run["manifest"].get("parent"),
                "created": time.time(),
                "saved": False,
            },
        )
        for row in run["trace"]:
            manager().store.append(ident, row)
        return manager().store.metadata(ident)

    @app.get("/api/runs/{ident}/verify")
    def verify(ident: str):
        return validate_trace(manager().store.bundle(ident))

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="interface")
    return app
