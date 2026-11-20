"""Transcript and profile export formats."""

from datetime import datetime
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from .model import Message
from .storage import Session, validate_session_id


PROFILE_LABELS = {
    "taste_anchors": "Taste anchors",
    "style_signature": "Style signature",
    "narrative_desires": "Narrative desires",
    "consumption": "Reading habits",
    "implicit": "Implicit signals",
    "explanations": "Why these preferences fit",
}
LEGACY_PROFILE_LABELS = {
    "reading_philosophy": "Reading philosophy",
    "anti_patterns": "Preferences to avoid",
}
STYLE_LABELS = {
    "prose_density": "Prose density",
    "pacing": "Pacing",
    "tone": "Tone",
    "worldbuilding": "Worldbuilding",
    "character_focus": "Character focus",
}


def atomic_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _value_text(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "Unknown"
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value)


class ProfileFormatter:
    @staticmethod
    def render(profile: dict[str, Any], markdown: bool = True) -> str:
        title = f"Literary profile | {profile.get('reader_archetype') or 'Reader'}"
        lines = [f"# {title}" if markdown else title]
        explanations = profile.get("explanations")
        explanations = explanations if isinstance(explanations, dict) else {}
        for field, heading in LEGACY_PROFILE_LABELS.items():
            value = profile.get(field)
            if value in (None, "", []) or value == explanations.get(field):
                continue
            lines.extend(("", f"## {heading}" if markdown else heading.upper(), _value_text(value)))
        for key, heading in PROFILE_LABELS.items():
            section = profile.get(key)
            if not isinstance(section, dict) or not section:
                continue
            lines.extend(("", f"## {heading}" if markdown else heading.upper()))
            for field, value in section.items():
                label = field.replace("_", " ").capitalize()
                value_text = _value_text(value)
                number = isinstance(value, (int, float)) and not isinstance(value, bool)
                if number and key == "style_signature":
                    value_text += "/100"
                elif number and key == "implicit":
                    value_text = f"{value:.0%}" if markdown else f"{value:.2f}/1.0"
                if markdown:
                    lines.append(f"- **{label}** · {value_text}")
                else:
                    lines.append(f"  {label} · {value_text}")
        metadata = profile.get("_metadata")
        if isinstance(metadata, dict):
            status = str(metadata.get("completion_status", "unknown")).replace("_", " ")
            lines.extend(("", f"Interview responses: {metadata.get('interview_turns', 0)} ({status})."))
            if metadata.get("early_termination"):
                lines.append("This interview ended early. Treat inferred preferences as tentative.")
        return "\n".join(lines) + "\n"

    @classmethod
    def format_for_sharing(cls, profile: dict[str, Any]) -> str:
        return cls.render(profile, markdown=False)

    @staticmethod
    def format_metrics_table(profile: dict[str, Any]) -> str:
        style = profile.get("style_signature") or {}
        return "\n".join(
            f"{label:<18} {_value_text(style.get(field))}/100"
            if style.get(field) is not None else f"{label:<18} Unknown"
            for field, label in STYLE_LABELS.items()
        )


class ProfileSaver:
    def __init__(self, base_dir: str | Path = "user_profiles"):
        self.directory = Path(base_dir).expanduser().resolve()

    def create_user_folder(self, user_id: str) -> Path:
        validate_session_id(user_id)
        folder = self.directory / user_id
        (folder / "logs").mkdir(parents=True, exist_ok=True)
        (folder / "profiles").mkdir(parents=True, exist_ok=True)
        return folder

    def save_conversation_log(
        self, user_id: str, conversation: list[dict], metadata: dict | None = None,
        include_reasoning: bool = True,
    ) -> str:
        folder = self.create_user_folder(user_id)
        when = datetime.now()
        log = self._log(user_id, conversation, metadata or {}, when, include_reasoning)
        return str(atomic_text(folder / "logs" / f"conversation_{when:%Y%m%d_%H%M%S}.json", log))

    def save_profile(self, user_id: str, profile_data: dict, format: str = "json") -> str:
        if format not in ("json", "markdown"):
            raise ValueError("Profile format must be json or markdown.")
        folder = self.create_user_folder(user_id)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        if format == "json":
            return str(atomic_text(folder / "profiles" / f"profile_{stamp}.json", json_text(profile_data)))
        return str(atomic_text(
            folder / "profiles" / f"profile_{stamp}.md", ProfileFormatter.render(profile_data)
        ))

    def save_session_summary(
        self, user_id: str, conversation: list[dict], profile_data: dict, metadata: dict | None = None,
    ) -> dict[str, str]:
        files = self._save_files(user_id, conversation, profile_data, metadata or {}, datetime.now())
        return {key: str(path) for key, path in files.items()}

    def save(self, session: Session) -> dict[str, Path]:
        if not session.is_complete or not session.profile:
            raise ValueError("A completed profile is needed before exporting.")
        when = datetime.fromisoformat(session.finished_at)
        metadata = {"completion_status": session.status}
        if session.status == "complete":
            metadata.update(turns=session.turn_count, completed_at=session.finished_at)
        elif session.status == "early_exit":
            metadata["turn_count"] = session.turn_count
        return self._save_files(
            session.session_id, [message.as_log() for message in session.messages],
            session.profile, metadata, when,
        )

    def _save_files(
        self, user_id: str, conversation: list[dict], profile: dict, metadata: dict, when: datetime,
    ) -> dict[str, Path]:
        stamp = when.strftime("%Y%m%d_%H%M%S")
        folder = self.create_user_folder(user_id)
        paths = {
            "user_folder": folder,
            "log": folder / "logs" / f"conversation_{stamp}.json",
            "profile_json": folder / "profiles" / f"profile_{stamp}.json",
            "profile_markdown": folder / "profiles" / f"profile_{stamp}.md",
            "profile_shareable": folder / "profiles" / f"profile_{stamp}_SHAREABLE.txt",
        }
        documents = {
            "log": self._log(user_id, conversation, metadata, when, True),
            "profile_json": json_text(profile),
            "profile_markdown": ProfileFormatter.render(profile),
            "profile_shareable": ProfileFormatter.format_for_sharing(profile),
        }
        for name, content in documents.items():
            atomic_text(paths[name], content)
        return paths

    @staticmethod
    def _log(
        user_id: str, conversation: list[dict], metadata: dict, when: datetime,
        include_reasoning: bool,
    ) -> str:
        entries = conversation if include_reasoning else [
            {key: value for key, value in item.items() if key != "reasoning_content"}
            for item in conversation
        ]
        return json_text({
            "timestamp": when.isoformat(),
            "user_id": user_id,
            "metadata": metadata,
            "conversation": entries,
            "note": (
                "reasoning_content was supplied by the configured model provider."
                if include_reasoning else None
            ),
        })


def excerpt_reasoning(text: str, limit: int) -> str:
    stripped = text.strip()
    return stripped if len(stripped) <= limit else stripped[:limit] + "…"


def save_reasoning(messages: tuple[Message, ...], path: Path) -> bool:
    records = [
        {
            "turn": offset,
            "role": message.role,
            "content_preview": message.content[:100],
            "reasoning": message.reasoning_content,
        }
        for offset, message in enumerate(messages)
        if message.reasoning_content
    ]
    if records:
        atomic_text(path, json_text(records))
    return bool(records)
