import asyncio
import copy
import dataclasses
import pytest
from hypothesis import given, settings, strategies as st
from cp1.models import Configuration, preset
from cp1.algorithm import AgentState, Message, activate
from cp1.engine import execute
from cp1.recording import pack, unpack, digest
from cp1.parameters import materialize
from cp1.validator import validate_trace
from cp1.store import RunStore


def regular(n=2, horizon=3):
    c = preset("ring", n)
    c.horizon = horizon
    c.defaults.update(activation_probability=1, delay_min=1, delay_max=1)
    return Configuration.model_validate(c.model_dump())


def test_analytic_two_agents():
    c = regular()
    c.nodes[1].initial = 2
    r = asyncio.run(execute(c))
    actual = [[s["z"] for s in t["state"]["agents"].values()] for t in r["trace"]]
    for a, e in zip(actual, [[0, 2], [0, 2], [4 / 3, 2 / 3], [8 / 5, 2 / 5]]):
        assert a == pytest.approx(e, abs=1e-13)
    assert validate_trace(r)["valid"]


@pytest.mark.parametrize("value", [-3.5, 0, 7.25])
def test_initial_consensus(value):
    c = regular(6, 100)
    for node in c.nodes:
        node.initial = value
    r = asyncio.run(execute(c))
    assert r["summary"]["Q"] is None
    assert max(t["metrics"]["D"] for t in r["trace"]) < 1e-12
    assert validate_trace(r)["valid"]


@pytest.mark.parametrize(
    "kind", ["ring", "bridge", "directed", "star", "path", "complete", "random"]
)
def test_convergence_and_mass(kind):
    c = preset(kind)
    c.horizon = 500
    c.defaults.update(activation_probability=1, delay_max=1)
    r = asyncio.run(execute(c))
    assert r["summary"]["error_mean"] < 1e-6
    assert validate_trace(r)["valid"]


def test_cumulative_latest_duplicate_and_immutable_payload():
    s = AgentState(0, rho={"b": {"x": 0.0, "y": 0.0, "stamp": 0}})
    old = Message("b:1:a", "b", "a", 1, 1, 1, 0.5)
    newer = Message("b:2:a", "b", "a", 2, 2, 2, 1.0)
    outgoing, applied = activate(s, "a", ["b"], [newer, old, newer], 3)
    assert applied == 1 and s.x == 2 and s.rho["b"]["stamp"] == 2
    frozen = outgoing[0].phi_y
    activate(s, "a", ["b"], [old], 4)
    assert outgoing[0].phi_y == frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        old.phi_x = 4


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["nodes"][0].update(initial=float("nan")),
        lambda d: d["nodes"][0]["overrides"].update(activation_probability=1.1),
        lambda d: d["nodes"][0]["overrides"].update(
            activation_mode="periodic", activation_period=2, activation_phase=2
        ),
        lambda d: d["edges"][0]["overrides"].update(delay_min=4, delay_max=2),
        lambda d: d["edges"].append(d["edges"][0].copy()),
        lambda d: d.update(edges=[]),
        lambda d: d.update(schema_version=99),
    ],
)
def test_invalid_configuration(change):
    d = preset().model_dump()
    change(d)
    with pytest.raises(ValueError):
        Configuration.model_validate(d)


def test_inheritance_and_materialization():
    c = preset()
    c.nodes[0].overrides["activation_probability"] = 0.1
    assert c.node_parameters("a01")["activation_probability"] == 0.1
    assert c.node_parameters("a02")["activation_probability"] == 0.5
    variable = {
        "name": "p_A",
        "kind": "float",
        "minimum": 0.0,
        "maximum": 1.0,
        "default": 0.5,
        "targets": [
            {"scope": "node", "id": n.id, "parameter": "activation_probability"}
            for n in c.nodes[:3]
        ],
    }
    spec = {
        "schema_version": 1,
        "base_hash": digest(c.model_dump()),
        "variables": [variable],
    }
    result = materialize(c.model_dump(), {"p_A": 0.2}, spec)
    assert [
        result.node_parameters(n.id)["activation_probability"] for n in result.nodes
    ] == [0.2, 0.2, 0.2, 0.5, 0.5, 0.5]
    assert c.node_parameters("a01")["activation_probability"] == 0.1
    with pytest.raises(ValueError):
        materialize(c.model_dump(), {"p_A": 2}, spec)
    spec["variables"].append({**variable, "name": "overlap"})
    with pytest.raises(ValueError):
        materialize(c.model_dump(), {}, spec)


@pytest.mark.parametrize("fork", [0, 1, 10, 39, 40])
def test_replay_fork_and_pending(fork):
    c = preset()
    c.horizon = 40
    parent = asyncio.run(execute(c, 7))
    replay = asyncio.run(execute(c, 7, replay=parent["trace"]))
    assert [t["state_hash"] for t in replay["trace"]] == [
        t["state_hash"] for t in parent["trace"]
    ]
    child = asyncio.run(execute(c, 1000, replay=parent["trace"], fork_tick=fork))
    assert child["trace"][: fork + 1] == parent["trace"][: fork + 1]
    assert validate_trace(child)["valid"]
    if fork == 40:
        assert child["summary"]["Q"] == parent["summary"]["Q"]


def test_pack_import_and_corruption(tmp_path):
    c = regular(2, 20)
    r = asyncio.run(execute(c))
    data = pack(r)
    back = unpack(data)
    assert back["trace"] == r["trace"]
    bad = copy.deepcopy(r)
    bad["trace"][1]["state"]["agents"]["a01"]["x"] += 1
    assert not validate_trace(bad)["valid"]
    store = RunStore(tmp_path / "runs.sqlite")
    store.create(
        "x",
        {
            "manifest": r["manifest"],
            "configuration": r["configuration"],
            "summary": r["summary"],
        },
    )
    for row in r["trace"]:
        store.append("x", row)
    reopened = RunStore(tmp_path / "runs.sqlite")
    assert reopened.bundle("x")["trace"] == r["trace"]
    import io
    import zipfile
    import json

    raw = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as z, zipfile.ZipFile(raw, "w") as out:
        for name in z.namelist():
            value = z.read(name)
            if name == "configuration.json":
                v = json.loads(value)
                v["horizon"] += 1
                value = json.dumps(v).encode()
            out.writestr(name, value)
    with pytest.raises(ValueError):
        unpack(raw.getvalue())


@settings(max_examples=35, deadline=None, derandomize=True)
@given(
    n=st.integers(2, 8),
    seed=st.integers(0, 10000),
    p=st.sampled_from([0.0, 0.1, 0.5, 1.0]),
    delay=st.integers(1, 5),
)
def test_generated_scenarios_properties(n, seed, p, delay):
    c = preset("random", n)
    c.horizon = 35
    c.defaults.update(activation_probability=p, delay_max=delay)
    r = asyncio.run(execute(c, seed))
    audit = validate_trace(r)
    assert audit["valid"], audit


def test_periodic_phases():
    c = preset()
    c.horizon = 16
    c.defaults.update(
        activation_mode="periodic", activation_period=4, activation_phase=3
    )
    c.nodes[0].overrides["activation_phase"] = 0
    r = asyncio.run(execute(c))
    a = [
        t["tick"]
        for t in r["trace"]
        if any(e.get("agent") == "a01" for e in t["events"])
    ]
    b = [
        t["tick"]
        for t in r["trace"]
        if any(e.get("agent") == "a02" for e in t["events"])
    ]
    assert a == [1, 5, 9, 13] and b == [4, 8, 12, 16]
    assert validate_trace(r)["valid"]


def test_record_context_and_payload_tamper_detected():
    c = regular(2, 5)
    r = asyncio.run(execute(c))
    bad = copy.deepcopy(r["trace"])
    bad[1]["decisions"][0]["entity"] = "missing"
    with pytest.raises(ValueError):
        asyncio.run(execute(c, replay=bad))
