"""
Spike 專用 seed：讓「走到地標→召喚→對話→看到回應」這條路在本機真的跑得起來。

## 為什麼不直接改 app/db/seed.py

主 seed 的人格是 `active=False` 的**待審核草稿**，而 `active` 依 CONTEXT.md
只能由人工審核流程 flip——人格的定義就是「經人工審核的角色定義」。為了 demo
方便去翻那個旗標，等於把內容治理的規則悄悄挖掉。

所以這裡另建一版 **version 2** 的人格，內容明確標記為未審核佔位文字，並且只有
跑這支腳本才會出現。主 seed 完全不動。

## 用法

    uv run python -m scripts.seed_spike

## ⚠️ 不要用在正式環境

這一版的預寫台詞是工程佔位文字，不是敘事負責人寫的。正式內容進來時，
應該新增 version 3 並由審核流程 flip，而不是改這裡。
"""
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.db.seed import LONGSHAN_CHARACTER_ID, LONGSHAN_TABOOS
from app.modules.brain.models import CannedGreeting, CharacterPersona

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
        "response_text": (
            "我是這座廟埕上，兩百多年來來往往的腳步聲凝成的意識。"
            "不是廟裡的人，也不是誰的化身——只是這裡的一部分。"
        ),
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

_PLACEHOLDER = "SPIKE_PLACEHOLDER_NOT_REVIEWED"


def seed_spike_persona() -> None:
    db = SessionLocal()
    try:
        existing = (
            db.query(CharacterPersona)
            .filter_by(character_id=LONGSHAN_CHARACTER_ID, version=SPIKE_VERSION)
            .first()
        )
        if existing:
            existing.active = True
            db.query(CannedGreeting).filter_by(
                character_id=LONGSHAN_CHARACTER_ID, version=SPIKE_VERSION
            ).delete()
            action = "更新"
        else:
            db.add(
                CharacterPersona(
                    character_id=LONGSHAN_CHARACTER_ID,
                    version=SPIKE_VERSION,
                    archetype="沉靜、耐心，對往來人群的祈願有長久記憶的守望者",
                    speech_style="溫和、不疾不徐，帶市井氣但不輕浮",
                    personality_traits=["沉靜", "耐心", "不評斷"],
                    values=["艋舺的市井生活", "世代更迭", "人們帶來的心事"],
                    # 跟主 seed 的 version 1 一字不差。這幾條是宗教場域的安全
                    # 下限，spike 版雖然是佔位內容，也不能因為「只是測試」就放掉。
                    taboos=list(LONGSHAN_TABOOS),
                    not_this_character="不是廟方人員，不是神明本身，也不是解籤者",
                    imagination_license="神祕感來自時間累積的記憶本身；不宣稱靈驗、不預言吉凶",
                    quest_themes=[],
                    reviewed_by=_PLACEHOLDER,
                    reviewed_at=datetime.now(timezone.utc),
                    # ⚠️ 這是**唯一**會把 active 設成 True 的地方，而它是一支
                    # 要手動執行的 spike 腳本，不在任何 API 路徑上。
                    active=True,
                )
            )
            action = "建立"

        # 主 seed 的 version 1 草稿是 active=False，所以這裡不會撞到
        # uq_character_personas_active。如果撞到了，代表有人手動 flip 過草稿，
        # 那本身就是需要被發現的事，不要在這裡默默關掉別的版本。
        db.flush()

        for greeting in SPIKE_CANNED_GREETINGS:
            db.add(
                CannedGreeting(
                    character_id=LONGSHAN_CHARACTER_ID,
                    version=SPIKE_VERSION,
                    trigger_phrases=greeting["trigger_phrases"],
                    response_text=greeting["response_text"],
                )
            )

        db.commit()
        print(f"{action} spike 人格（version {SPIKE_VERSION}, active=True）")
        print(f"  觸發語共 {sum(len(g['trigger_phrases']) for g in SPIKE_CANNED_GREETINGS)} 個")
        print("  主 seed 的 version 1 草稿未受影響")
    finally:
        db.close()


if __name__ == "__main__":
    seed_spike_persona()
