"""Owned, loopback-only XMPP service. No dependency files are modified."""

import argparse
import asyncio
import sys
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


async def serve(port, server_port, data):
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("CP1_XMPP_LOG_LEVEL", "WARNING"))
    import pyjabber.server as module
    from pyjabber.server_parameters import Parameters

    module.setup_query_local_ip = lambda: "127.0.0.1"
    certs = (Path(data) / "certs").resolve()
    certs.mkdir(parents=True, exist_ok=True)
    # PyJabber's certificate generator expects a pre-provisioned CA. A private
    # loopback service instead owns its own ephemeral self-signed certificate.
    if not (certs / "localhost_cert.pem").exists():
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        (certs / "localhost_key.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        (certs / "localhost_cert.pem").write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
    server = module.Server(
        Parameters(
            host="localhost",
            client_port=port,
            server_port=server_port,
            database_in_memory=True,
            cert_path=str(certs),
            message_persistence=False,
            plugins=[p for p in Parameters().plugins if p != "urn:xmpp:http:upload:0"],
            items={"pubsub.$": Parameters().items["pubsub.$"]},
        )
    )
    # Avoid duplicate IPv4 binds when localhost and the discovered IP resolve
    # to the same address on Windows; the XMPP domain remains localhost.
    server._host = "127.0.0.1"
    server._public_ip = None
    # PBKDF2 releases the GIL. A small owned thread pool avoids spawning one
    # Python child per CPU in each short-lived Windows XMPP service, and can
    # be shut down with the service without leaving authentication children.
    from pyjabber import AppConfig
    from attrs import evolve

    AppConfig.app_config.process_pool_exe.shutdown(wait=False, cancel_futures=True)
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="xmpp-auth")
    AppConfig.app_config = evolve(
        AppConfig.app_config, process_pool_exe=pool, semaphore=asyncio.Semaphore(2)
    )
    try:
        await server.start()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--server-port", type=int, required=True)
    p.add_argument("--data", required=True)
    a = p.parse_args()
    asyncio.run(serve(a.port, a.server_port, a.data))
