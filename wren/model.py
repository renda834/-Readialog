"""Configuration and the OpenAI-compatible chat API boundary."""

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


PROVIDER_DEFAULTS = {
    "deepseek": ("https://api.deepseek.com", "deepseek-flash", "deepseek-flash"),
    "moonshot": ("https://api.moonshot.cn/v1", "kimi-k2-thinking-turbo", "kimi-k2-thinking"),
}


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    base_url: str = ""
    interview_model: str = ""
    profile_model: str = ""
    interview_max_tokens: int = 800
    profile_max_tokens: int = 4000
    redis_host: str = ""
    redis_port: int = 6379
    redis_password: str = ""
    debug: bool = False
    provider: str = "moonshot"
    deepseek_thinking: str = "disabled"

    def __post_init__(self) -> None:
        if self.provider not in PROVIDER_DEFAULTS:
            raise ValueError("READWREN_PROVIDER must be deepseek or moonshot.")
        fields = ("base_url", "interview_model", "profile_model")
        for name, default in zip(fields, PROVIDER_DEFAULTS[self.provider]):
            if not getattr(self, name):
                object.__setattr__(self, name, default)

    @classmethod
    def load(cls, *, provider: str | None = None, api_key: str | None = None) -> "Settings":
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        provider = (provider if provider is not None else os.getenv("READWREN_PROVIDER", "")).strip().lower()
        if not provider:
            has_legacy_key = os.getenv("MOONSHOT_API_KEY", "").strip()
            has_deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
            provider = "moonshot" if has_legacy_key and not has_deepseek_key else "deepseek"
        defaults = cls(provider=provider)
        prefix = provider.upper()
        return cls(
            api_key=(api_key if api_key is not None else os.getenv(f"{prefix}_API_KEY", "")).strip(),
            base_url=os.getenv(f"{prefix}_BASE_URL", defaults.base_url).strip(),
            interview_model=os.getenv(f"{prefix}_INTERVIEW_MODEL", defaults.interview_model).strip(),
            profile_model=os.getenv(f"{prefix}_PROFILE_MODEL", defaults.profile_model).strip(),
            interview_max_tokens=int(os.getenv(f"{prefix}_INTERVIEW_MAX_TOKENS", str(defaults.interview_max_tokens))),
            profile_max_tokens=int(os.getenv(f"{prefix}_PROFILE_MAX_TOKENS", str(defaults.profile_max_tokens))),
            redis_host=os.getenv("REDIS_HOST", "").strip(),
            redis_port=int(os.getenv("REDIS_PORT", str(defaults.redis_port))),
            redis_password=os.getenv("REDIS_PASSWORD", ""),
            debug=os.getenv("DEBUG_MODE", "").lower() == "true",
            provider=provider,
            deepseek_thinking=os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower(),
        )


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    reasoning_content: str = ""

    def as_log(self) -> dict[str, str]:
        record = {"role": self.role, "content": self.content}
        if self.reasoning_content:
            record["reasoning_content"] = self.reasoning_content
        return record


class ChatModel(Protocol):
    def complete(
        self, messages: list[Message], *, model: str, max_tokens: int
    ) -> Message: ...


class ModelError(RuntimeError):
    """The provider failed to return a usable response."""


class ProviderChat:
    def __init__(
        self, settings: Settings, timeout: float = 120, *, request_new_key: Callable[[], str] | None = None
    ):
        prefix = settings.provider.upper()
        if not settings.api_key:
            raise ValueError(f"Set {prefix}_API_KEY in .env or in your environment.")
        if not settings.base_url.startswith(("http://", "https://")):
            raise ValueError(f"{prefix}_BASE_URL must be an HTTP or HTTPS URL.")
        if min(settings.interview_max_tokens, settings.profile_max_tokens) < 1:
            raise ValueError("The model token limits must both be positive.")
        if settings.provider == "deepseek" and settings.deepseek_thinking not in ("enabled", "disabled"):
            raise ValueError("DEEPSEEK_THINKING must be enabled or disabled.")
        self.settings = settings
        self.timeout = timeout
        self._api_key = settings.api_key
        self._request_new_key = request_new_key

    def complete(
        self, messages: list[Message], *, model: str, max_tokens: int
    ) -> Message:
        return self._request(messages, model=model, max_tokens=max_tokens)

    def complete_profile(
        self, messages: list[Message], *, model: str, max_tokens: int
    ) -> Message:
        return self._request(messages, model=model, max_tokens=max_tokens, json_output=True)

    def _request(
        self, messages: list[Message], *, model: str, max_tokens: int, json_output: bool = False
    ) -> Message:
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "stream": False,
        }
        deepseek = self.settings.provider == "deepseek"
        if deepseek:
            # This workflow has no tools. DeepSeek only needs prior dialog text.
            body["messages"] = [{"role": item.role, "content": item.content} for item in messages]
            body["thinking"] = {"type": self.settings.deepseek_thinking}
            if json_output:
                body["response_format"] = {"type": "json_object"}
        else:
            body["messages"] = [item.as_log() for item in messages]
        request = Request(
            f"{self.settings.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            while True:
                request.add_header("Authorization", f"Bearer {self._api_key}")
                try:
                    with urlopen(request, timeout=self.timeout) as response:
                        result = json.load(response)
                    break
                except HTTPError as exc:
                    if exc.code != 401 or self._request_new_key is None:
                        raise
                    exc.close()
                    self._api_key = self._request_new_key()
            choice = result["choices"][0]
            message = choice["message"]
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                reason = choice.get("finish_reason")
                raise ModelError(f"The model returned no text ({reason or 'unknown reason'}).")
            reasoning = message.get("reasoning_content")
            return Message("assistant", content.strip(), reasoning if isinstance(reasoning, str) else "")
        except HTTPError as exc:
            name = "DeepSeek" if deepseek else "Moonshot"
            raise ModelError(f"{name} returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError) as exc:
            raise ModelError("Could not reach the model endpoint.") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("The provider response does not match the chat API format.") from exc
