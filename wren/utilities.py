"""Inspect existing session state and conversation logs."""

import argparse
import json
from pathlib import Path
import sys

from redis.exceptions import RedisError

from .interview import ProfileGeneratorAgent
from .legacy import import_legacy_session
from .model import Message, ModelError, Settings
from .reports import atomic_text, excerpt_reasoning, json_text, save_reasoning
from .storage import RedisSessions, SessionConflict


def read_log(path: Path) -> tuple[list[dict], dict]:
    log = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(log, dict) or not isinstance(log.get("conversation"), list):
        raise ValueError("Expected a ReadWren conversation log.")
    return log["conversation"], log


def show_transcript(records: list[dict], reasoning: bool) -> None:
    for record in records:
        role = record.get("role", "unknown")
        speaker = {"human": "Reader", "user": "Reader", "assistant": "Interviewer", "ai": "Interviewer"}
        print(f"{speaker.get(role, role)}: {record.get('content', '')}")
        if reasoning and record.get("reasoning_content"):
            print(f"  Model reasoning: {excerpt_reasoning(record['reasoning_content'], 300)}")


def list_sessions(_: argparse.Namespace) -> None:
    sessions = RedisSessions.connect(Settings.load()).list()
    print(f"Found {len(sessions)} active Redis record(s).")
    for state, ttl in sessions:
        print(f"{state.session_id}   {state.turn_count}/12   {state.status}   expires in {ttl}s")


def view_session(options: argparse.Namespace) -> None:
    session = saved_session(options.session_id)
    print(f"{session.session_id} · {session.turn_count}/12 · {session.status}")
    show_transcript([item.as_log() for item in session.messages], not options.hide_reasoning)
    if options.reasoning_file:
        if not save_reasoning(session.messages, options.reasoning_file):
            print("The saved transcript contains no provider reasoning.")


def saved_session(session_id: str):
    state = RedisSessions.connect(Settings.load()).get(session_id)
    if state is None:
        raise ValueError("No saved session with that ID. It may have expired.")
    return state


def view_log(options: argparse.Namespace) -> None:
    records, details = read_log(options.log_file)
    print(f"{details.get('user_id', 'Unknown session')} · {details.get('timestamp', '')}")
    print(json_text(details.get("metadata", {})), end="")
    show_transcript(records, not options.hide_reasoning)
    if options.reasoning_file:
        conversation = tuple(Message(**entry) for entry in records)
        if not save_reasoning(conversation, options.reasoning_file):
            print("The saved transcript contains no provider reasoning.")


def retrieve_profile(options: argparse.Namespace) -> None:
    if options.log:
        records, details = read_log(options.log)
        saved = details.get("metadata", {})
        status = saved.get("completion_status", "unknown")
        turns = sum(record.get("role") in ("user", "human") for record in records)
        profile = ProfileGeneratorAgent().generate_profile(
            records,
            {
                "turn_count": turns,
                "completion_status": status,
                "early_termination": status in ("early_exit", "interrupted"),
            },
        )
    else:
        profile = saved_session(options.session_id).profile
        if not profile:
            raise ValueError("This Redis session has no completed profile.")
    output = json_text(profile)
    if options.output:
        atomic_text(options.output, output)
    print(output, end="")


def migrate_session(options: argparse.Namespace) -> None:
    store = RedisSessions.connect(Settings.load())
    profile = json.loads(options.profile.read_text(encoding="utf-8-sig")) if options.profile else None
    if options.profile and not isinstance(profile, dict):
        raise ValueError("The supplied file must contain a JSON profile object.")
    session = import_legacy_session(store, options.session_id, profile)
    print(f"Imported {session.session_id} · {session.turn_count}/12 · {session.status}")
    if not session.is_complete and session.turn_count == 12:
        print("The checkpoint has all answers and no saved profile. Resuming will generate it.")


def run_utility(operation: str) -> int:
    parser = argparse.ArgumentParser()
    if operation == "view_redis_sessions":
        action = list_sessions
    elif operation == "import_legacy_session":
        action = migrate_session
        parser.add_argument("session_id")
        parser.add_argument("--profile", type=Path, help="attach the original exported JSON profile")
    elif operation == "retrieve_profile":
        action = retrieve_profile
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--log", type=Path, help="make a new model request from a local log")
        group.add_argument("session_id", nargs="?", help="read an already generated profile from Redis")
        parser.add_argument("--output", type=Path)
    else:
        parser.add_argument("--hide-reasoning", action="store_true")
        parser.add_argument("--reasoning-file", type=Path)
        if operation == "view_session_conversation":
            action = view_session
            parser.add_argument("session_id")
        else:
            action = view_log
            parser.add_argument("log_file", type=Path)
    options = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        action(options)
    except (ValueError, OSError, ModelError, TypeError, RedisError, SessionConflict) as exc:
        print(f"Could not complete the request. {exc}", file=sys.stderr)
        return 1
    return 0
