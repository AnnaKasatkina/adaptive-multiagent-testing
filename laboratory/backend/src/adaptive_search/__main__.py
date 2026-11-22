"""Run one adaptive search session."""

import argparse
import json
from pathlib import Path

from .search import METHODS, Plan, run_session, verify_witnesses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", choices=("local", "maxsum"), default="local")
    parser.add_argument("--profile", default="development")
    parser.add_argument("--method", choices=METHODS, default="guided")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--trials", type=int, default=8)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.algorithm == "maxsum":
        from .maxsum_adapter import MaxSumProblem, maxsum_profile

        problem = MaxSumProblem(maxsum_profile(args.profile))
    else:
        from .local import LocalProblem, local_profile

        problem = LocalProblem(local_profile(args.profile))
    session = run_session(problem, seed=args.seed, method=args.method, plan=Plan(args.trials, args.warmup))
    audits = verify_witnesses(problem, session)
    if args.output:
        result = dict(configuration=problem.config, method=args.method, seed=args.seed,
                      rows=session.rows, audits=audits)
        args.output.parent.mkdir(exist_ok=True, parents=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    counts = dict(trials=len(session.rows), events=sum(row["events"] for row in session.rows),
                  signatures=sorted(session.witnesses), verified=len(audits))
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == "__main__":
    main()
