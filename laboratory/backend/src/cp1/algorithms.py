"""Algorithm identity, transition, message codec, and algorithm-specific checks."""

from . import MODEL_VERSION as RAPS_VERSION
from . import algorithm as raps
from . import homework


def rule(config):
    if config.schema_version == 2:
        from .event_rule import EventRule

        return EventRule(config)
    return homework if config.algorithm.startswith("homework") else raps


def model_version(config):
    if config.schema_version == 2:
        return f"{config.algorithm}.events-1.window-800.parameters-2.recording-2"
    if config.algorithm == "homework-original":
        return "homework-original-spade414-1.tick-1.parameters-1"
    return homework.MODEL_VERSION if config.algorithm == "homework" else RAPS_VERSION


def metadata(config):
    return {
        "id": config.algorithm,
        "name": "Архивный обработчик SPADE 4.1.4"
        if config.algorithm == "homework-original"
        else "Локальный"
        if config.algorithm == "homework"
        else "RAPS",
        "source": f"https://github.com/AnnaKasatkina/mean_consensus_spade/tree/{homework.COMMIT}"
        if config.algorithm.startswith("homework")
        else "https://jmlr.org/papers/volume21/18-813/18-813.pdf",
        "adapter": "event-window-800"
        if config.schema_version == 2 and config.algorithm == "homework"
        else "controlled-local-rule"
        if config.algorithm.startswith("homework")
        else "algorithm-1",
        "commit": homework.COMMIT if config.algorithm.startswith("homework") else None,
        "message_compatibility_patch": config.algorithm == "homework",
    }


def decode_message(value):
    return homework.ValueMessage(**value) if "value" in value else raps.Message(**value)
