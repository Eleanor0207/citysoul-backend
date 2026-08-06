"""
Spike 專用 seed：讓「走到地標→召喚→對話→看到回應」這條路在本機真的跑得起來。

## 為什麼不直接改 app/db/seed.py

主 seed 的人格卡是 `is_active=False` 的**待審核草稿**，而 `is_active` 依 CONTEXT.md
只能由人工審核流程 flip——人格卡的定義就是「經人工審核的角色定義」。為了 demo
方便去翻那個旗標，等於把內容治理的規則悄悄挖掉。

所以這裡另建一張 **version 2** 的卡，內容明確標記為未審核佔位文字，並且只有
跑這支腳本才會出現。主 seed 完全不動。

## 用法

    uv run python -m scripts.seed_spike

## ⚠️ 不要用在正式環境

這張卡的 canned_greetings 是工程佔位文字，不是敘事負責人寫的。正式內容進來時，
應該新增 version 3 並由審核流程 flip，而不是改這裡。
"""
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.db.seed import PLANETARIUM_PLACE_ID
from app.modules.brain.models import PersonaCard

SPIKE_VERSION = 2

# 佔位台詞。刻意寫得像那麼一回事，方便在手機上看出「這是角色在講話」而不是
# 測試字串，但**沒有經過審核**。
SPIKE_CANNED_GREETINGS = [
    {
        "trigger_phrases": ["你好", "哈囉", "嗨", "hello", "hi"],
        "response_text": "你來了。這個時間的天空剛好最安靜——抬頭看看，今晚雲不多。",
    },
    {
        "trigger_phrases": ["你是誰", "你是什麼", "自我介紹"],
        "response_text": "我是這座館舍累積了幾十年的仰望所凝成的意識。不是館員，也不是誰的化身——只是這裡的一部分。",
    },
    {
        "trigger_phrases": ["今天天氣如何", "今天天氣", "天氣"],
        "response_text": "地面的天氣我感覺得到，但我更在意上面那層。今晚適合待久一點。",
    },
    {
        "trigger_phrases": ["再見", "掰掰", "bye", "我要走了"],
        "response_text": "路上小心。星星會在原地等你，它們很有耐心。",
    },
]


def seed_spike_persona_card() -> None:
    db = SessionLocal()
    try:
        existing = (
            db.query(PersonaCard)
            .filter_by(spirit_id=PLANETARIUM_PLACE_ID, version=SPIKE_VERSION)
            .first()
        )
        if existing:
            existing.content = {**existing.content, "canned_greetings": SPIKE_CANNED_GREETINGS}
            existing.is_active = True
            action = "更新"
        else:
            db.add(
                PersonaCard(
                    spirit_id=PLANETARIUM_PLACE_ID,
                    version=SPIKE_VERSION,
                    content={
                        "schema_version": 1,
                        "core_personality": "安靜、好奇、帶有夜行與神祕氣質的觀星者",
                        "speaking_style": "沉靜、帶一點詩意，不誇張、不油滑",
                        "emotional_core": "城市夜空、時間尺度、人類的好奇心",
                        "factual_boundary": {
                            "known_facts": "SPIKE_PLACEHOLDER_NOT_REVIEWED",
                            "folklore": "SPIKE_PLACEHOLDER_NOT_REVIEWED",
                            "imagination": "神祕感來自宇宙未知本身；不將超自然或虛構科學當作事實",
                        },
                        "taboo_topics": [],
                        "quest_themes": [],
                        "not_this_character": "不是館員，不是特定科學家化身",
                        "canned_greetings": SPIKE_CANNED_GREETINGS,
                    },
                    reviewed_by="SPIKE_PLACEHOLDER_NOT_REVIEWED",
                    reviewed_at=datetime.now(timezone.utc),
                    is_active=True,
                )
            )
            action = "建立"
        db.commit()
        print(f"{action} spike 人格卡（version {SPIKE_VERSION}, is_active=True）")
        print(f"  觸發語共 {sum(len(g['trigger_phrases']) for g in SPIKE_CANNED_GREETINGS)} 個")
        print("  主 seed 的 version 1 草稿未受影響")
    finally:
        db.close()


if __name__ == "__main__":
    seed_spike_persona_card()
