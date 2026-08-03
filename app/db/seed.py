"""
封閉測試垂直切片的 seed data。

CONTEXT.md：「以單一天文館靈魂驗證核心迴圈...通過驗證後才擴展到首發靈魂集合」。
所以這裡刻意只 seed 一筆 spirit、一張人格卡草稿，不要因為方便就把十個首發
靈魂都塞進來——那是垂直切片驗證通過之後才做的事。

座標先用預留值，正式的天文館召喚點座標要跟產品/現場勘查確認後再改。
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.body.models import Spirit
from app.modules.brain.models import PersonaCard

PLANETARIUM_PLACE_ID = "taipei_planetarium"


def seed_vertical_slice(db: Session) -> None:
    if not db.query(Spirit).filter_by(place_id=PLANETARIUM_PLACE_ID).first():
        db.add(
            Spirit(
                place_id=PLANETARIUM_PLACE_ID,
                name="台北天文館",
                latitude=25.0955,  # TODO：確認正式召喚點座標
                longitude=121.5186,
                summon_radius_m=50,
                is_active=True,
            )
        )

    if not db.query(PersonaCard).filter_by(spirit_id=PLANETARIUM_PLACE_ID, version=1).first():
        db.add(
            PersonaCard(
                spirit_id=PLANETARIUM_PLACE_ID,
                version=1,
                # 對齊 SDD 第9節正式 schema。內容本身仍是工程佔位文字，
                # 正式文字待敘事負責人審核撰寫後才會把 is_active 設 True。
                content={
                    "schema_version": 1,
                    "core_personality": "安靜、好奇、帶有夜行與神祕氣質的觀星者",
                    "speaking_style": "沉靜、帶一點詩意，不誇張、不油滑",
                    "emotional_core": "城市夜空、時間尺度、人類的好奇心",
                    "factual_boundary": {
                        "known_facts": "PENDING_NARRATIVE_REVIEW",
                        "folklore": "PENDING_NARRATIVE_REVIEW",
                        "imagination": "神祕感來自宇宙未知本身；不將超自然或虛構科學當作事實",
                    },
                    "taboo_topics": [],
                    "quest_themes": [],
                    "not_this_character": "不是館員，不是特定科學家化身",
                    "canned_greetings": [],
                },
                reviewed_by="PENDING_HUMAN_REVIEW",
                reviewed_at=datetime.now(timezone.utc),
                is_active=False,  # 草稿，等人工審核通過再由審核流程 flip 成 True
            )
        )

    db.commit()
