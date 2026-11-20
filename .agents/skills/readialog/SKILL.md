---
name: readialog
description: Conduct a Readialog reading interview in a Codex chat and export a personal reading profile. Use when the user invokes $readialog or asks 阅谈 to interview them about their reading preferences.
---

# Readialog · 阅谈

Work in the cloned Readialog project. Find its root three directories above this skill directory and use the project's export script from there.

## Interview

- Use the reader's language. Ask about books or stories they enjoyed and one they did not, along with what made the difference.
- Ask one question per turn. Build each follow-up around something the reader actually said and explore their taste, preferred writing style, ideal stories, and reading habits. Count only accepted answers to interview questions.
- Generate a profile after 12 answers or when the reader explicitly asks to finish after giving at least one answer. If they stop without requesting a profile, let the interview end.

## Profile and files

1. Read the `PROFILE_REQUIREMENTS` section of `wren/interview.py` for the output fields and [the scoring rubric](../../../wren/profile_rubric.md) for the scales. Explain scores using the reader's answers. Leave unsupported judgments unknown.
2. Write a UTF-8 JSON draft under the ignored `user_profiles/` directory. Include a `profile` object and a `conversation` array of `{"role":"user","content":"..."}` and `{"role":"assistant","content":"..."}` objects. Start with the first accepted reader answer. Include visible follow-ups in chronological order and keep the reader's wording. A final reader answer can end the array.
3. Run `python scripts/export_codex_profile.py PATH_TO_DRAFT` using the project virtual environment if available. If dependencies are missing, follow the install commands in [the project README](../../../README.md). This script derives completion metadata from the answers and writes the transcript and three profile formats locally.
4. Summarize the reader's main preferences and link the saved profile. If export fails, retain the draft and report the error with the profile. The interview and profile writing happen in the Codex chat.
