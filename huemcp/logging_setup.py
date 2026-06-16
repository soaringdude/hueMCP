"""House-standard logging setup. App logs and uvicorn's own loggers share one
timestamped format so every line answers: what happened, when, how long, did it fail.

See ~/.claude/skills/new-mcp/assets/mcp-logging-prompt.md. Wired in __main__.main().
"""

from __future__ import annotations

import logging

DATEFMT = "%Y-%m-%d %H:%M:%S"
APP_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_app_logging(level: str) -> None:
    """Configure the root logger once at startup. force=True replaces any handler a
    library installed on import, so every line gets the timestamped APP_FORMAT."""
    logging.basicConfig(level=level.upper(), format=APP_FORMAT, datefmt=DATEFMT, force=True)


def uvicorn_log_config(level: str) -> dict:
    """uvicorn's default LOGGING_CONFIG with a timestamp prepended to both formatters.
    Pass to uvicorn.run(log_config=...). Do NOT also pass log_level."""
    lvl = level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s %(levelprefix)s %(message)s",
                "datefmt": DATEFMT,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "datefmt": DATEFMT,
            },
        },
        "handlers": {
            "default": {"formatter": "default", "class": "logging.StreamHandler",
                        "stream": "ext://sys.stderr"},
            "access": {"formatter": "access", "class": "logging.StreamHandler",
                       "stream": "ext://sys.stdout"},
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": lvl, "propagate": False},
            "uvicorn.error": {"level": lvl},
            "uvicorn.access": {"handlers": ["access"], "level": lvl, "propagate": False},
        },
    }
