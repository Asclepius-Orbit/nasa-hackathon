"""Authentication of the /ui API: a token shared with the Electron main process, and the astronaut it has unlocked.

Every /ui request carries `Authorization: Bearer <token>`; a missing or wrong token is a 401. Requests about one astronaut
also carry `X-Astronaut-Id` (the astronaut unlocked in the UI): the astronaut of the path, or the owner of the session or
command of the path, must be that one, else 403. The crew list and the registration of an id (`GET /ui/crew`,
`PUT /ui/crew/{id}`) need the token only: the main process does them for the whole crew, with nobody unlocked.
An unknown session or command is left to the route (404). /health and the read-only API are outside /ui and need nothing.

The token comes from the settings or from a file next to the database, generated on first start (owner-only permissions).
"""

import logging
import os
import secrets
from collections.abc import Awaitable, Callable

from fastapi import Request
from spine_contracts import topics

from monitor.db import Store
from monitor.settings import AppSettings

_log = logging.getLogger(__name__)

ASTRONAUT_HEADER = "x-astronaut-id"
# Crew-level routes: the token is enough.
_CREW_ROUTES = {("GET", "/ui/crew"), ("PUT", "/ui/crew/{astronaut_id}")}


class AuthProblem(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def ui_token_of(settings: AppSettings) -> str:
    """The configured token; else the one in the token file, created with a fresh random token when missing."""
    if settings.ui_token is not None and settings.ui_token.get_secret_value():
        return settings.ui_token.get_secret_value()
    path = settings.ui_token_file or settings.db_path.parent / "ui-token"
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(token)
    # The mode of os.open applies only on creation: tighten a file that already existed (e.g. an empty one made with touch).
    os.chmod(path, 0o600)
    _log.info("generated the /ui token in %s", path)
    return token


def _bearer(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def _owner(store: Store, params: dict[str, str]) -> str | None:
    """The astronaut the request is about; None when the path names none, an invalid one, or an unknown session or command."""
    if "astronaut_id" in params:
        # Not a pseudonym: the route answers 422, as it does for anyone.
        return params["astronaut_id"] if topics.ASTRONAUT_ID.fullmatch(params["astronaut_id"]) else None
    if "session_id" in params:
        row = store.session(params["session_id"])
        return None if row is None else row.payload["astronaut_id"]
    if "command_id" in params:
        command = store.command(params["command_id"])
        return None if command is None else command.payload["astronaut_id"]
    return None


def ui_guard(token: str, store: Store) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency of the /ui router."""

    async def guard(request: Request) -> None:
        given = _bearer(request)
        if given is None or not secrets.compare_digest(given.encode(), token.encode()):
            raise AuthProblem(401, "missing or invalid token")
        route = request.scope.get("route")
        if (request.method, getattr(route, "path", "")) in _CREW_ROUTES:
            return
        unlocked = request.headers.get(ASTRONAUT_HEADER)
        if not unlocked:
            raise AuthProblem(403, "no unlocked astronaut (X-Astronaut-Id)")
        owner = _owner(store, request.path_params)
        if owner is not None and owner != unlocked:
            raise AuthProblem(403, "the request is about another astronaut than the one unlocked")

    return guard
