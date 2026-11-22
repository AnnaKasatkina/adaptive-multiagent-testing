"""One isolated live run. The wire protocol is UTF-8 JSON lines."""

import asyncio
import json
import sys
import time
import threading
import traceback
from .event_models import parse_configuration
from .engine import ExecutionEngine


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


async def main(initial):
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def read():
        for line in sys.stdin:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, json.loads(line))
            except Exception:
                break
        try:
            loop.call_soon_threadsafe(queue.put_nowait, {"action": "stop"})
        except RuntimeError:
            pass

    threading.Thread(target=read, daemon=True).start()
    request = initial
    config = parse_configuration(request["configuration"])
    runtime = None
    if request.get("runtime") == "spade":
        from .spade_runtime import SpadeRuntime

        runtime = SpadeRuntime()
    if config.schema_version == 2:
        from .event_engine import EventEngine

        engine = EventEngine(config, request.get("seed", 0), runtime)
    else:
        engine = ExecutionEngine(config, request.get("seed", 0), runtime)

    def complete():
        return (
            engine.finished
            if config.schema_version == 2
            else engine.tick == config.horizon
        )

    compute = 0.0
    started = time.perf_counter()

    async def one(recorded=None):
        nonlocal compute
        begin = time.perf_counter()
        rec = await engine.step(recorded)
        compute += time.perf_counter() - begin
        emit({"kind": "tick", "record": rec})
        return rec

    def status(name):
        r = engine.result(name, request.get("parent"))
        r["manifest"]["id"] = request["id"]
        r["summary"].update(
            execution_seconds=compute, wall_seconds=time.perf_counter() - started
        )
        emit(
            {
                "kind": "status",
                "status": name,
                "manifest": r["manifest"],
                "summary": r["summary"],
            }
        )

    try:
        emit({"kind": "tick", "record": await engine.start()})
        original = request.get("prefix")
        if original:
            if original[0]["state_hash"] != engine.trace[0]["state_hash"]:
                raise ValueError("Исходное состояние не совпадает")
            status("replaying")
            for row in original[1:]:
                rec = await one(row["decisions"])
                if rec["state_hash"] != row["state_hash"]:
                    raise ValueError(f"Расхождение повтора в такт {row['tick']}")
        running = request.get("initial_action") == "start"
        speed = float(request.get("speed", 10))
        if request.get("initial_action") in ("step", "step_update") and not complete():
            await one()
            if (
                request.get("initial_action") == "step_update"
                and config.schema_version == 2
            ):
                while not complete() and not any(
                    e["kind"] == "update" for e in engine.trace[-1]["events"]
                ):
                    await one()
        status("completed" if complete() else "running" if running else "paused")
        virtual_target = getattr(engine, "time_ms", 0)
        while not complete():
            if running:
                try:
                    command = await asyncio.wait_for(
                        queue.get(),
                        timeout=0.05
                        if config.schema_version == 2
                        else 1 / max(speed, 0.25),
                    )
                except TimeoutError:
                    command = {"action": "advance"}
            else:
                command = await queue.get()
            action = command["action"]
            if action == "start":
                running = True
                status("running")
            elif action == "pause":
                running = False
                virtual_target = getattr(engine, "time_ms", 0)
                status("paused")
            elif action == "speed":
                speed = max(0.25, min(120, float(command["value"])))
            elif action == "stop":
                status("stopped")
                break
            elif action in ("step", "step_update", "advance"):
                if config.schema_version == 2 and action == "advance":
                    virtual_target += speed * 50
                    count = 0
                    while (
                        not complete()
                        and engine.heap[0][0] <= virtual_target
                        and count < 500
                    ):
                        await one()
                        count += 1
                else:
                    await one()
                    if action == "step_update" and config.schema_version == 2:
                        while not complete() and not any(
                            e["kind"] == "update" for e in engine.trace[-1]["events"]
                        ):
                            await one()
                if complete():
                    status("completed")
                    break
                if action in ("step", "step_update"):
                    running = False
                    status("paused")
            await asyncio.sleep(0)
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        emit({"kind": "error", "error": str(exc) or type(exc).__name__})
    finally:
        await engine.runtime.close()


if __name__ == "__main__":
    initial = json.loads(sys.stdin.readline())
    if initial.get("runtime") == "spade":
        import spade  # noqa: F401
    asyncio.run(main(initial))
