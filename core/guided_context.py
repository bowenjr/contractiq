"""Request-scoped context for the guided Bid workflow banner.

Templates for supporting pages (discovery, owner decision, registers) are rendered by many
routes. Rather than threading the workflow projection through each one, a tiny ASGI
middleware remembers the active request so the shared page header can look up which
guided task the page belongs to. Nothing here reads storage or a clock.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

_current_request: ContextVar[Request | None] = ContextVar(
    "contractiq_current_request", default=None
)

_BID_PATH = re.compile(r"^/bids/(?P<bid_id>[^/]+)(?:/(?P<rest>.*))?$")


_bid_hint: ContextVar[str | None] = ContextVar("contractiq_page_bid_hint", default=None)


def set_page_bid_hint(context: dict[str, object]) -> None:
    """Remember which Bid the page being rendered belongs to, if its context says so.

    Detail pages such as ``/requirements/REQ-1`` carry no Bid in the URL; the record they
    render does. Looks for a ``bid_id`` key, or a ``bid_id`` on any one context object.
    """
    found = context.get("bid_id")
    if not isinstance(found, str):
        found = None
        for value in context.values():
            candidate = (
                value.get("bid_id") if isinstance(value, dict) else getattr(value, "bid_id", None)
            )
            if isinstance(candidate, str) and candidate:
                found = candidate
                break
    _bid_hint.set(found)


def page_bid_hint() -> str | None:
    """Return the Bid hint stored for the page currently rendering."""
    return _bid_hint.get()


def current_request() -> Request | None:
    """Return the request being served in this context, if any."""
    return _current_request.get()


def bid_page_of(path: str, query_bid_id: str | None) -> tuple[str, str] | None:
    """Split a request path into ``(bid_id, path relative to the Bid)``.

    ``/bids/B-1/owner-decision`` -> ``("B-1", "owner-decision")``;
    ``/requirements`` with ``?bid_id=B-1`` -> ``("B-1", "requirements")``.
    Returns None when the page is not scoped to one Bid.
    """
    match = _BID_PATH.match(path)
    if match:
        return match.group("bid_id"), (match.group("rest") or "").strip("/")
    if query_bid_id:
        return query_bid_id, path.strip("/")
    return None


class RequestContextMiddleware:
    """Pure ASGI middleware exposing the active request through :func:`current_request`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _current_request.set(Request(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            _current_request.reset(token)
