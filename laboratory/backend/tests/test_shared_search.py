from adaptive_search.search import Plan, coverage_at, first_coverage, run_session, verify_witnesses


class TwoCases:
    """The same eligible proposals offer either one known case or a different one."""

    def simulate(self, seed, *, overrides=None, parent=None, cut=None):
        value = (overrides or {}).get(("delay", "a>b", 0), 0)
        return dict(outcome={"signature": "B" if value == 2 else "A", "severity": 1},
                    processed_events=11, prefix_events=3 if parent else 0,
                    trace=[{"state_hash": str((seed, value))}])

    def pool(self, run):
        return [dict(key=("delay", "a>b", 0), value=x, baseline_score=3-x,
                     feedback_score=x, feature=str(x), cut=3) for x in (1, 2)]

    def validate(self, run):
        return True

    def replay(self, run):
        return run


def test_equal_prefix_and_cost_to_second_observed_signature():
    problem = TwoCases()
    sessions = {
        m: run_session(problem, seed=45, method=m, plan=Plan(5, 2, fixed_parents=True))
        for m in ("independent", "legacy_boundary", "guided")
    }
    assert [x["seed"] for x in sessions["guided"].rows[:2]] == [
        x["seed"] for x in sessions["independent"].rows[:2]]
    assert first_coverage(sessions["guided"].rows, 2) == {"trials": 3, "events": 33}
    assert first_coverage(sessions["independent"].rows, 2) is None
    assert coverage_at(sessions["guided"].rows, events=32) == 1
    assert coverage_at(sessions["guided"].rows, events=33) == 2
    assert sessions["guided"].rows[2]["prefix_events"] == 3
    assert len(verify_witnesses(problem, sessions["guided"])) == 2
