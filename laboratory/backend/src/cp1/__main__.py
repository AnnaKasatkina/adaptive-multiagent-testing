"""Command line entry points use the same models and materializer as the API."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .engine import execute
from .models import preset
from .event_models import parse_configuration, event_preset
from .parameters import materialize
from .recording import pack, unpack
from .validator import validate_trace


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        prog="cp1", description="Лаборатория воспроизводимых исполнений RAPS"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--data", type=Path)
    for name in ("run", "replay", "fork"):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
        p.add_argument("--runtime", choices=("core", "spade"), default="core")
        if name == "run":
            p.add_argument("--configuration", type=Path)
            p.add_argument("--preset", default="bridge")
            p.add_argument("--space", type=Path)
            p.add_argument("--values", type=Path)
        else:
            p.add_argument("record", type=Path)
        if name != "replay":
            p.add_argument("--seed", type=int, default=0)
        if name == "fork":
            p.add_argument("--after", type=int, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("record", type=Path)
    space = sub.add_parser("export-space")
    space.add_argument("spec", type=Path)
    space.add_argument("--output", type=Path, required=True)
    export = sub.add_parser("preset")
    export.add_argument("--kind", default="bridge")
    export.add_argument("--nodes", type=int, default=6)
    export.add_argument("--output", required=True, type=Path)
    export.add_argument("--legacy", action="store_true")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from .service import create_app

        uvicorn.run(
            create_app(args.data), host="127.0.0.1", port=args.port, log_level="warning"
        )
        return
    if args.command == "verify":
        result = validate_trace(unpack(args.record.read_bytes()))
        if not result["valid"]:
            raise SystemExit(json.dumps(result, ensure_ascii=False))
    elif args.command == "export-space":
        spec = read_json(args.spec)
        materialize(spec["base_configuration"], {}, spec)
        write_json(args.output, spec)
        result = {"saved": str(args.output), "variables": len(spec["variables"])}
    elif args.command == "preset":
        write_json(
            args.output,
            (
                preset(args.kind, args.nodes)
                if args.legacy
                else event_preset(args.kind, args.nodes)
            ).model_dump(),
        )
        result = {"saved": str(args.output)}
    else:
        original = None
        if args.command == "run":
            spec = read_json(args.space) if args.space else None
            data = (
                read_json(args.configuration)
                if args.configuration
                else spec["base_configuration"]
                if spec
                else event_preset(args.preset).model_dump()
            )
            c = (
                materialize(data, read_json(args.values) if args.values else {}, spec)
                if spec
                else parse_configuration(data)
            )
            if args.values and not spec:
                parser.error("--values требует --space")
        else:
            original = unpack(args.record.read_bytes())
            c = parse_configuration(original["configuration"])
            audit = validate_trace(original)
            if not audit["valid"]:
                raise ValueError("Исходная запись не прошла проверку")
            if args.command == "replay" and not audit["complete"]:
                raise ValueError(
                    "CLI-повтор требует полную запись; для незавершённой используйте fork"
                )
            if args.command == "fork" and not 0 <= args.after < len(original["trace"]):
                raise ValueError("Граница префикса отсутствует в записи")
        seed = original["manifest"]["seed"] if args.command == "replay" else args.seed
        if not 0 <= seed < 2**63:
            raise ValueError("Seed должен лежать от 0 до 2^63-1")
        runtime = None
        if args.runtime == "spade":
            # SPADE selects its supported Windows loop policy at import time.
            # Import before asyncio.run creates the execution loop.
            import spade  # noqa: F401
            from .spade_runtime import SpadeRuntime

            runtime = SpadeRuntime()
        run = asyncio.run(
            execute(
                c,
                seed,
                runtime,
                replay=original["trace"] if original else None,
                fork_tick=args.after if args.command == "fork" else None,
            )
        )
        if original:
            run["manifest"]["parent"] = {
                "run_id": original["manifest"]["id"],
                "after_tick": args.after
                if args.command == "fork"
                else original["trace"][-1]["tick"],
                "kind": args.command,
            }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(pack(run))
        result = {
            "saved": str(args.output),
            "summary": run["summary"],
            "audit": validate_trace(run),
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
