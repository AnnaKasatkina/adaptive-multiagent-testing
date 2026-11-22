import pytest
import os
import socket
from cp1.models import preset
from cp1.engine import execute
from cp1.spade_runtime import SpadeRuntime
from cp1.validator import validate_trace


@pytest.fixture(scope="module", autouse=True)
def isolated_xmpp_pair():
    # API lifecycle tests stop their owned service. A fresh pair avoids Windows
    # sockets still closing when this module starts a new real XMPP service.
    from cp1.spade_runtime import stop_server

    stop_server()
    previous = os.environ.get("CP1_XMPP_PORT")
    for _ in range(50):
        with socket.socket() as first, socket.socket() as second:
            first.bind(("127.0.0.1", 0))
            port = first.getsockname()[1]
            if port >= 65535:
                continue
            try:
                second.bind(("127.0.0.1", port + 1))
            except OSError:
                continue
            break
    else:
        raise RuntimeError("Не найдена свободная пара портов для тестовых агентов")
    os.environ["CP1_XMPP_PORT"] = str(port)
    try:
        yield
    finally:
        stop_server()
        if previous is None:
            os.environ.pop("CP1_XMPP_PORT", None)
        else:
            os.environ["CP1_XMPP_PORT"] = previous


@pytest.mark.spade
@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("kind,n", [("ring", 2), ("bridge", 6), ("directed", 6)])
async def test_real_spade_parity(kind, n):
    c = preset(kind, n)
    c.horizon = 25
    core = await execute(c, 7)
    runtime = SpadeRuntime()
    other = await execute(c, 7, runtime, replay=core["trace"])
    assert runtime.executed == core["summary"]["activations"] > 0
    assert [t["state_hash"] for t in other["trace"]] == [
        t["state_hash"] for t in core["trace"]
    ]
    assert validate_trace(other)["valid"]
