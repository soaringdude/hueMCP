"""Typed tool errors. The `kind` is the contract the agent branches on.

KINDS is documented in docs/hueMCP_API_SPEC.md. Adding a kind is an interface change.
"""

from __future__ import annotations

KINDS = frozenset(
    {
        "invalid_argument",   # bad / missing / contradictory args
        "not_found",          # named light / room / scene does not exist
        "unsupported",        # the resource can't do this (e.g. a room with no grouped_light)
        "hue_unreachable",    # bridge not configured, or unreachable on the LAN
        "upstream_error",     # unexpected exception surfaced verbatim
    }
)


class HueMCPError(Exception):
    """A tool-level error carrying a machine-readable kind."""

    def __init__(self, kind: str, message: str, hint: str | None = None):
        if kind not in KINDS:
            raise ValueError(f"unknown error kind: {kind!r}")
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        out = {"kind": self.kind, "error": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out
