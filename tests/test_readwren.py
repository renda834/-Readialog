"""Exercise the public terminal and script commands with local provider fixtures."""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pickle
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from fakeredis import FakeRedis, TcpFakeServer
from langchain_core.messages import AIMessage, HumanMessage
from redis import Redis

from wren import ProfileSaver
from wren.interview import ProfileError, analyze_conversation, parse_profile
from wren.legacy import import_legacy_session
from wren.model import Message, Settings
from wren.storage import RedisSessions, Session, SessionConflict, TTL_SECONDS


ROOT = Path(__file__).resolve().parents[1]
PROFILE = {
    "taste_anchors": {"loves": ["《尤利西斯》"], "hates": [], "inferred_genres": ["modernism"]},
    "style_signature": {
        "prose_density": 81, "pacing": 38, "tone": 29, "worldbuilding": 10, "character_focus": 87
    },
    "narrative_desires": {"wish": "A close study of a reader.", "themes": ["memory"]},
    "consumption": {"daily_time_minutes": 40},
    "implicit": {"vocabulary_richness": 0.72},
    "explanations": {"pacing": "The reader seeks measured development."},
    "reader_archetype": "Patient Reader",
}


def old_checkpoint(session_id, answers, pending=None):
    transcript = []
    for text in answers:
        inputs = [*transcript, HumanMessage(content=text)]
        transcript = [
            *inputs, *inputs,
            AIMessage(content="What would you like to read?", additional_kwargs={"reasoning_content": "fixture"}),
        ]
    if pending is not None:
        transcript.append(HumanMessage(content=pending))
    return pickle.dumps({
        "checkpoint": {"channel_values": {
            "messages": transcript, "turn_count": 0, "is_complete": False, "profile_data": {},
        }},
        "config": {"configurable": {"thread_id": session_id}},
    }, protocol=4)


class ChatFixture(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        call = json.loads(self.rfile.read(length))
        call["path"] = self.path
        model = call["model"]
        expected_key = self.server.required_keys.get(model, "offline-test")
        call["authenticated"] = self.headers.get("Authorization") == f"Bearer {expected_key}"
        self.server.calls.append(call)
        if call["model"] == "profile-test" or call.get("response_format") == {"type": "json_object"}:
            status = self.server.profile_status
            content = json.dumps(PROFILE, ensure_ascii=False)
            if self.server.fenced:
                content = "```json\n" + content + "\n```"
            if self.server.profile_override is not None:
                content = self.server.profile_override
        else:
            status = self.server.question_status
            content = "What makes a slower story work for you?"
        if model in self.server.required_keys and not call["authenticated"]:
            status = 401
        body = json.dumps({
            "choices": [{
                "message": {"content": content, "reasoning_content": "model fixture reasoning"},
                "finish_reason": "stop",
            }]
        }).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_service(server, cleanup):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def close():
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    cleanup(close)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ChatFixture)
        self.server.calls = []
        self.server.required_keys = {}
        self.server.fenced = False
        self.server.profile_status = 200
        self.server.profile_override = None
        self.server.question_status = 200
        start_service(self.server, self.addCleanup)
        self.env = dict(os.environ)
        self.env.update(
            READWREN_PROVIDER="moonshot",
            MOONSHOT_API_KEY="offline-test",
            MOONSHOT_BASE_URL=f"http://127.0.0.1:{self.server.server_port}/v1",
            MOONSHOT_INTERVIEW_MODEL="questions-test",
            MOONSHOT_PROFILE_MODEL="profile-test",
            MOONSHOT_INTERVIEW_MAX_TOKENS="800",
            MOONSHOT_PROFILE_MAX_TOKENS="4000",
            REDIS_HOST="",
            REDIS_PORT="6379",
            REDIS_PASSWORD="",
            NO_PROXY="127.0.0.1,localhost",
            LANGSMITH_TRACING="false",
            LANGCHAIN_TRACING_V2="false",
        )

    def run_tool(self, *args, data=""):
        return subprocess.run(
            [sys.executable, *map(str, args)],
            cwd=ROOT,
            env=self.env,
            input=data,
            encoding="utf-8",
            capture_output=True,
            timeout=35,
        )

    def interview(self, session_id: str, data: str, *flags):
        return self.run_tool("cli_interview.py", "--session", session_id, "--output-dir", self.out, *flags, data=data)

    def files(self, session_id):
        folder = self.out / session_id
        logs = list((folder / "logs").glob("conversation_*.json"))
        reports = list((folder / "profiles").glob("profile_*.json"))
        markdown = list((folder / "profiles").glob("profile_*.md"))
        shareable = list((folder / "profiles").glob("profile_*_SHAREABLE.txt"))
        self.assertEqual([len(logs), len(reports), len(markdown), len(shareable)], [1, 1, 1, 1])
        return (
            logs[0],
            json.loads(logs[0].read_text(encoding="utf-8")),
            json.loads(reports[0].read_text(encoding="utf-8")),
            markdown[0].read_text(encoding="utf-8"),
            shareable[0].read_text(encoding="utf-8"),
        )

    def enable_redis_fixture(self):
        redis = TcpFakeServer(("127.0.0.1", 0), server_type="redis")
        start_service(redis, self.addCleanup)
        self.env.update(REDIS_HOST="127.0.0.1", REDIS_PORT=str(redis.server_address[1]))
        return Redis(host="127.0.0.1", port=redis.server_address[1])

    def test_full_interview_exports_and_provider_contract(self):
        answers = [f"I love this book because of its plot and voice. I read daily. {i}" for i in range(12)]
        result = self.interview("complete", "\n".join(answers) + "\n", "--show-reasoning")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("12/12", result.stdout)
        self.assertIn("Model reasoning: model fixture reasoning", result.stdout)
        _, log, profile, markdown, shareable = self.files("complete")
        self.assertEqual(profile["_metadata"], {
            "interview_turns": 12, "completion_status": "complete", "early_termination": False
        })
        self.assertEqual(profile["taste_anchors"], PROFILE["taste_anchors"])
        self.assertEqual([item["content"] for item in log["conversation"] if item["role"] == "user"], answers)
        self.assertEqual(len(log["conversation"]), 24)
        self.assertEqual(log["metadata"]["turns"], 12)
        self.assertIn("《尤利西斯》", markdown)
        self.assertIn("81/100", shareable)
        self.assertNotIn("model fixture reasoning", shareable)
        self.assertEqual([call["max_tokens"] for call in self.server.calls], [800] * 11 + [4000])
        self.assertTrue(all(call["authenticated"] and call["path"] == "/v1/chat/completions"
                            for call in self.server.calls))
        self.assertTrue(all("thinking" not in call and "response_format" not in call
                            for call in self.server.calls))
        self.assertIn("question 2 of 12", self.server.calls[0]["messages"][0]["content"])
        self.assertEqual(
            self.server.calls[0]["messages"][-1], {"role": "user", "content": answers[0]}
        )
        self.assertEqual([len(call["messages"]) for call in self.server.calls[:-1]], list(range(3, 24, 2)))
        self.assertEqual(self.server.calls[1]["messages"][-2]["reasoning_content"], "model fixture reasoning")
        self.assertIn("Reading preference rubric", self.server.calls[-1]["messages"][0]["content"])

    def interview_with_key_prompts(self, session_id, keys, data=""):
        self.env.update(
            MOONSHOT_API_KEY="unused-moonshot-key",
            DEEPSEEK_API_KEY="unused-deepseek-key",
            DEEPSEEK_BASE_URL=self.env["MOONSHOT_BASE_URL"],
            DEEPSEEK_INTERVIEW_MODEL="questions-test",
            DEEPSEEK_PROFILE_MODEL="profile-test",
            DEEPSEEK_THINKING="disabled",
        )
        command = (
            "import runpy, sys; from unittest.mock import patch\n"
            "with patch.object(sys.stdin, 'isatty', return_value=True), "
            f"patch('getpass.getpass', side_effect={keys!r}):\n"
            "    runpy.run_path('cli_interview.py', run_name='__main__')\n"
        )
        return self.run_tool(
            "-X", "utf8", "-c", command, "--prompt-deepseek-key", "--session", session_id,
            "--output-dir", self.out, data=data,
        )

    def test_deepseek_prompt_starts_an_interview_with_the_entered_key(self):
        answer = "我喜欢《城南旧事》的叙述语气。"
        result = self.interview_with_key_prompts("prompt", ["  offline-test  "], f"{answer}\nquit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("请说说三部你喜欢的作品", result.stdout)
        self.assertIn("1/12", result.stdout, result.stderr)
        self.assertNotIn("Show reasoning", result.stdout)
        self.assertNotIn("offline-test", result.stdout + result.stderr)
        _, log, profile, markdown, shareable = self.files("prompt")
        self.assertEqual(profile["_metadata"]["interview_turns"], 1)
        self.assertNotIn("offline-test", str(log) + str(profile) + markdown + shareable)
        self.assertEqual([call["model"] for call in self.server.calls], ["questions-test", "profile-test"])
        self.assertTrue(all(call["authenticated"] for call in self.server.calls))
        self.assertEqual(self.server.calls[0]["thinking"], {"type": "disabled"})
        self.assertEqual(self.server.calls[0]["messages"][-1]["content"], answer)
        self.assertEqual(self.server.calls[1]["response_format"], {"type": "json_object"})

    def test_deepseek_prompt_cancels_on_an_empty_key(self):
        result = self.interview_with_key_prompts("blank", [" "])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("本次访谈已取消", result.stdout)
        self.assertEqual(self.server.calls, [])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_deepseek_401_retries_the_same_answer_with_a_replacement_key(self):
        self.server.required_keys["questions-test"] = "offline-test"
        answer = "我喜欢《龙族》的角色。"
        result = self.interview_with_key_prompts("recover", ["rejected-key", "offline-test"], f"{answer}\nquit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DeepSeek 拒绝了当前 Key（401）", result.stderr)
        rejected, retried, profile_call = self.server.calls
        self.assertEqual([call["authenticated"] for call in self.server.calls], [False, True, True])
        self.assertEqual(rejected["messages"], retried["messages"])
        self.assertEqual(profile_call["response_format"], {"type": "json_object"})
        _, log, profile, _, _ = self.files("recover")
        self.assertEqual([msg["content"] for msg in log["conversation"] if msg["role"] == "user"], [answer])
        self.assertEqual(profile["_metadata"]["interview_turns"], 1)
        for key in ("rejected-key", "offline-test"):
            self.assertNotIn(key, result.stdout + result.stderr)

    def test_deepseek_401_can_be_cancelled_without_retry_or_profile(self):
        self.server.required_keys["questions-test"] = "offline-test"
        result = self.interview_with_key_prompts("cancel", ["rejected-key", ""], "An answer\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("本次访谈已取消", result.stdout)
        self.assertEqual([call["authenticated"] for call in self.server.calls], [False])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_deepseek_401_during_final_profile_keeps_all_twelve_answers(self):
        self.server.required_keys["profile-test"] = "replacement-key"
        answers = [f"Answer {index}" for index in range(1, 13)]
        result = self.interview_with_key_prompts("late", ["offline-test", "replacement-key"], "\n".join(answers))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["authenticated"] for call in self.server.calls], [True] * 11 + [False, True])
        self.assertEqual(self.server.calls[-2]["messages"], self.server.calls[-1]["messages"])
        _, log, profile, _, _ = self.files("late")
        self.assertEqual([msg["content"] for msg in log["conversation"] if msg["role"] == "user"], answers)
        self.assertEqual(profile["_metadata"]["interview_turns"], 12)

    def test_deepseek_prompt_rejects_redirected_input(self):
        result = self.interview("redirected", "offline-test\n", "--prompt-deepseek-key")
        self.assertEqual(result.returncode, 1)
        self.assertIn("交互式终端", result.stderr)
        self.assertNotIn("offline-test", result.stdout + result.stderr)
        self.assertEqual(self.server.calls, [])

    def test_early_exit_long_input_and_fenced_profile(self):
        self.server.fenced = True
        data = "\n" + "字" * 2001 + "\nn\nq\n"
        result = self.interview("early", data, "--no-show-reasoning")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Enter an answer", result.stdout)
        self.assertIn("first 2,000 characters", result.stdout)
        self.assertNotIn("Model reasoning:", result.stdout)
        _, log, profile, _, _ = self.files("early")
        self.assertEqual(len(log["conversation"][0]["content"]), 2000)
        self.assertEqual(profile["_metadata"]["completion_status"], "early_exit")

    def test_eof_keeps_answers_already_sent(self):
        result = self.interview("eof", "I prefer a quick chapter.\n", "--no-show-reasoning")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Interview interrupted", result.stdout)
        _, _, profile, _, _ = self.files("eof")
        self.assertEqual(profile["_metadata"]["completion_status"], "interrupted")
        self.assertEqual(profile["_metadata"]["interview_turns"], 1)

    def test_model_call_fails_without_committing_a_follow_up(self):
        self.server.question_status = 503
        result = self.interview("provider", "The previous answer.\nquit\n", "--no-show-reasoning")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HTTP 503", result.stderr)
        _, log, profile, _, _ = self.files("provider")
        self.assertEqual(log["conversation"], [])
        self.assertEqual(profile["_metadata"]["interview_turns"], 0)

    def test_failed_final_model_call_is_saved_without_an_automatic_retry(self):
        self.enable_redis_fixture()
        self.server.profile_status = 503
        data = "\n".join(f"Answer {i}" for i in range(12)) + "\nn\n"
        blocked = self.interview("retry", data, "--no-show-reasoning")
        self.assertEqual(blocked.returncode, 1)
        self.assertEqual(sum(call["model"] == "profile-test" for call in self.server.calls), 1)
        self.server.profile_status = 200
        recovered = self.run_tool(
            "cli_interview.py", "--resume", "retry", "--output-dir", self.out, "--no-show-reasoning"
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        _, log, profile, _, _ = self.files("retry")
        self.assertEqual(profile["_metadata"]["interview_turns"], 12)
        self.assertEqual(log["conversation"][-2]["content"], "Answer 11")

    def test_cross_process_redis_resume_and_all_inspection_commands(self):
        self.enable_redis_fixture()
        seed = self.run_tool("-c", """
from wren import InterviewAgent
agent = InterviewAgent()
agent.start_interview("resume")
agent.send_message("I read after work.", "resume")
""")
        self.assertEqual(seed.returncode, 0, seed.stderr)
        listed = self.run_tool("scripts/view_redis_sessions.py")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("resume   1/12   active", listed.stdout)
        state = self.run_tool("scripts/view_session_conversation.py", "resume")
        self.assertEqual(state.returncode, 0, state.stderr)
        self.assertIn("I read after work.", state.stdout)
        recovered = self.run_tool(
            "cli_interview.py", "--resume", "resume", "--output-dir", self.out, "--no-show-reasoning",
            data="The prose matters most.\nquit\n",
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        log_path, _, profile, _, _ = self.files("resume")
        self.assertEqual(profile["_metadata"]["interview_turns"], 2)
        reasoning = self.out / "reasoning.json"
        view = self.run_tool("scripts/view_conversation_log.py", log_path, "--reasoning-file", reasoning)
        self.assertEqual(view.returncode, 0, view.stderr)
        self.assertEqual(len(json.loads(reasoning.read_text(encoding="utf-8"))), 2)
        call_count = len(self.server.calls)
        retrieved = self.run_tool("scripts/retrieve_profile.py", "resume")
        self.assertEqual(retrieved.returncode, 0, retrieved.stderr)
        self.assertEqual(json.loads(retrieved.stdout), profile)
        self.assertEqual(len(self.server.calls), call_count)
        generated = self.run_tool("scripts/retrieve_profile.py", "--log", log_path)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertEqual(json.loads(generated.stdout), profile)
        self.assertEqual(len(self.server.calls), call_count + 1)

    def test_empty_api_key_stops_before_the_model_request(self):
        self.env["MOONSHOT_API_KEY"] = ""
        result = self.interview("unconfigured", "", "--no-show-reasoning")
        self.assertEqual(result.returncode, 1)
        self.assertIn("MOONSHOT_API_KEY", result.stderr)
        self.assertEqual(self.server.calls, [])

    def test_public_demo_reproduces_the_sample_without_a_key(self):
        self.env.update(READWREN_PROVIDER="deepseek", DEEPSEEK_API_KEY="", MOONSHOT_API_KEY="")
        demo = self.run_tool("examples/run_demo.py", "--output-dir", self.out)
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertIn("12/12", demo.stdout)
        self.assertEqual(self.server.calls, [])
        sessions = [path for path in self.out.iterdir() if path.is_dir()]
        self.assertEqual(len(sessions), 1)
        _, log, profile, markdown, _ = self.files(sessions[0].name)
        self.assertEqual(sum(entry["role"] == "user" for entry in log["conversation"]), 12)
        self.assertEqual(profile["_metadata"]["completion_status"], "complete")
        checked_in = (ROOT / "examples" / "sample-profile.md").read_text(encoding="utf-8")
        self.assertEqual(checked_in.split("\n\n", 1)[1], markdown)

    def test_codex_chat_export_saves_profile_and_visible_transcript_without_keys(self):
        self.env.update(MOONSHOT_API_KEY="", DEEPSEEK_API_KEY="")
        draft = self.out / "draft.json"
        conversation = [
            {"role": "user", "content": "我喜欢人物比情节更重要的小说。"},
            {"role": "assistant", "content": "你喜欢怎样的结局？"},
            {"role": "user", "content": "带一点遗憾，但留有希望。"},
        ]
        draft.write_text(json.dumps({
            "conversation": conversation,
            "profile": {**PROFILE, "_metadata": {"interview_turns": 9}, "_reasoning": "omit"},
        }, ensure_ascii=False), encoding="utf-8")
        exported = self.run_tool("scripts/export_codex_profile.py", draft, "--output-dir", self.out)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        self.assertEqual(self.server.calls, [])
        paths = {key: Path(value) for key, value in json.loads(exported.stdout).items()}
        for path in paths.values():
            self.assertTrue(path.exists())
        profile = json.loads(paths["profile_json"].read_text(encoding="utf-8"))
        log = json.loads(paths["log"].read_text(encoding="utf-8"))
        self.assertEqual(log["conversation"], conversation)
        self.assertNotIn("_reasoning", profile)
        self.assertEqual(profile["_metadata"], {
            "interview_turns": 2, "completion_status": "early_exit", "early_termination": True,
        })

    def test_codex_chat_export_rejects_a_profile_without_reader_answers(self):
        draft = self.out / "empty.json"
        draft.write_text(json.dumps({"profile": PROFILE, "conversation": []}), encoding="utf-8")
        exported = self.run_tool("scripts/export_codex_profile.py", draft, "--output-dir", self.out)
        self.assertEqual(exported.returncode, 1, exported.stderr)
        self.assertEqual(list(self.out.iterdir()), [draft])

    def test_deepseek_full_interview_and_json_profile_contract(self):
        self.env.update(
            READWREN_PROVIDER="deepseek",
            MOONSHOT_API_KEY="other-provider-key",
            DEEPSEEK_API_KEY="offline-test",
            DEEPSEEK_BASE_URL=f"http://127.0.0.1:{self.server.server_port}",
            DEEPSEEK_INTERVIEW_MODEL="deepseek-flash",
            DEEPSEEK_PROFILE_MODEL="deepseek-flash",
            DEEPSEEK_INTERVIEW_MAX_TOKENS="800",
            DEEPSEEK_PROFILE_MAX_TOKENS="4000",
            DEEPSEEK_THINKING="disabled",
        )
        result = self.interview("deepseek", "\n".join(f"Answer {i}" for i in range(12)) + "\n",
                                "--no-show-reasoning")
        self.assertEqual(result.returncode, 0, result.stderr)
        _, _, profile, _, _ = self.files("deepseek")
        self.assertEqual(profile["_metadata"]["interview_turns"], 12)
        calls = self.server.calls
        self.assertEqual(len(calls), 12)
        self.assertTrue(all(call["model"] == "deepseek-flash" and call["authenticated"]
                            and call["path"] == "/chat/completions" for call in calls))
        self.assertTrue(all(call["thinking"] == {"type": "disabled"} for call in calls))
        self.assertTrue(all("response_format" not in call for call in calls[:-1]))
        self.assertEqual(calls[-1]["response_format"], {"type": "json_object"})
        self.assertIn("JSON", calls[-1]["messages"][0]["content"])
        self.assertTrue(all("reasoning_content" not in message for call in calls
                            for message in call["messages"]))

    def test_deepseek_missing_key_does_not_fall_back_to_kimi(self):
        self.env.update(READWREN_PROVIDER="deepseek", DEEPSEEK_API_KEY="")
        result = self.interview("unconfigured-deepseek", "", "--no-show-reasoning")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("DEEPSEEK_API_KEY", result.stderr)
        self.assertEqual(self.server.calls, [])

    def test_deepseek_empty_json_profile_stops_without_automatic_retry(self):
        self.env.update(
            READWREN_PROVIDER="deepseek",
            DEEPSEEK_API_KEY="offline-test",
            DEEPSEEK_BASE_URL=f"http://127.0.0.1:{self.server.server_port}",
            DEEPSEEK_THINKING="enabled",
        )
        self.server.profile_override = "   "
        result = self.interview("deepseek-empty", "quit\nn\n", "--no-show-reasoning")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("returned no text", result.stderr)
        self.assertIn("Retry profile generation", result.stdout)
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(self.server.calls[0]["thinking"], {"type": "enabled"})
        self.assertFalse((self.out / "deepseek-empty").exists())

    def test_migrate_original_interview_and_resume_with_pending_answer(self):
        client = self.enable_redis_fixture()
        key = "langgraph:checkpoint:old-interview:latest"
        raw = old_checkpoint("old-interview", ["Same answer", "Same answer"], pending="Waiting for a question.")
        client.set(key, raw, ex=500)
        result = self.run_tool("scripts/import_legacy_session.py", "old-interview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3/12", result.stdout)
        self.assertEqual(self.server.calls, [])
        self.assertEqual(client.get(key), raw)
        self.assertLessEqual(client.ttl("readwren:session:old-interview"), 500)
        self.assertEqual(self.run_tool("scripts/import_legacy_session.py", "old-interview").returncode, 1)
        resumed = self.run_tool(
            "cli_interview.py", "--resume", "old-interview", "--output-dir", self.out,
            "--no-show-reasoning", data="quit\n",
        )
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        _, log, generated, _, _ = self.files("old-interview")
        user_messages = [entry["content"] for entry in log["conversation"] if entry["role"] == "user"]
        self.assertEqual(user_messages, ["Same answer", "Same answer", "Waiting for a question."])
        self.assertEqual(generated["_metadata"]["interview_turns"], 3)
        self.assertEqual([call["max_tokens"] for call in self.server.calls], [800, 4000])

    def test_migrate_completed_original_profile_without_requesting_a_model(self):
        client = self.enable_redis_fixture()
        session_id = "archived"
        old_profile = self.out / "profile_original.json"
        payload = dict(PROFILE, _metadata={"interview_turns": 12, "completion_status": "complete"})
        old_profile.write_text(json.dumps(payload), encoding="utf-8")
        raw = old_checkpoint(session_id, [f"Answer {i}" for i in range(12)])
        client.set("langgraph:checkpoint:archived:latest", raw, ex=90)
        imported = self.run_tool("scripts/import_legacy_session.py", session_id, "--profile", old_profile)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertIn("12/12", imported.stdout)
        self.assertIn("complete", imported.stdout)
        retrieved = self.run_tool("scripts/retrieve_profile.py", session_id)
        self.assertEqual(retrieved.returncode, 0, retrieved.stderr)
        self.assertEqual(json.loads(retrieved.stdout), payload)
        self.assertEqual(self.server.calls, [])
        self.assertEqual(client.get("langgraph:checkpoint:archived:latest"), raw)


class LocalRuleTests(unittest.TestCase):
    def test_provider_defaults_and_legacy_moonshot_configuration(self):
        with patch.dict(os.environ, {}, clear=True), patch("wren.model.load_dotenv"):
            defaults = Settings.load()
            self.assertEqual(defaults.provider, "deepseek")
            self.assertEqual(defaults.base_url, "https://api.deepseek.com")
            self.assertEqual((defaults.interview_model, defaults.profile_model), ("deepseek-flash", "deepseek-flash"))
            os.environ["MOONSHOT_API_KEY"] = "fixture"
            legacy = Settings.load()
            self.assertEqual(legacy.provider, "moonshot")
            self.assertEqual(legacy.interview_model, "kimi-k2-thinking-turbo")
            os.environ["READWREN_PROVIDER"] = "deepseek"
            explicit = Settings.load()
            self.assertEqual(explicit.api_key, "")
            self.assertEqual(Settings(provider="deepseek", api_key="fixture").base_url,
                             "https://api.deepseek.com")

    def test_role_normalization_and_response_signals(self):
        responses = (Message("human", "I love a story because of prose."),) * 8
        analysis = analyze_conversation(responses)
        self.assertEqual(analysis["turn_count"], 8)
        self.assertTrue(analysis["ready_for_summary"])
        self.assertEqual(analysis["response_analysis"]["engagement_level"], 1.0)

    def test_profile_parser_rejects_invalid_types_and_prose(self):
        for value in ("[]", "I wrote a long report.", "```json\n{\n```"):
            with self.subTest(value=value), self.assertRaises(ProfileError):
                parse_profile(value)

    def test_redis_roundtrip_expiry_and_optimistic_updates(self):
        client = FakeRedis()
        first = RedisSessions(client)
        version = first.put(Session("thread", "Opening"))
        second = RedisSessions(client)
        self.assertEqual(second.get("thread"), version)
        self.assertGreaterEqual(second.list()[0][1], TTL_SECONDS - 1)
        second.put(replace(version, messages=(Message("user", "newest"),)))
        with self.assertRaises(SessionConflict):
            first.put(version)
        client.expire("readwren:session:thread", 0)
        self.assertIsNone(first.get("thread"))
        self.assertEqual(first.list(), [])

    def test_programmatic_profile_exports(self):
        with TemporaryDirectory() as temp:
            saver = ProfileSaver(base_dir=temp)
            profile_path = saver.save_profile("api", PROFILE)
            self.assertEqual(json.loads(Path(profile_path).read_text(encoding="utf-8")), PROFILE)
            log_path = saver.save_conversation_log(
                "api", [Message("assistant", "Question", "Provider reasoning").as_log()],
                include_reasoning=False,
            )
            log = json.loads(Path(log_path).read_text(encoding="utf-8"))
            self.assertNotIn("reasoning_content", log["conversation"][0])
            files = saver.save_session_summary("another-api", log["conversation"], PROFILE)
            self.assertIn("Literary profile", Path(files["profile_markdown"]).read_text(encoding="utf-8"))

    def test_old_profile_fields_and_original_implicit_units_in_exports(self):
        with TemporaryDirectory() as temp:
            profile = dict(
                PROFILE,
                reading_philosophy="A distinctive view on reading.",
                anti_patterns=["Predictable reversals", "Detached narration"],
                _metadata={"interview_turns": 3, "early_termination": True},
                _reasoning="Internal field kept only in the raw profile",
            )
            files = ProfileSaver(temp).save_session_summary("old-profile", [], profile)
            for name in ("profile_markdown", "profile_shareable"):
                text = Path(files[name]).read_text(encoding="utf-8")
                self.assertIn(profile["reading_philosophy"], text)
                self.assertIn("Detached narration", text)
                self.assertIn("Treat inferred preferences as tentative.", text)
                self.assertNotIn(profile["_reasoning"], text)
            self.assertIn("72%", Path(files["profile_markdown"]).read_text(encoding="utf-8"))
            self.assertIn("0.72/1.0", Path(files["profile_shareable"]).read_text(encoding="utf-8"))
            saved = json.loads(Path(files["profile_json"]).read_text(encoding="utf-8"))
            self.assertEqual(saved, profile)

    def test_unsupported_legacy_object_does_not_create_a_new_session(self):
        client = FakeRedis()
        client.set("langgraph:checkpoint:unknown:latest", pickle.dumps(Path("other-data")), ex=40)
        store = RedisSessions(client)
        with self.assertRaisesRegex(ValueError, "Could not import"):
            import_legacy_session(store, "unknown")
        self.assertIsNone(store.get("unknown"))


if __name__ == "__main__":
    unittest.main()
