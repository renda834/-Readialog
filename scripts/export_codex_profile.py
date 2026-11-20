"""Save a Readialog interview composed in Codex using the normal profile exporter."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wren import ProfileGeneratorAgent, ProfileSaver
from wren.model import Message
from wren.storage import SESSION_LIMIT, Session


def read_interview(path: Path) -> Session:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("The draft must be a JSON object.")
    data = payload.get("profile")
    if not isinstance(data, dict) or not ProfileGeneratorAgent.validate_profile(data):
        raise ValueError("The draft needs a profile containing taste, style, and narrative sections.")
    records = payload.get("conversation")
    if not isinstance(records, list):
        raise ValueError("The draft needs a list of conversation messages.")
    messages = []
    for index, record in enumerate(records):
        expected_role = "user" if index % 2 == 0 else "assistant"
        if not isinstance(record, dict) or record.get("role") != expected_role:
            raise ValueError("The conversation must alternate roles, starting with a reader answer.")
        content = record.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Every conversation message must contain text.")
        messages.append(Message(expected_role, content))
    turns = sum(message.role == "user" for message in messages)
    if not 1 <= turns <= SESSION_LIMIT:
        raise ValueError("An exported interview must contain between one and twelve reader answers.")
    status = "complete" if turns == SESSION_LIMIT else "early_exit"
    profile = {key: value for key, value in data.items() if key != "_reasoning"}
    profile["_metadata"] = {
        "interview_turns": turns,
        "completion_status": status,
        "early_termination": status == "early_exit",
    }
    now = datetime.now()
    session_id = f"codex_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    return Session(session_id, "", tuple(messages), status, now.isoformat(), profile)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("user_profiles"))
    args = parser.parse_args()
    try:
        session = read_interview(args.draft)
        files = ProfileSaver(args.output_dir).save(session)
    except (OSError, ValueError) as exc:
        print(f"Could not export the interview. {exc}", file=sys.stderr)
        return 1
    print(json.dumps({name: str(path) for name, path in files.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
