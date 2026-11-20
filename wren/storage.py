"""Session storage with optimistic updates and a 24-hour Redis expiry."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import json
import re
from threading import Lock
from typing import Any, Protocol

from redis import Redis
from redis.exceptions import RedisError, WatchError

from .model import Message, Settings


TTL_SECONDS = 24 * 60 * 60
SESSION_LIMIT = 12


def validate_session_id(session_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,95}", session_id):
        raise ValueError("Session IDs may contain ASCII letters, numbers, underscores and hyphens.")


@dataclass(frozen=True)
class Session:
    session_id: str
    opening: str
    messages: tuple[Message, ...] = ()
    status: str = "active"
    finished_at: str = ""
    profile: dict[str, Any] = field(default_factory=dict)
    revision: int = 0

    @property
    def turn_count(self) -> int:
        return sum(message.role == "user" for message in self.messages)

    @property
    def is_complete(self) -> bool:
        return self.status != "active"

    @classmethod
    def decode(cls, raw: str | bytes) -> "Session":
        data = json.loads(raw)
        data["messages"] = tuple(Message(**row) for row in data["messages"])
        return cls(**data)


class SessionConflict(RuntimeError):
    """An interview was modified by another process before this update."""


class SessionStore(Protocol):
    def get(self, session_id: str) -> Session | None: ...

    def put(self, session: Session) -> Session: ...


def next_revision(previous: Session | None, current: Session) -> Session:
    if (previous.revision if previous else 0) != current.revision:
        raise SessionConflict("The session changed elsewhere. Reopen it before submitting again.")
    return replace(current, revision=current.revision + 1)


class MemorySessions:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return deepcopy(self._sessions.get(session_id))

    def put(self, session: Session) -> Session:
        with self._lock:
            updated = next_revision(self._sessions.get(session.session_id), session)
            self._sessions[session.session_id] = deepcopy(updated)
            return updated


class RedisSessions:
    _prefix = "readwren:session:"

    def __init__(self, client: Redis, ttl: int = TTL_SECONDS):
        self.client = client
        self.ttl = ttl

    def _key(self, session_id: str) -> str:
        validate_session_id(session_id)
        return f"{self._prefix}{session_id}"

    @classmethod
    def connect(cls, settings: Settings) -> "RedisSessions":
        if not settings.redis_host:
            raise ValueError("Set REDIS_HOST to access saved sessions.")
        client = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        client.ping()
        return cls(client)

    def get(self, session_id: str) -> Session | None:
        raw = self.client.get(self._key(session_id))
        return Session.decode(raw) if raw else None

    def put(self, session: Session) -> Session:
        key = self._key(session.session_id)
        with self.client.pipeline() as transaction:
            try:
                transaction.watch(key)
                entry = transaction.get(key)
                updated = next_revision(Session.decode(entry) if entry else None, session)
                transaction.multi()
                transaction.set(key, json.dumps(asdict(updated), ensure_ascii=False), ex=self.ttl)
                transaction.execute()
            except WatchError as exc:
                raise SessionConflict("The same session was updated elsewhere. Reopen it.") from exc
        return updated

    def list(self) -> list[tuple[Session, int]]:
        results = []
        for key in self.client.scan_iter(match=f"{self._prefix}*"):
            raw = self.client.get(key)
            if raw:
                ttl = self.client.ttl(key)
                if ttl >= 0:
                    results.append((Session.decode(raw), ttl))
        return sorted(results, key=lambda item: item[0].session_id)


def configured_store(settings: Settings) -> tuple[SessionStore, str]:
    if settings.redis_host:
        try:
            return RedisSessions.connect(settings), "Redis session persistence enabled."
        except RedisError:
            return MemorySessions(), "Redis unavailable. This process will keep sessions in memory."
    return MemorySessions(), "This process will keep sessions in memory."
