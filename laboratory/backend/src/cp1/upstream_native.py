"""Run the actual pinned SPADE program with only loopback transport/instrumentation.

No equation, receive window, weight, startup delay, round limit, or source file is
changed. This wall-clock observation is NOT a deterministic virtual-time trace.
"""

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "vendor/mean_consensus_spade"
OUTPUT = ROOT.parent / "artifacts/laboratory/upstream-native"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


async def observe():
    from .spade_runtime import ensure_server, stop_server
    from spade.container import Container

    provenance = json.loads((SOURCE / "UPSTREAM.json").read_text(encoding="utf-8"))
    for name, digest in provenance["files"].items():
        if hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Исходник изменён: " + name)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    port = await asyncio.to_thread(ensure_server)
    Container().reset()
    original = load("agent", SOURCE / "agent.py")
    program = load("upstream_run", SOURCE / "run.py")
    agents = []
    events = []
    started = time.perf_counter()

    def event(kind, agent, **fields):
        events.append(
            dict(
                kind=kind,
                seconds=time.perf_counter() - started,
                agent=str(agent.jid),
                **fields,
            )
        )

    class ObservedAgent(original.ConsensusAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, port=port, **kwargs)
            agents.append(self)

        @property
        def value(self):
            return self._observed_value

        @value.setter
        def value(self, value):
            initial = not hasattr(self, "_observed_value")
            self._observed_value = value
            event("initial" if initial else "update", self, value=value)

        async def _async_connect(self):
            connect = self.client.connect
            self.client.connect = lambda host=None, port=None: connect(
                host="127.0.0.1", port=port or self.xmpp_port
            )
            try:
                return await super()._async_connect()
            finally:
                self.client.connect = connect

        async def start(self, *args, **kwargs):
            from spade.agent import DisconnectedException

            for attempt in range(1, 4):
                try:
                    return await asyncio.wait_for(super().start(*args, **kwargs), 20)
                except (DisconnectedException, TimeoutError) as exc:
                    event("connection_retry", self, attempt=attempt, error=str(exc))
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                    if attempt == 3:
                        raise
                    Container().register(self)
                    await asyncio.sleep(0.2)

        async def setup(self):
            await super().setup()
            for behaviour in self.behaviours:

                async def send(message, _send=behaviour.send):
                    event("send", self, to=str(message.to), body=message.body)
                    return await _send(message)

                async def receive(timeout=None, _receive=behaviour.receive):
                    message = await _receive(timeout=timeout)
                    if message is not None:
                        event(
                            "receive",
                            self,
                            sender=str(message.sender),
                            body=message.body,
                        )
                    return message

                behaviour.send = send
                behaviour.receive = receive

    program.ConsensusAgent = ObservedAgent
    program.CONFIG = str(SOURCE / "graph.json")
    try:
        with (
            (OUTPUT / "stdout.log").open("w", encoding="utf-8") as stream,
            contextlib.redirect_stdout(stream),
        ):
            await asyncio.wait_for(program.main(), 120)
        config = json.loads((SOURCE / "graph.json").read_text(encoding="utf-8"))
        target = sum(config["initial_values"]) / config["n"]
        values = [agent.value for agent in agents]
        initial_range = max(config["initial_values"]) - min(config["initial_values"])
        result = dict(
            status="completed",
            execution="unmodified-upstream-behaviour; observed wall-clock",
            source=provenance,
            configuration=config,
            elapsed_seconds=time.perf_counter() - started,
            target_mean=target,
            values=values,
            D=max(values) - min(values),
            Q=(max(values) - min(values)) / initial_range,
            error_mean=max(abs(value - target) for value in values),
            mae=sum(abs(value - target) for value in values) / len(values),
            all_finished_rounds=all(
                getattr(agent, "_finished_rounds", False) for agent in agents
            ),
            local_updates=[
                sum(
                    e["kind"] == "update" and e["agent"] == str(agent.jid)
                    for e in events
                )
                for agent in agents
            ],
            transport="Owned loopback XMPP; same SPADE behaviour code; timestamp logging only",
        )
        (OUTPUT / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUTPUT / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except BaseException as exc:
        (OUTPUT / "failed-observation.json").write_text(
            json.dumps(
                dict(
                    status="failed",
                    error=str(exc),
                    source=provenance,
                    elapsed_seconds=time.perf_counter() - started,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (OUTPUT / "failed-events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8",
        )
        raise
    finally:
        for agent in agents:
            if agent.is_alive():
                await agent.stop()
        stop_server()


if __name__ == "__main__":
    import spade  # noqa: F401 -- initialize SPADE's supported Windows loop policy

    asyncio.run(observe())
