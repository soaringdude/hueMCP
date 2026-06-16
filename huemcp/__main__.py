"""Entry point: build the backend, generate TLS material, run uvicorn.

Exposed as the `huemcp` console script in pyproject.toml.
"""

from __future__ import annotations

import logging

from . import __version__
from .client import HueBackend
from .config import Config
from .logging_setup import configure_app_logging, uvicorn_log_config
from .server import create_app


def main() -> None:
    config = Config.from_env()
    configure_app_logging(config.log_level)
    log = logging.getLogger("huemcp")

    backend = HueBackend.from_config(config)
    # Startup banner: version + key config, one line, NO secrets (never log the app key).
    log.info("starting hueMCP v%s: bind=%s port=%s tls=%s paired=%s bridge_ip=%s",
             __version__, config.bind, config.port, config.tls_mode, backend.paired, backend.bridge_ip)
    if not backend.paired:
        log.warning("bridge not paired; run `huemcp-cli pair` or set HUE_BRIDGE_IP/HUE_APP_KEY")

    cert_pem = None
    ssl_certfile = ssl_keyfile = None
    if config.tls_mode == "selfsigned":
        from .tls import ensure_self_signed
        cert_pem = ensure_self_signed(config.cert_path, config.key_path)
        ssl_certfile = str(config.cert_path)
        ssl_keyfile = str(config.key_path)

    app = create_app(backend, config.auth_token, cert_pem=cert_pem)

    import uvicorn
    uvicorn.run(
        app,
        host=config.bind,
        port=config.port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        log_config=uvicorn_log_config(config.log_level),
    )


if __name__ == "__main__":
    main()
