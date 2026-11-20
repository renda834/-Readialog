"""Import conversation data from the original ReadWren Redis format."""

from datetime import datetime
from io import BytesIO
import json
import pickle
import pickletools
from typing import Any

from .interview import FINISH_STATUSES, OPENING, ProfileGeneratorAgent
from .model import Message
from .storage import RedisSessions, SESSION_LIMIT, TTL_SECONDS, Session, validate_session_id


# The published checkpoint format needs basic data operations and two message records.
# In particular, the import does not allow REDUCE, extension codes or persistent IDs.
DATA_OPS = frozenset("""
    PROTO FRAME STOP MARK MEMOIZE BINGET LONG_BINGET BINPUT LONG_BINPUT
    NONE NEWTRUE NEWFALSE BININT BININT1 BININT2 LONG1 LONG4 BINFLOAT
    SHORT_BINUNICODE BINUNICODE BINUNICODE8 SHORT_BINBYTES BINBYTES BINBYTES8
    EMPTY_DICT DICT SETITEM SETITEMS EMPTY_LIST LIST APPEND APPENDS
    EMPTY_TUPLE TUPLE TUPLE1 TUPLE2 TUPLE3 EMPTY_SET ADDITEMS FROZENSET
    STACK_GLOBAL NEWOBJ BUILD
""".split())
MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024


class _MessageRecord:
    __slots__ = ("content", "reasoning")
    role = ""

    def __setstate__(self, state: dict):
        if not isinstance(state, dict) or not isinstance(state.get("__dict__"), dict):
            raise pickle.UnpicklingError("Unknown saved message structure.")
        fields = state["__dict__"]
        content = fields.get("content")
        if not isinstance(content, str):
            raise pickle.UnpicklingError("The original interview must contain text messages.")
        extra = fields.get("additional_kwargs") or {}
        reasoning = extra.get("reasoning_content") if isinstance(extra, dict) else None
        self.content = content
        self.reasoning = reasoning if isinstance(reasoning, str) else ""


class _ReaderRecord(_MessageRecord):
    __slots__ = ()
    role = "user"


class _InterviewerRecord(_MessageRecord):
    __slots__ = ()
    role = "assistant"


class _CheckpointReader(pickle.Unpickler):
    def find_class(self, module: str, name: str):
        allowed_records = {
            ("langchain_core.messages.human", "HumanMessage"): _ReaderRecord,
            ("langchain_core.messages.ai", "AIMessage"): _InterviewerRecord,
        }
        if (record := allowed_records.get((module, name))) is None:
            raise pickle.UnpicklingError("Unsupported object in the original checkpoint.")
        return record


def checkpoint_values(raw: bytes, session_id: str) -> dict:
    if len(raw) > MAX_CHECKPOINT_BYTES:
        raise ValueError("The original checkpoint exceeds the supported size.")
    try:
        if raw[:2] not in (b"\x80\x04", b"\x80\x05"):
            raise pickle.UnpicklingError("Unknown checkpoint protocol.")
        for opcode, _, offset in pickletools.genops(raw):
            if opcode.name not in DATA_OPS:
                raise pickle.UnpicklingError("Unsupported operation in the original checkpoint.")
            if opcode.name == "STOP" and offset != len(raw) - 1:
                raise pickle.UnpicklingError("Unexpected data after the original checkpoint.")
        data = _CheckpointReader(BytesIO(raw)).load()
        if data["config"]["configurable"]["thread_id"] != session_id:
            raise ValueError("The Redis key and checkpoint identify different sessions.")
        values = data["checkpoint"]["channel_values"]
        if not isinstance(values, dict):
            raise TypeError("Unknown channel structure.")
        return values
    except (pickle.UnpicklingError, EOFError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError(f"Could not import the original checkpoint. {exc}") from exc


def conversation(values: dict) -> tuple[Message, ...]:
    records = values.get("messages", [])
    if not isinstance(records, list):
        raise ValueError("The original checkpoint does not contain a message list.")
    seen = set()
    messages = []
    for record in records:
        if not isinstance(record, _MessageRecord) or not hasattr(record, "content"):
            raise ValueError("Unsupported message record in the original checkpoint.")
        if id(record) in seen:
            continue
        seen.add(id(record))
        expected = "assistant" if len(messages) % 2 else "user"
        if record.role != expected:
            raise ValueError("The original messages are out of order.")
        messages.append(Message(record.role, record.content, record.reasoning))
    if len(messages) > SESSION_LIMIT * 2:
        raise ValueError("The original session contains more than twelve interview rounds.")
    return tuple(messages)


def import_legacy_session(
    store: RedisSessions, session_id: str, exported_profile: dict[str, Any] | None = None,
) -> Session:
    validate_session_id(session_id)
    if store.get(session_id) is not None:
        raise ValueError("This session already exists in the new store. Open it with --resume.")
    key = f"langgraph:checkpoint:{session_id}:latest"
    with store.client.pipeline(transaction=True) as snapshot:
        raw, ttl = snapshot.get(key).ttl(key).execute()
    if not raw or ttl in (-2, 0):
        raise ValueError("The original Redis checkpoint is missing or expired.")
    values = checkpoint_values(raw, session_id)
    messages = conversation(values)
    turns = sum(message.role == "user" for message in messages)
    status = "active"
    finished_at = ""
    profile = exported_profile
    if profile is None and values.get("is_complete"):
        candidate = values.get("profile_data")
        if isinstance(candidate, dict) and ProfileGeneratorAgent.validate_profile(candidate):
            profile = candidate

    if profile is not None:
        if not isinstance(profile, dict) or not ProfileGeneratorAgent.validate_profile(profile):
            raise ValueError("The supplied file does not contain the required profile sections.")
        metadata = profile.get("_metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("The supplied profile contains invalid interview metadata.")
        if metadata.get("interview_turns", turns) != turns:
            raise ValueError("The profile and the original checkpoint have different response counts.")
        status = metadata.get("completion_status")
        if status not in FINISH_STATUSES:
            status = "complete" if turns == SESSION_LIMIT else "early_exit"
        if status == "complete" and turns != SESSION_LIMIT:
            raise ValueError("The supplied profile marks a full interview with fewer than twelve answers.")
        try:
            json.dumps(profile, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("The supplied profile contains unsupported data.") from exc
        finished_at = datetime.now().isoformat()

    remaining = min(TTL_SECONDS, ttl) if ttl > 0 else TTL_SECONDS
    imported = Session(session_id, OPENING, messages, status, finished_at, profile or {})
    return RedisSessions(store.client, ttl=remaining).put(imported)
