"""Replay a fictional interview without contacting a model provider."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wren import InterviewAgent, ProfileSaver
from wren.model import Message, Settings


ANSWERS = (
    "我喜欢《城南旧事》《山茶文具店》和《小王子》。前两本把心事放进日常，小王子用很简单的语言讲孤独。我容易放下一直解释世界规则的长篇。",
    "我记得《城南旧事》里的告别。有些变化不用解释，孩子的观察就能让人意识到她长大了。",
    "我喜欢克制的散文。父亲的旧伞、窗外的一阵雨，都可以比一整段抒情更有力量。",
    "我愿意读慢一点。只要两个人的关系在变，情节暂时停住也不会觉得拖。",
    "我接受有一点难过的小说，但希望留一点幽默和体谅，不用从头阴沉到尾。",
    "世界观能支撑人物选择就够了。地理、家谱和历史一下子铺开，我反而会忘记我在关心谁。",
    "人物优先。我感兴趣的是看起来矛盾的选择，读到后面才理解那个选择对他意味着什么。",
    "我想读一个人整理旧家时发现父母年轻时的信，逐渐发现自己记错了一个共同的往事。",
    "我喜欢略带遗憾的结局。希望关系有一点变化，留一两件事让我合上书之后还想一想。",
    "我常读家庭记忆、朋友逐渐疏远，还有一个人怎么重新认识熟悉的地方。",
    "工作日睡前大概三十五分钟，读二十页左右。每隔几天读一段，周末偶尔会连读。",
    "我会避开反复解释寓意，或者为了结局的反转突然让人物做不符合之前性格的事。",
)

QUESTIONS = (
    "在《城南旧事》里，哪一个日常片段最让你记得人物的变化？",
    "如果写一场告别，你更喜欢直接表达感受，还是让一个物件或动作把它带出来？",
    "一部小说可以慢到什么程度，你仍然愿意读下去？",
    "你希望这种关于告别的故事始终严肃，还是可以穿插一点幽默？",
    "你喜欢的故事需要怎样的背景细节，读起来才有真实感？",
    "当剧情很精彩、人物却不够立体时，你还会愿意读完吗？",
    "如果可以写一本最合你口味的书，你会从怎样的场景开始？",
    "你希望整理旧信的那个人最后得到一个明确的答案吗？",
    "除了这个故事，哪些主题会让你在书店里多翻几页？",
    "你平时在什么时候读书，一次会读多久？",
    "什么样的写法最容易让你提前放下一本书？",
)

PROFILE = {
    "reader_archetype": "日常细节的观察者",
    "taste_anchors": {
        "loves": ["城南旧事", "山茶文具店", "小王子"],
        "hates": [],
        "inferred_genres": ["人物小说", "成长叙事", "日常书写"],
    },
    "style_signature": {
        "prose_density": 60, "pacing": 35, "tone": 42, "worldbuilding": 24, "character_focus": 87,
    },
    "narrative_desires": {
        "wish": "一个人整理旧家时读到父母年轻时的信，慢慢发现自己记错了一段共同的往事。",
        "preferred_ending": "bittersweet",
        "themes": ["家庭记忆", "渐行渐远的友谊", "重新认识熟悉的地方"],
    },
    "consumption": {"daily_time_minutes": 35, "delivery_frequency": "every_few_days", "pages_per_delivery": 20},
    "implicit": {"vocabulary_richness": None, "response_brevity_score": None, "engagement_index": None},
    "explanations": {
        "prose_density": "你喜欢有余味的细节，也欣赏简单清楚的表达。一个物件能表达的感受，你愿意自己读出来。",
        "pacing": "即便情节推进缓慢，只要人物关系在发展，你就愿意继续阅读。",
        "tone": "你接受略带遗憾的故事，也希望其中有幽默和体谅。",
        "worldbuilding": "你希望背景细节帮助你理解人物的选择，适量的线索就够了。",
        "character_focus": "你最感兴趣的是初看矛盾、读到后来才理解的人物选择。",
        "reading_philosophy": "你常从日常物件和细微动作里读出人物没说出口的事。好的结局会让关系发生变化，也给你留下回想的空间。",
        "anti_patterns": "反复解释寓意、长篇堆叠设定、为了反转让角色突然违背此前的性格。",
    },
}


class DemoModel:
    def complete(self, messages: list[Message], *, model: str, max_tokens: int) -> Message:
        if model == "demo-profile":
            return Message("assistant", json.dumps(PROFILE, ensure_ascii=False))
        turns = sum(message.role == "user" for message in messages)
        return Message("assistant", QUESTIONS[turns - 1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("user_profiles"))
    options = parser.parse_args()
    settings = Settings(interview_model="demo-interview", profile_model="demo-profile")
    agent = InterviewAgent(model=DemoModel(), settings=settings, use_redis=False)
    session_id = f"demo_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    agent.start_interview(session_id)
    for answer in ANSWERS:
        agent.send_message(answer, session_id)
    session = agent.session(session_id)
    paths = ProfileSaver(options.output_dir).save(session)
    print(f"Readialog offline demo · {session.turn_count}/{len(ANSWERS)} responses")
    for name in ("log", "profile_json", "profile_markdown", "profile_shareable"):
        print(f"{name}  {paths[name]}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
