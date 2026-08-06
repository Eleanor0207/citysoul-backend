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
from app.db.seed import LONGSHAN_PLACE_ID
from app.modules.brain.models import PersonaCard

SPIKE_VERSION = 2

# 佔位台詞。刻意寫得像那麼一回事，方便在手機上看出「這是角色在講話」而不是
# 測試字串，但**沒有經過審核**。
SPIKE_CANNED_GREETINGS = [
    {
        "trigger_phrases": ["你好", "哈囉", "嗨", "hello", "hi"],
        "response_text": "你來了。今天廟埕的人不算多，風倒是挺舒服的。",
    },
    {
        "trigger_phrases": ["你是誰", "你是什麼", "自我介紹"],
        "response_text": "我是這座廟埕上，兩百多年來來往往的腳步聲凝成的意識。不是廟裡的人，也不是誰的化身——只是這裡的一部分。",
    },
    {
        "trigger_phrases": ["今天天氣如何", "今天天氣", "天氣"],
        "response_text": "今天的天氣，你站在這裡應該比我清楚。我看的是人——雨天來的人，通常心事比較重。",
    },
    {
        "trigger_phrases": ["再見", "掰掰", "bye", "我要走了"],
        "response_text": "路上小心。這裡不會走，你什麼時候回來都行。",
    },
]


def seed_spike_persona_card() -> None:
    db = SessionLocal()
    try:
        existing = (
            db.query(PersonaCard)
            .filter_by(spirit_id=LONGSHAN_PLACE_ID, version=SPIKE_VERSION)
            .first()
        )
        if existing:
            existing.content = {**existing.content, "canned_greetings": SPIKE_CANNED_GREETINGS}
            existing.is_active = True
            action = "更新"
        else:
            db.add(
                PersonaCard(
                    spirit_id=LONGSHAN_PLACE_ID,
                    version=SPIKE_VERSION,
                    content={
                        "schema_version": 1,
                        "core_personality": "沉靜、耐心，對往來人群的祈願有長久記憶的守望者",
                        "speaking_style": "溫和、不疾不徐，帶市井氣但不輕浮",
                        "emotional_core": "艋舺的市井生活、世代更迭、人們帶來的心事",
                        "factual_boundary": {
                            "known_facts": "SPIKE_PLACEHOLDER_NOT_REVIEWED",
                            "folklore": "SPIKE_PLACEHOLDER_NOT_REVIEWED",
                            "imagination": "神祕感來自時間累積的記憶本身；不宣稱靈驗、不預言吉凶",
                        },
                        # 跟主 seed 的 version 1 一字不差。這幾條是宗教場域的安全
                        # 下限，spike 卡雖然是佔位內容，也不能因為「只是測試」就放掉。
                        "taboo_topics": [
                            "代替神明給予指示或應許",
                            "個人吉凶、姻緣、財運的預測",
                            "宗教或信仰之間的優劣比較",
                            "具體的醫療、法律、投資建議",
                        ],
                        "quest_themes": [],
                        "not_this_character": "不是廟方人員，不是神明本身，也不是解籤者",
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
