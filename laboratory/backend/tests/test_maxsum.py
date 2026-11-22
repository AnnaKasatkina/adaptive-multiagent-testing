import copy
import json

import pytest

from adaptive_search.maxsum import audit_transport, oracle, profile, replay, simulate


@pytest.mark.parametrize("n", [6, 7, 8])
def test_unique_tree_oracle_and_convergence(n):
    config = profile("tree", n)
    alternating = [i % 2 for i in range(n)]
    assert oracle(config) == dict(cost=n//2, assignment=alternating, n_optima=1, evaluated=3**n)
    for seed in (0, 4, 37):
        run = simulate(config, seed)
        assert run["summary"]["channels"] == alternating
        assert run["summary"]["gap"] == 0
        assert audit_transport(run)["ok"]


@pytest.mark.parametrize("name", ["cycle", "chorded"])
@pytest.mark.parametrize("n", [6, 7, 8])
def test_cyclic_trace_matches_cost_and_replays_from_json(name, n):
    config = profile(name, n, 18000)
    run = simulate(config, 4)
    for row in run["trace"]:
        x = row["channels"]
        cost = sum(config["unary"][i][c] for i,c in enumerate(x))
        cost += sum(w for i,j,w in config["edges"] if x[i] == x[j])
        assert cost == row["metrics"]["cost"] >= oracle(config)["cost"]
    assert audit_transport(run)["ok"]
    loaded = json.loads(json.dumps(run))
    assert replay(loaded)["trace"] == run["trace"]


def small_config():
    c = profile("tree", 6, 35)
    c.update(unary=[[0, 3, 4], [0, 1, 8], [0, 2, 9]],
             edges=[[0, 1, 5], [1, 2, 5]], first_update_ms=[1, 4, 2],
             interval_ms=[10, 10], delay_ms=[1, 5])
    return c


def test_hand_cavity_projection_and_decode():
    c = small_config()
    r = simulate(c, 1, overrides={("delay", "s0>s1", 0): 2, ("delay", "s2>s1", 0): 1})
    row = next(row for row in r["trace"] if row["time_ms"] == 4)
    update, left, right = row["events"]
    assert [m["values"] for m in update["used"]] == [[3, 0, 0], [2, 0, 0]]
    assert update["scores"] == [5, 1, 8]
    assert update["channel"] == 1
    assert (update["cost_before"], update["cost_after"]) == (10, 1)
    # Remove the recipient's own vector, then minimize the pair factor.
    assert left["message"]["values"] == [0, 1, 0]
    assert right["message"]["values"] == [0, 2, 0]


def test_equal_time_update_is_first_and_fifo_clamping_survives_branch():
    c = small_config()
    c.update(delay_ms=[1, 21], first_update_ms=[1, 22, 2], horizon_ms=44)
    r = simulate(c, 5, overrides={("delay", "s0>s1", 0): 21, ("delay", "s0>s1", 1): 1})
    sends = [e["message"] for row in r["trace"] for e in row["events"]
             if e["kind"] == "send" and e["message"]["id"] in ("s0>s1#0", "s0>s1#1")]
    assert [m["due_ms"] for m in sends] == [22, 22]
    assert sends[1]["fifo_wait_ms"] == 10
    at_22 = [row["events"][0] for row in r["trace"] if row["time_ms"] == 22]
    assert not any(m["sender"] == "s0" for m in next(e for e in at_22 if e["agent"] == "s1" and e["kind"] == "update")["used"])
    cut = next(row["tick"] for row in r["trace"] if row["time_ms"] == 11 and row["events"][0]["kind"] == "update")
    branch = simulate(c, 88, parent=r, cut=cut)
    assert branch["trace"][:cut+1] == r["trace"][:cut+1]
    arrivals = [row["events"][0]["message"]["id"] for row in branch["trace"]
                if row["time_ms"] == 22 and row["events"][0]["kind"] == "delivery"]
    assert [m for m in arrivals if m in ("s0>s1#0", "s0>s1#1")] == ["s0>s1#0", "s0>s1#1"]
    assert audit_transport(branch)["ok"]
    assert replay(branch)["trace"] == branch["trace"]
    with pytest.raises(ValueError, match="prefix"):
        simulate(c, 88, parent=r, cut=cut, overrides={("delay","s0>s1",0): 1})


def test_reject_corrupt_trace_and_invalid_override():
    c = small_config()
    run = simulate(c, 9)
    bad = copy.deepcopy(run)
    m = next(e["message"] for row in bad["trace"] for e in row["events"] if e["kind"] == "send")
    m["due_ms"] += 1
    with pytest.raises(ValueError):
        audit_transport(bad)
    with pytest.raises(ValueError):
        replay(bad)
    for key, value in [(("delay","s0>s1",999),2),(("delay","s0>s1",0),0)]:
        with pytest.raises(ValueError):
            simulate(c, 9, overrides={key:value})

def test_exact_single_move_landscape_and_suboptimal_preferences():
    from adaptive_search.maxsum_adapter import outcome
    c = small_config()
    # [1,0,1] has no conflicts; each individual recoloring is non-improving.
    observed = outcome(c, [1,0,1])
    assert observed["signature"] == "preferences/one-step-minimum"
    assert (observed["cost"], observed["optimum"], observed["best_one_sensor_gain"]) == (5, 1, 0)
    assert outcome(c, [0,0,0])["signature"] == "conflict/one-step-improvable"
    assert outcome(c, [0,1,0])["signature"] is None


def test_signal_pool_against_unseen_mutations_and_common_interface():
    from adaptive_search.maxsum_adapter import MaxSumProblem
    from adaptive_search.search import run_session, Plan, verify_witnesses
    p = MaxSumProblem(profile("chorded", 6, 11500))
    parent = p.simulate(7)
    examples = p.pool(parent)
    assert any(q["predicted_flip"] for q in examples)
    for q in examples[::4]:
        child = p.simulate(7, overrides={q["key"]: q["value"]})
        event = next(row["events"][0] for row in child["trace"][1:]
                     if row["time_ms"] == q["update_ms"] and row["events"][0]["kind"] == "update"
                     and row["events"][0]["agent"] == q["agent"])
        incoming = next((m for m in event["used"] if m["sender"] == q["key"][1].split(">")[0]), None)
        assert (incoming["id"] if incoming else None) == q["new_message"]
        # When this activation is the first global choice or payload divergence,
        # all parent payloads used in this forecast are exact.
        ref = next(row["events"][0] for row in parent["trace"][1:] if
                   row["time_ms"] == q["update_ms"] and row["events"][0]["kind"] == "update"
                   and row["events"][0]["agent"] == q["agent"])
        p_vectors = {m["id"]: m["values"] for row in parent["trace"] for e in row["events"]
                     if e["kind"] == "send" for m in [e["message"]]}
        if all(m["values"] == p_vectors[m["id"]] for m in event["used"]):
            assert q["old_channel"] == ref["channel"]
            assert q["new_channel"] == event["channel"]
            assert q["new_scores"] == event["scores"]
    session = run_session(p, seed=100, method="guided", plan=Plan(warmup=1, attempts=3))
    assert session.rows[1]["proposal"] is not None
    assert all(v["audit"]["valid"] for v in verify_witnesses(p, session))
