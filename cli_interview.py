"""Start or resume an interactive ReadWren interview."""

import argparse
from datetime import datetime
from getpass import GetPassWarning, getpass
from pathlib import Path
import sys
from uuid import uuid4
import warnings

from redis.exceptions import RedisError

from wren import InterviewAgent, ProfileSaver
from wren.interview import OPENING
from wren.model import ModelError, ProviderChat, Settings
from wren.reports import excerpt_reasoning, json_text
from wren.storage import SESSION_LIMIT, SessionConflict


RECOVERABLE = (ModelError, OSError, RedisError, SessionConflict, ValueError)
OPENING_ZH = (
    "请说说三部你喜欢的作品，再说一部你不喜欢或没读完的。书或故事都可以。"
    "分别是什么让你想继续读，或者让你放下了？"
)


class InterviewCancelled(Exception):
    """The reader left the hidden API key prompt without entering a key."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--session", metavar="ID", help="start an interview with this ID")
    choice.add_argument("--resume", metavar="ID", help="reopen an interview stored in Redis")
    parser.add_argument("--output-dir", type=Path, default=Path("user_profiles"))
    parser.add_argument("--show-reasoning", action=argparse.BooleanOptionalAction)
    parser.add_argument("--prompt-deepseek-key", action="store_true", help="ask for a DeepSeek key in the terminal")
    return parser.parse_args()


def prompt_deepseek_key(prompt: str = "粘贴 DeepSeek API Key 后按回车（输入内容会隐藏） > ") -> str:
    interactive_terminal_required = "请双击「启动阅谈.cmd」，或在交互式终端中运行。"
    if not sys.stdin.isatty():
        raise ValueError(interactive_terminal_required)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            key = getpass(prompt).strip()
    except GetPassWarning as exc:
        raise ValueError(interactive_terminal_required) from exc
    except (EOFError, KeyboardInterrupt):
        key = ""
    if not key:
        raise InterviewCancelled
    return key


def request_new_deepseek_key() -> str:
    print("DeepSeek 拒绝了当前 Key（401）。请从 DeepSeek 开放平台复制完整的 API Key。", file=sys.stderr)
    return prompt_deepseek_key("粘贴 Key 后按回车以重试刚才的操作（隐藏输入，直接回车退出） > ")


def show_progress(answered: int) -> None:
    line = "#" * answered + "." * (SESSION_LIMIT - answered)
    print(f"Progress [{line}] {answered}/{SESSION_LIMIT}")


def wants_profile_retry() -> bool:
    try:
        return input("Retry profile generation? (y/N): ").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def close_interview(agent: InterviewAgent, session_id: str, status: str):
    while True:
        try:
            return agent.finish(session_id, status)
        except RECOVERABLE as exc:
            print(f"Could not finish the profile. {exc}", file=sys.stderr)
            if not wants_profile_retry():
                return None


def main() -> int:
    try:
        return run_interview(arguments())
    except InterviewCancelled:
        print("本次访谈已取消。")
        return 0


def run_interview(options: argparse.Namespace) -> int:
    session_id = options.resume or options.session or (
        f"cli_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    )
    easy_start = options.prompt_deepseek_key
    answer_prompt = "请输入回答（输入 quit 可提前生成画像） > " if easy_start else "Your response (or 'quit'): "
    try:
        if easy_start:
            settings = Settings.load(provider="deepseek", api_key=prompt_deepseek_key())
            model = ProviderChat(settings, request_new_key=request_new_deepseek_key)
            agent = InterviewAgent(model=model, settings=settings)
        else:
            agent = InterviewAgent(settings=Settings.load())
        show_reasoning = bool(options.show_reasoning)
        if options.show_reasoning is None and not easy_start:
            try:
                answer = input("Show reasoning supplied by the model? (y/N): ")
            except (EOFError, KeyboardInterrupt):
                return 0
            show_reasoning = answer.strip().lower() == "y"
        print("Readialog | Literary interview")
        print(agent.storage_status)
        print(f"Session ID: {session_id}")
        if options.resume:
            session = agent.resume_interview(session_id)
            if not session.is_complete and session.turn_count < SESSION_LIMIT:
                question = session.messages[-1].content if session.messages else session.opening
                print(f"Interviewer: {question}")
                show_progress(session.turn_count)
        else:
            first = agent.start_interview(session_id, opening=OPENING_ZH if easy_start else OPENING)
            print(f"Interviewer: {first['message']}")
            show_progress(0)
    except RECOVERABLE as exc:
        print(f"Could not start the interview. {exc}", file=sys.stderr)
        return 1

    previous_step_failed = False
    while True:
        try:
            session = agent.session(session_id)
        except RECOVERABLE as exc:
            print(f"Could not load the interview. {exc}", file=sys.stderr)
            return 1

        if session.is_complete:
            break
        if session.turn_count >= SESSION_LIMIT:
            if previous_step_failed and not wants_profile_retry():
                return 1
            session = close_interview(agent, session_id, "complete")
            break

        try:
            response = input(answer_prompt).strip()
            if len(response) > 2000:
                print(f"This response has {len(response)} characters.")
                if input("Keep it in full? (y/N): ").strip().lower() != "y":
                    response = response[:2000]
                    print("Using the first 2,000 characters.")
        except (KeyboardInterrupt, EOFError):
            print("\nInterview interrupted. Building a profile from your saved answers.")
            session = close_interview(agent, session_id, "interrupted")
            break
        if not response:
            print("Enter an answer to continue.")
            continue
        if response.lower() in ("q", "quit", "exit"):
            print("Building a profile from the answers so far.")
            session = close_interview(agent, session_id, "early_exit")
            break

        try:
            result = agent.send_message(response, session_id)
        except RECOVERABLE as exc:
            print(f"Could not complete this step. {exc}", file=sys.stderr)
            previous_step_failed = True
            continue
        previous_step_failed = False
        print(f"Interviewer: {result['message']}")
        if show_reasoning and result.get("reasoning_content"):
            print(f"Model reasoning: {excerpt_reasoning(result['reasoning_content'], 300)}")
        show_progress(result["turn_count"])

    if session is None:
        return 1
    if show_reasoning and session.profile.get("_reasoning"):
        print(f"Profile reasoning: {excerpt_reasoning(session.profile['_reasoning'], 500)}")
    print("Your literary profile")
    print(json_text(session.profile), end="")
    try:
        paths = ProfileSaver(options.output_dir).save(session)
    except (ValueError, OSError) as exc:
        print(f"Export failed. {exc}", file=sys.stderr)
        return 1
    print(f"Saved transcript and three profile formats in {paths['user_folder']}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
