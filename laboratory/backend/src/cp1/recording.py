from __future__ import annotations
import hashlib
import io
import json
import platform
import sys
import zipfile
from pathlib import Path
from . import MODEL_VERSION, __version__


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def fingerprint():
    root = Path(__file__).parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(str(p.relative_to(root)).replace("\\", "/").encode())
        h.update(p.read_bytes())
    lock = root.parents[1] / "uv.lock"
    return {
        "source": h.hexdigest(),
        "lock": hashlib.sha256(lock.read_bytes()).hexdigest()
        if lock.exists()
        else None,
        "python": sys.version,
        "platform": platform.platform(),
        "app_version": __version__,
        "model_version": MODEL_VERSION,
    }


def pack(run: dict) -> bytes:
    stream = io.BytesIO()
    trace = run["trace"]
    from .event_models import parse_configuration

    config = parse_configuration(run["configuration"])
    parameters = {
        "defaults": config.defaults,
        "nodes": {
            n.id: {"overrides": n.overrides, "effective": config.node_parameters(n.id)}
            for n in config.nodes
        },
        "edges": {
            e.id: {"overrides": e.overrides, "effective": config.edge_parameters(e)}
            for e in config.edges
        },
    }
    files = {
        "manifest.json": {
            **run["manifest"],
            "trace_hash": digest(trace),
            "parameters_hash": digest(parameters),
            "summary_hash": digest(run.get("summary", {})),
        },
        "configuration.json": run["configuration"],
        "parameters.json": parameters,
        "summary.json": run.get("summary", {}),
        "view.json": run["configuration"].get("view", {}),
    }
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name, value in files.items():
            z.writestr(name, canonical(value))
        z.writestr("trace.jsonl", "\n".join(canonical(t) for t in trace))
        z.writestr(
            "decisions.jsonl",
            "\n".join(
                canonical({"tick": t["tick"], "decisions": t["decisions"]})
                for t in trace
                if t["tick"] > 0
            ),
        )
    return stream.getvalue()


def unpack(data: bytes) -> dict:
    if len(data) > 128 * 1024 * 1024:
        raise ValueError("Слишком большой пакет")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Слишком большой распакованный пакет")
        manifest = json.loads(z.read("manifest.json"))
        config = json.loads(z.read("configuration.json"))
        trace = [
            json.loads(s) for s in z.read("trace.jsonl").decode().splitlines() if s
        ]
        decisions = [
            json.loads(s) for s in z.read("decisions.jsonl").decode().splitlines() if s
        ]
        from .event_models import parse_configuration
        from .algorithms import model_version

        validated = parse_configuration(config)
        if manifest.get("model_version") != model_version(validated):
            raise ValueError("Несовместимая версия модели")
        if manifest.get("trace_hash") != digest(trace):
            raise ValueError("Повреждённая запись: контрольная сумма трассы")
        if decisions != [
            {"tick": t["tick"], "decisions": t["decisions"]}
            for t in trace
            if t["tick"] > 0
        ]:
            raise ValueError("Запись решений не совпадает с трассой")
        parse_configuration(config)
        if manifest.get("configuration_hash") != digest(config):
            raise ValueError("Повреждённая конфигурация")
        if manifest.get("parameters_hash") != digest(
            json.loads(z.read("parameters.json"))
        ):
            raise ValueError("Повреждённые эффективные параметры")
        if json.loads(z.read("view.json")) != config.get("view", {}):
            raise ValueError("Представление не соответствует конфигурации")
        summary = json.loads(z.read("summary.json"))
        if manifest.get("summary_hash") != digest(summary):
            raise ValueError("Повреждённая итоговая статистика")
        return {
            "manifest": manifest,
            "configuration": config,
            "trace": trace,
            "summary": summary,
        }
