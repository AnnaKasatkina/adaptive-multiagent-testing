"""A real SPADE adapter, sharing only the mathematical transition with CoreRuntime."""

from __future__ import annotations
import atexit
import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from .algorithms import rule, decode_message

_server = None


def stop_server():
    global _server
    if _server and _server.poll() is None:
        _server.terminate()
        try:
            _server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _server.kill()
            _server.wait()
    _server = None


def ensure_server():
    global _server
    port = int(os.environ.get("CP1_XMPP_PORT", "55222"))
    if os.environ.get("CP1_XMPP_EXTERNAL") == "1":
        return port
    if _server and _server.poll() is None:
        return port
    for candidate in (port, port + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", candidate))
            except OSError as exc:
                raise RuntimeError(
                    f"Локальный порт {candidate} занят или недоступен"
                ) from exc
    data = Path(
        os.environ.get("CP1_DATA", Path(__file__).resolve().parents[4] / "artifacts/library")
    )
    data.mkdir(parents=True, exist_ok=True)
    log = open(data / "xmpp.log", "ab")
    _server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cp1.xmpp_server",
            "--port",
            str(port),
            "--server-port",
            str(port + 1),
            "--data",
            str(data),
        ],
        stdout=log,
        stderr=log,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    log.close()
    atexit.register(stop_server)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if _server.poll() is not None:
            raise RuntimeError(f"XMPP не запустился; см. {data / 'xmpp.log'}")
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return port
        time.sleep(0.1)
    stop_server()
    raise TimeoutError("Время запуска XMPP истекло")


class SpadeRuntime:
    name = "spade"

    async def start(self, config):
        self.diagnostics = {"startup_attempts": 0, "connection_failures": []}
        for attempt in range(1, 4):
            self.diagnostics["startup_attempts"] = attempt
            try:
                await self._start_once(config)
                return
            except (TimeoutError, ConnectionError) as exc:
                self.diagnostics["connection_failures"].append(
                    {"attempt": attempt, "error": str(exc)}
                )
                await self.close()
                if attempt == 3:
                    raise
                # No model state exists yet. Retry with entirely new agent
                # identities; all preparation failures remain in the manifest.
                await asyncio.sleep(0.2)

    async def _start_once(self, config):
        port = await asyncio.to_thread(ensure_server)
        from spade.agent import Agent
        from spade.behaviour import CyclicBehaviour
        from spade.message import Message as SpadeMessage
        from spade.container import Container

        logging.getLogger("spade").setLevel(logging.WARNING)
        Container().reset()
        self._SpadeMessage = SpadeMessage
        self._pending = {}
        self._serial = 0
        self.agents = []
        self.mirrors = {}
        self.executed = 0
        tag = uuid.uuid4().hex[:12]
        password = uuid.uuid4().hex
        self.jids = {n.id: f"{tag}_{n.id}@localhost" for n in config.nodes}
        coordinator = f"{tag}_coordinator@localhost"
        owner = self
        selected_rule = rule(config)

        class LocalAgent(Agent):
            async def _async_connect(agent):
                # The owned server deliberately listens only on IPv4 loopback.
                # Avoid retries against localhost's IPv6 address on Windows.
                agent.client.use_ipv6 = False
                connect = agent.client.connect

                def loopback(host=None, port=None):
                    return connect(host="127.0.0.1", port=port or agent.xmpp_port)

                # SPADE passes an explicit 'localhost' address; Slixmpp's DNS
                # IPv6 flag is bypassed for that path. Keep the JID domain but
                # force the actual TCP destination to the owned IPv4 service.
                agent.client.connect = loopback
                try:
                    return await super()._async_connect()
                finally:
                    agent.client.connect = connect

        class Replies(CyclicBehaviour):
            async def run(beh):
                msg = await beh.receive(timeout=1)
                if msg:
                    value = json.loads(msg.body)
                    future = owner._pending.get(value["request"])
                    if future and not future.done():
                        future.set_result(value)

        class Updates(CyclicBehaviour):
            async def run(beh):
                msg = await beh.receive(timeout=1)
                if not msg:
                    return
                command = json.loads(msg.body)
                try:
                    if "event" in command:
                        effects = selected_rule.dispatch(
                            beh.agent.local,
                            beh.agent.ident,
                            beh.agent.neighbours,
                            command["event"],
                        )
                        response = {
                            "request": command["request"],
                            "state": beh.agent.local.snapshot(),
                            "effects": effects,
                        }
                    else:
                        outgoing, applied = selected_rule.activate(
                            beh.agent.local,
                            beh.agent.ident,
                            beh.agent.neighbours,
                            [decode_message(m) for m in command["inbox"]],
                            command["tick"],
                        )
                        response = {
                            "request": command["request"],
                            "state": beh.agent.local.snapshot(),
                            "outgoing": [asdict(m) for m in outgoing],
                            "applied": applied,
                        }
                except Exception as exc:
                    response = {"request": command["request"], "error": str(exc)}
                reply = SpadeMessage(to=coordinator)
                reply.body = json.dumps(response, allow_nan=False)
                await beh.send(reply)

        self.coordinator = LocalAgent(coordinator, password, port=port)
        self.driver = Replies()
        self.coordinator.add_behaviour(self.driver)
        self.agents.append(self.coordinator)
        states = selected_rule.initial_states(config)
        for node in sorted(config.nodes, key=lambda n: n.id):
            agent = LocalAgent(self.jids[node.id], password, port=port)
            agent.ident = node.id
            agent.local = states[node.id]
            agent.neighbours = sorted(
                e.target for e in config.edges if e.source == node.id
            )
            agent.add_behaviour(Updates())
            self.agents.append(agent)
            self.mirrors[node.id] = agent.local.snapshot()
        try:
            # Registration in the embedded in-memory XMPP database is serialized.
            # This startup barrier is outside the logical execution schedule.
            for agent in self.agents:
                try:
                    await asyncio.wait_for(agent.start(auto_register=True), 20)
                except TimeoutError as exc:
                    raise TimeoutError(
                        f"Не удалось подключить SPADE-агента {agent.jid} за 20 секунд"
                    ) from exc
        except BaseException:
            await self.close()
            raise

    async def activate(self, ident, inbox, tick):
        self._serial += 1
        request = str(self._serial)
        future = asyncio.get_running_loop().create_future()
        self._pending[request] = future
        msg = self._SpadeMessage(to=self.jids[ident])
        msg.body = json.dumps(
            {"request": request, "tick": tick, "inbox": [asdict(m) for m in inbox]},
            allow_nan=False,
        )
        await self.driver.send(msg)
        try:
            response = await asyncio.wait_for(future, 10)
        finally:
            self._pending.pop(request, None)
        if "error" in response:
            raise RuntimeError(response["error"])
        self.mirrors[ident] = response["state"]
        self.executed += 1
        return [decode_message(m) for m in response["outgoing"]], response["applied"]

    def snapshot(self):
        return dict(sorted(self.mirrors.items()))

    async def dispatch(self, ident, event):
        self._serial += 1
        request = str(self._serial)
        future = asyncio.get_running_loop().create_future()
        self._pending[request] = future
        msg = self._SpadeMessage(to=self.jids[ident])
        msg.body = json.dumps({"request": request, "event": event}, allow_nan=False)
        await self.driver.send(msg)
        try:
            response = await asyncio.wait_for(future, 10)
        finally:
            self._pending.pop(request, None)
        if "error" in response:
            raise RuntimeError(response["error"])
        self.mirrors[ident] = response["state"]
        self.executed += 1
        return response["effects"]

    async def close(self):
        for agent in reversed(getattr(self, "agents", [])):
            try:
                await asyncio.wait_for(agent.stop(), 3)
            except Exception:
                pass
        self.agents = []
