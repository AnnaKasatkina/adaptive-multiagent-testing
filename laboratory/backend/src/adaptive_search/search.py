"""Search policies and budgets shared by test subjects."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from random import Random
from time import perf_counter, process_time
from typing import Any, Protocol


Key = tuple[str, str, int]
Run = dict[str, Any]

# Error-only and boundary-sensitivity policies preserve the selection rules of
# the earlier elite/H6 searches, restricted to the operators on this task.
METHODS = ("independent", "uniform", "legacy_error", "legacy_boundary", "legacy_fuzz", "guided")


class Problem(Protocol):
    def simulate(
        self, seed: int, *, overrides: dict[Key, int] | None = None,
        parent: Run | None = None, cut: int | None = None,
    ) -> Run: ...

    def pool(self, run: Run) -> list[dict]: ...

    def replay(self, run: Run) -> Run: ...

    def validate(self, run: Run) -> bool | dict: ...


@dataclass(frozen=True)
class Plan:
    attempts: int
    warmup: int
    restart_every: int = 4
    archive_capacity: int = 24
    # Use fixed parents in the ablation; allow archived descendants in search.
    fixed_parents: bool = False

    def __post_init__(self):
        if not 1 <= self.warmup <= self.attempts:
            raise ValueError("warmup must be positive and within the attempt budget")
        if self.restart_every < 2 or self.archive_capacity < self.warmup:
            raise ValueError("invalid restart interval or archive capacity")


@dataclass
class Session:
    rows: list[dict] = field(default_factory=list)
    witnesses: dict[str, Run] = field(default_factory=dict)


@dataclass
class Entry:
    number: int
    seed: int
    overrides: dict[Key, int]
    run: Run
    severity: float
    signature: str | None
    features: list[dict] | None = None


def key_for(seed: int, overrides: dict[Key, int]) -> tuple:
    return seed, tuple(sorted(overrides.items()))


def _events(run: Run) -> int:
    count = run.get("processed_events", run.get("summary", {}).get("tick"))
    if type(count) is not int or count < 0:
        raise ValueError("adapter must return a nonnegative event count, including errors")
    return count


def _outcome(run: Run) -> tuple[str | None, float]:
    if run.get("status", "completed") != "completed":
        return None, 0.0
    outcome = run["outcome"]
    signature = outcome.get("signature")
    if signature is not None and (not isinstance(signature, str) or not signature):
        raise ValueError("signature must be a nonempty string or null")
    severity = float(outcome["severity"] if "severity" in outcome else outcome["E"])
    if severity < 0 or not isfinite(severity):
        raise ValueError("nonfinite or negative severity")
    return signature, severity


def _proposals(problem: Problem, entry: Entry) -> list[dict]:
    if entry.features is None:
        entries = []
        seen = set()
        for raw in problem.pool(entry.run):
            key, value = tuple(raw["key"]), raw["value"]
            if len(key) != 3 or type(value) is not int or not isinstance(raw["feature"], str):
                raise ValueError("invalid proposal from adapter")
            marker = key, value
            if marker not in seen:
                if not all(isfinite(float(raw[x])) for x in ("baseline_score", "feedback_score")):
                    raise ValueError("proposal scores must be finite")
                seen.add(marker)
                entries.append({**raw, "key": key})
        entry.features = entries
    return entry.features


def _pick(
    choices: list[tuple[Entry, dict, dict[Key, int]]],
    method: str, rng: Random, feature_uses: Counter, legacy_uses: Counter, outcome_uses: Counter,
):
    if method == "uniform":
        return rng.choice(choices)
    if method == "legacy_error":
        return max(choices, key=lambda item: (item[0].severity, item[1]["baseline_score"]))
    if method == "legacy_boundary":
        return max(choices, key=lambda item: item[1]["baseline_score"])
    if method == "legacy_fuzz":
        return min(
            choices,
            key=lambda item: legacy_uses[item[1].get("legacy_feature", item[1]["feature"])],
        )
    if method == "guided":
        # Prefer a plausible local effect and spend less on already explored
        # features and parent outcomes. The child outcome is never read here.
        return max(
            choices,
            key=lambda item: (
                item[1]["feedback_score"] / (
                    (1 + feature_uses[item[1]["feature"]]) *
                    (1 + outcome_uses[item[0].signature])
                ),
                -feature_uses[item[1]["feature"]],
                item[1]["baseline_score"],
            ),
        )
    raise ValueError(f"unknown policy {method!r}")


def run_session(problem: Problem, *, seed: int, method: str, plan: Plan) -> Session:
    if method not in METHODS:
        raise ValueError(f"unknown policy {method!r}")
    root_rng = Random(seed)
    choose_rng = Random(seed ^ 0x4626E36ABE)
    roots = [root_rng.getrandbits(63) for _ in range(plan.attempts)]
    session = Session()
    archive: list[Entry] = []
    warmup_parents: list[Entry] = []
    used = set()
    feature_uses: Counter = Counter()
    legacy_uses: Counter = Counter()
    outcome_uses: Counter = Counter()
    total_events = 0
    for number, root_seed in enumerate(roots, start=1):
        start_cpu, start_wall = process_time(), perf_counter()
        parent, candidate = None, None
        run_seed, overrides = root_seed, {}
        post_warmup = number - plan.warmup
        if method != "independent" and post_warmup > 0 and post_warmup % plan.restart_every:
            if plan.fixed_parents:
                parents = ([warmup_parents[(post_warmup - 1) % len(warmup_parents)]]
                           if warmup_parents else [])
            else:
                parents = archive
            choices = []
            for entry in parents:
                for proposal in _proposals(problem, entry):
                    changed = {**entry.overrides, proposal["key"]: proposal["value"]}
                    if key_for(entry.seed, changed) not in used:
                        choices.append((entry, proposal, changed))
            if choices:
                parent, candidate, overrides = _pick(
                    choices, method, choose_rng, feature_uses, legacy_uses, outcome_uses,
                )
                run_seed = parent.seed
                feature_uses[candidate["feature"]] += 1
                legacy_uses[candidate.get("legacy_feature", candidate["feature"])] += 1
                outcome_uses[parent.signature] += 1
        prepare_cpu, prepare_wall = process_time() - start_cpu, perf_counter() - start_wall
        used.add(key_for(run_seed, overrides))
        cut = candidate.get("cut") if candidate else None
        active_parent = parent.run if cut is not None else None
        run = problem.simulate(run_seed, overrides=overrides, parent=active_parent, cut=cut)
        cost = _events(run)
        prefix = run.get("prefix_events", 0)
        if type(prefix) is not int or not 0 <= prefix <= cost:
            raise ValueError("invalid prefix event accounting")
        total_events += cost
        signature, severity = _outcome(run)
        if run.get("status", "completed") == "completed":
            entry = Entry(number, run_seed, overrides, run, severity, signature)
            archive.append(entry)
            if number <= plan.warmup:
                warmup_parents.append(entry)
            if len(archive) > plan.archive_capacity:
                archive.pop(0)
            if signature is not None and signature not in session.witnesses:
                session.witnesses[signature] = run
        session.rows.append(dict(
            number=number, method=method, root_seed=root_seed, seed=run_seed,
            overrides=[[*key, value] for key, value in sorted(overrides.items())],
            parent=parent.number if parent else None, cut=cut,
            proposal=candidate, status=run.get("status", "completed"), error=run.get("error"),
            signature=signature, severity=severity, events=cost, prefix_events=prefix,
            cumulative_events=total_events, coverage=len(session.witnesses),
            proposal_cpu_seconds=prepare_cpu, proposal_wall_seconds=prepare_wall,
            cpu_seconds=process_time() - start_cpu, wall_seconds=perf_counter() - start_wall,
        ))
    return session


def coverage_at(rows: list[dict], *, events: int | None = None, trials: int | None = None) -> int:
    """Count only observations from completed trials within the supplied budget."""
    if events is None and trials is None:
        raise ValueError("must supply a budget")
    coverage = 0
    for row in rows:
        if (events is not None and row["cumulative_events"] > events) or (
            trials is not None and row["number"] > trials
        ):
            break
        coverage = row["coverage"]
    return coverage


def first_coverage(rows: list[dict], count: int) -> dict | None:
    for row in rows:
        if row["coverage"] >= count:
            return {"trials": row["number"], "events": row["cumulative_events"]}
    return None


def verify_witnesses(problem: Problem, session: Session) -> list[dict]:
    result = []
    for signature, run in session.witnesses.items():
        start_cpu, start_wall = process_time(), perf_counter()
        audit = problem.validate(run)
        if not (audit.get("valid", False) if isinstance(audit, dict) else audit):
            raise ValueError(f"witness for {signature} is invalid: {audit}")
        repeated = problem.replay(run)
        old = [x["state_hash"] for x in run["trace"]]
        new = [x["state_hash"] for x in repeated["trace"]]
        if not old or old != new or _outcome(repeated)[0] != signature:
            raise ValueError(f"inexact witness replay for {signature}")
        result.append(dict(signature=signature, events=_events(repeated),
                           state_hash=old[-1], audit=audit,
                           cpu_seconds=process_time()-start_cpu,
                           wall_seconds=perf_counter()-start_wall))
    return result
