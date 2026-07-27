"""Session lookup for the HTTP layer.

Locally there is exactly one session — the files the CLI already loaded. Hosted, every browser
gets its own, keyed by a cookie, so two people are never looking at the same document.
"""

from __future__ import annotations

import secrets
import threading
import time


class SingleSession:
    """One shared session; the local tool has exactly one user."""

    hosted = False

    def __init__(self, session):
        self.session = session

    def resolve(self, cookie_value: str | None):
        return self.session, None

    def sweep(self) -> None:
        pass


class CookieSessions:
    """One session per browser, evicted when idle or when the cap is reached."""

    hosted = True

    def __init__(self, factory, max_sessions: int = 40, ttl_seconds: int = 1800):
        self.factory = factory
        self.max_sessions = max_sessions
        self.ttl = ttl_seconds
        self.lock = threading.Lock()
        self.sessions: dict[str, object] = {}
        self.seen: dict[str, float] = {}

    def resolve(self, cookie_value: str | None):
        """Return (session, new_cookie_or_None)."""
        now = time.monotonic()
        with self.lock:
            if cookie_value and cookie_value in self.sessions:
                self.seen[cookie_value] = now
                return self.sessions[cookie_value], None

            self._evict(now)
            token = secrets.token_urlsafe(18)
            self.sessions[token] = self.factory()
            self.seen[token] = now
            return self.sessions[token], token

    def sweep(self) -> None:
        with self.lock:
            self._evict(time.monotonic())

    def _evict(self, now: float) -> None:
        for token, seen in list(self.seen.items()):
            if now - seen > self.ttl:
                self._drop(token)
        while len(self.sessions) >= self.max_sessions:
            oldest = min(self.seen, key=self.seen.get)
            self._drop(oldest)

    def _drop(self, token: str) -> None:
        session = self.sessions.pop(token, None)
        self.seen.pop(token, None)
        if session is not None:
            session.close()
