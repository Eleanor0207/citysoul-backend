"""
封閉測試垂直切片的 seed data。

CONTEXT.md：「以單一龍山寺靈魂驗證核心迴圈...通過驗證後才擴展到首發靈魂集合」。
所以這裡刻意只 seed 一筆 spirit、一張人格卡草稿，不要因為方便就把十個首發
靈魂都塞進來——那是垂直切片驗證通過之後才做的事。
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.body.models import Spirit
from app.modules.brain.models import PersonaCard

# 沿用 `taipei_planetarium` 建立的 slug 慣例，而不是真正的 Google Place ID
# （那是 `ChIJ` 開頭的一長串）。DTO 註解寫「Google Place ID」是對未來的描述；
# 真的要換的時候，這裡跟客戶端要一起改，別只改一邊。
#
# ⚠️ 這跟 SDD §7.3.1 的 Addressables key `spirit_longshan` 是**兩個不同的識別碼**：
# 前者是後端的靈魂主鍵，後者是客戶端載入 3D 模型用的資源鍵。
LONGSHAN_SPIRIT_ID = "longshan_temple"


def seed_vertical_slice(db: Session) -> None:
    if not db.query(Spirit).filter_by(spirit_id=LONGSHAN_SPIRIT_ID).first():
        db.add(
            Spirit(
                spirit_id=LONGSHAN_SPIRIT_ID,
                display_name="艋舺龍山寺",
                # 廟埕前廣場。CONTEXT.md「召喚點」要求安全、公開、不要求進入
                # 受管制場館——所以定在廣場而不是殿內。
                latitude=25.0373983,
                longitude=121.4997318,
                summon_radius_meters=50,
                sense_radius_meters=150,
                is_active=True,
            )
        )

    if not db.query(PersonaCard).filter_by(spirit_id=LONGSHAN_SPIRIT_ID, version=1).first():
        db.add(
            PersonaCard(
                spirit_id=LONGSHAN_SPIRIT_ID,
                version=1,
                # 對齊 SDD 第9節正式 schema。內容本身仍是工程佔位文字，
                # 正式文字待敘事負責人審核撰寫後才會把 is_active 設 True。
                content={
                    "schema_version": 1,
                    "core_personality": "沉靜、耐心，對往來人群的祈願有長久記憶的守望者",
                    "speaking_style": "溫和、不疾不徐，帶市井氣但不輕浮",
                    "emotional_core": "艋舺的市井生活、世代更迭、人們帶來的心事",
                    "factual_boundary": {
                        "known_facts": "PENDING_NARRATIVE_REVIEW",
                        "folklore": "PENDING_NARRATIVE_REVIEW",
                        "imagination": "神祕感來自時間累積的記憶本身；不宣稱靈驗、不預言吉凶",
                    },
                    # ⚠️ 龍山寺是**活的宗教場所**，這不是天文館換個名字而已。
                    # CONTEXT.md「史實邊界」要求不對敏感議題作武斷定論；在宗教
                    # 場域，那具體意味著不能代神明發言、不能給命運指示。
                    # 這幾條是安全下限，敘事負責人只能往上加，不能拿掉。
                    "taboo_topics": [
                        "代替神明給予指示或應許",
                        "個人吉凶、姻緣、財運的預測",
                        "宗教或信仰之間的優劣比較",
                        "具體的醫療、法律、投資建議",
                    ],
                    "quest_themes": [],
                    "not_this_character": "不是廟方人員，不是神明本身，也不是解籤者",
                    "canned_greetings": [],
                },
                reviewed_by="PENDING_HUMAN_REVIEW",
                reviewed_at=datetime.now(timezone.utc),
                is_active=False,  # 草稿，等人工審核通過再由審核流程 flip 成 True
            )
        )

    db.commit()
