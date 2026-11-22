import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cp1.engine import execute
from cp1.homework import HomeworkState, ValueMessage, activate
from cp1.models import Configuration, preset
from cp1.recording import pack, unpack
from cp1.validator import validate_trace

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fresh_spade_service(request, monkeypatch):
    if request.node.get_closest_marker('spade') is None:
        yield
        return
    import socket
    from cp1.spade_runtime import stop_server
    stop_server()
    for _ in range(100):
        with socket.socket() as a, socket.socket() as b:
            a.bind(('127.0.0.1',0)); port=a.getsockname()[1]
            if port==65535: continue
            try: b.bind(('127.0.0.1',port+1))
            except OSError: continue
            break
    else: raise RuntimeError('Нет свободной пары портов XMPP')
    monkeypatch.setenv('CP1_XMPP_PORT',str(port))
    try: yield
    finally: stop_server()


def test_upstream_files_are_pinned_and_unmodified():
    path = ROOT / "vendor/mean_consensus_spade"
    data = json.loads((path / "UPSTREAM.json").read_text(encoding="utf-8"))
    assert data["commit"] == "a776db124ace86059a77bc640c5e7a606b97bbb5"
    for name, sha in data["files"].items():
        assert hashlib.sha256((path / name).read_bytes()).hexdigest() == sha


@pytest.mark.parametrize("compatible", [False, True])
def test_rule_matches_real_upstream_behaviour(compatible):
    # Execute the upstream run() itself, with controlled I/O and one completed
    # round. The compatibility probe changes only the missing metadata access.
    async def run():
        from spade.message import Message

        path = ROOT / "vendor/mean_consensus_spade/agent.py"
        spec = importlib.util.spec_from_file_location("tested_upstream_agent", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class CompatibleMessage(Message):
            @property
            def metadata(self):
                return self._metadata

        cls = CompatibleMessage if compatible else Message
        messages = [cls(body=str(v), metadata={"type": "value"}) for v in (20, 30, 40)]
        namespace = SimpleNamespace(
            value=10.0,
            weight=0.0001,
            neighbors=["a02@localhost", "a03@localhost"],
            round_timeout=1,
            rounds=1,
            start_delay=0,
            shutdown=True,
        )
        behaviour = module.ConsensusAgent.ConsensusBehav()
        behaviour.agent = namespace
        sent = []

        async def send(message):
            sent.append(message.body)

        async def receive(timeout=None):
            return messages.pop(0) if messages else None

        behaviour.send = send
        behaviour.receive = receive
        await behaviour.on_start()
        await behaviour.run()
        assert behaviour.weight == 1 / 7  # agent.weight is actually ignored.
        state = HomeworkState(10.0, accepts_values=compatible)
        outgoing, applied = activate(
            state,
            "a01",
            ["a02", "a03"],
            [
                ValueMessage(str(i), "a02", "a01", 1, 1, float(v))
                for i, v in enumerate((20, 30, 40))
            ],
            1,
        )
        assert namespace.value == state.value
        assert sent == [str(m.value) for m in outgoing]
        assert applied == (3 if compatible else 0)
        assert getattr(namespace, "_in_messages", 0) == applied

    asyncio.run(run())


@pytest.mark.parametrize("algorithm", ["homework-original", "homework"])
def test_homework_replay_and_fork(algorithm):
    async def run():
        c = preset()
        c.algorithm = algorithm
        c.horizon = 30
        source = await execute(c, 11)
        assert validate_trace(source)["valid"]
        imported = unpack(pack(source))
        assert imported["manifest"]["algorithm"]["id"] == algorithm
        repeated = await execute(c, 999, replay=source["trace"])
        assert [r["state_hash"] for r in repeated["trace"]] == [
            r["state_hash"] for r in source["trace"]
        ]
        branch = await execute(c, 1001, replay=source["trace"], fork_tick=12)
        assert [r["state_hash"] for r in branch["trace"][:13]] == [
            r["state_hash"] for r in source["trace"][:13]
        ]

    asyncio.run(run())


def test_legacy_raps_config_keeps_its_serialized_contract():
    c = preset()
    assert c.algorithm == "raps" and "algorithm" not in c.model_dump()
    assert Configuration.model_validate(c.model_dump()).algorithm == "raps"


def test_wrong_mean_and_instability_are_findings_not_invalid_schedules():
    async def run():
        c = preset("directed", 3)
        c.edges.append(type(c.edges[0])(source="a01", target="a03"))
        for n, value in zip(c.nodes, [0, 0, 1]):
            n.initial = value
        c.defaults.update(activation_probability=1, delay_min=1, delay_max=1)
        c.algorithm = "homework"
        r = await execute(c, 0)
        assert r["summary"]["Q"] < 1e-12 and r["summary"]["error_mean"] > 0.05
        c = preset("complete", 20)
        c.horizon = 40
        c.algorithm = "homework"
        c.defaults.update(activation_probability=1, delay_min=1, delay_max=1)
        r = await execute(c, 0)
        assert r["summary"]["range_violations"] > 0 and r["summary"]["Q"] > 1000
        assert validate_trace(r)["valid"]

    asyncio.run(run())


@pytest.mark.spade
@pytest.mark.parametrize("algorithm", ["homework-original", "homework"])
def test_homework_real_spade_matches_core(algorithm):
    async def run():
        from cp1.spade_runtime import SpadeRuntime

        c = preset("ring", 3)
        c.algorithm = algorithm
        c.horizon = 15
        core = await execute(c, 4)
        runtime = SpadeRuntime()
        actual = await execute(c, 4, runtime, replay=core["trace"])
        assert runtime.executed == actual["summary"]["activations"] > 0
        assert [t["state_hash"] for t in core["trace"]] == [
            t["state_hash"] for t in actual["trace"]
        ]

    asyncio.run(run())
