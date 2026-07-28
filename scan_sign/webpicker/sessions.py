"""Session lookup for the HTTP layer.

Locally there is exactly one session — the files the CLI already loaded. Hosted, a visitor names
themselves to start one, and it is never resumed: coming back means starting fresh, so nobody
inherits the documents of whoever used the browser before them.
"""

from __future__ import annotations

import secrets
import threading
import time

from .memory import release_free_memory


class SingleSession:
    """One shared session; the local tool has exactly one user."""

    hosted = False

    def __init__(self, session):
        self.session = session

    def resolve(self, cookie_value: str | None):
        return self.session, None

    def start(self, cookie_value: str | None, name: str):
        return self.session, None

    def end(self, cookie_value: str | None) -> None:
        pass

    def sweep(self) -> None:
        pass


class CookieSessions:
    """One session per visitor, created on demand and evicted when idle or at the cap."""

    hosted = True

    def __init__(self, factory, max_sessions: int = 40, ttl_seconds: int = 1800):
        self.factory = factory
        self.max_sessions = max_sessions
        self.ttl = ttl_seconds
        self.lock = threading.Lock()
        self.sessions: dict[str, object] = {}
        self.seen: dict[str, float] = {}

    def resolve(self, cookie_value: str | None):
        """Return (session, new_cookie). Never creates — an unknown cookie has no session."""
        with self.lock:
            session = self.sessions.get(cookie_value) if cookie_value else None
            if session is not None:
                self.seen[cookie_value] = time.monotonic()
            return session, None

    def start(self, cookie_value: str | None, name: str):
        """Always a brand new session; whatever this browser had before is dropped."""
        now = time.monotonic()
        with self.lock:
            dropped = bool(cookie_value) and self._drop(cookie_value)
            dropped = self._evict(now) or dropped
            token = secrets.token_urlsafe(18)
            session = self.factory()
            session.name = name
            self.sessions[token] = session
            self.seen[token] = now
        self._reclaim(dropped)
        return session, token

    def end(self, cookie_value: str | None) -> None:
        with self.lock:
            dropped = bool(cookie_value) and self._drop(cookie_value)
        self._reclaim(dropped)

    def sweep(self) -> None:
        with self.lock:
            dropped = self._evict(time.monotonic())
        self._reclaim(dropped)

    def _reclaim(self, dropped: bool) -> None:
        """A closed session freed a document and its page rasters; give the pages back to the OS.

        Always outside the store lock — trimming takes milliseconds, and every request waits on
        that lock to find its session.
        """
        if dropped:
            release_free_memory()

    def _evict(self, now: float) -> bool:
        dropped = False
        for token, seen in list(self.seen.items()):
            if now - seen > self.ttl:
                dropped = self._drop(token) or dropped
        while len(self.sessions) >= self.max_sessions:
            dropped = self._drop(min(self.seen, key=self.seen.get)) or dropped
        return dropped

    def _drop(self, token: str) -> bool:
        """True when a session was really closed, so callers know there is memory to reclaim."""
        session = self.sessions.pop(token, None)
        self.seen.pop(token, None)
        if session is None:
            return False
        session.close()
        return True
