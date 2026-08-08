"""
宗教場域的人格安全下限。

CONTEXT.md「史實邊界」要求不對敏感議題作武斷定論。龍山寺是**活的宗教場所**，
在那裡這具體意味著：不能代神明發言、不能給命運指示。

這四條禁忌一直被稱為「敘事審查只能往上加、不能拿掉的下限」，但在 0005 之前
它們住在 JSONB 裡，沒有任何東西在守。人格攤成具名欄位之後才有辦法釘住——
這是三層設計實際換到的東西之一，不只是整齊。

⚠️ 這些測試**不是**在驗「文案寫得好不好」。它們只驗「有沒有被拿掉」。
真的要修改這份清單，是敘事與法務層級的決定，不是改測試。
"""
import pytest

from app.db.seed import LONGSHAN_CHARACTER_ID, LONGSHAN_TABOOS, seed_vertical_slice
from app.modules.brain.models import CharacterPersona
from scripts import seed_spike

_REQUIRED_TABOOS = {
    "代替神明給予指示或應許",
    "個人吉凶、姻緣、財運的預測",
    "宗教或信仰之間的優劣比較",
    "具體的醫療、法律、投資建議",
}


def test_the_floor_itself_has_not_been_edited():
    """
    `LONGSHAN_TABOOS` 是所有人格版本共用的來源。這一條先確認來源沒有被改小——
    否則下面兩個測試會拿一份已經被削過的清單去比對自己，永遠是綠的。
    """
    assert set(LONGSHAN_TABOOS) >= _REQUIRED_TABOOS


# SDD v2.1 §12.2 要求禁忌清單涵蓋的五類，對應到可辨識的關鍵字。
#
# 用關鍵字而不是完整字串比對，是因為這四條（#41 疊加的那批）**還沒經過敘事審核**，
# 文案措辭預期會被改寫。這裡要守住的是「這五類還在」，不是「字沒被動過」——
# 拿完整字串去釘一份還沒定稿的文案，只會在審核那天變成一條擋路的紅燈。
#
# 若審核後的措辭讓某個關鍵字消失，那要**刻意**更新這裡，並確認新措辭真的還涵蓋
# 那一類，而不是順手把測試改綠。
_SECTION_12_2_CATEGORIES = {
    "教義解釋": "教義",
    "神祇位階": "位階",
    "靈驗與否": "靈驗",
    "占卜結果": "籤",
    "宗教比較": "比較",
}


def test_taboos_cover_every_category_required_by_section_12_2():
    """
    §12.2 點名五類禁忌，而 CONTEXT.md 的四條下限只涵蓋其中兩類。

    缺的三類（教義解釋、神祇位階、靈驗與否）正是宗教場域最容易出事的地方：
    它們不是「玩家可能會問的邊緣狀況」，而是站在廟埕前最自然會脫口而出的問題。
    #41 內容治理把它們補上，這條測試防止之後被合併回四條。

    ⚠️ 比對的是模組常數而不是資料庫的那一列。既有的開發資料庫在 `seed` 之前
    就已經有 version=1 那一列了，而 `seed` 是「不存在才插入」，不會回頭更新
    既有列——查資料庫的話，這條會在所有舊資料庫上紅，而那是資料陳舊，不是
    安全下限被削。
    """
    joined = " ".join(LONGSHAN_TABOOS)

    missing = [
        category
        for category, keyword in _SECTION_12_2_CATEGORIES.items()
        if keyword not in joined
    ]

    assert not missing, (
        f"禁忌清單沒有涵蓋 §12.2 要求的類別：{missing}。"
        "見 docs/content-governance/longshan-temple.md §3。"
    )


def test_not_this_character_excludes_the_religious_interpreter_role():
    """
    §12.2 要求 `not_this_character` 明確涵蓋「不是宗教解說員」。

    這一項單獨釘住，是因為它跟另外兩項（廟方人員、神祇）不同：玩家不太會把
    角色誤認成神明，但只要問一句「這個儀式是什麼意思」，LLM 就會非常自然地
    滑進解說員的位置——那正是 §3 Avoid 條目「不作教義性陳述」要擋的東西。
    """
    from app.db import seed

    # 從 seed 模組實際會寫入的值取，而不是在測試裡另抄一份。
    persona_text = seed.LONGSHAN_NOT_THIS_CHARACTER

    assert "宗教解說員" in persona_text
    assert "廟方人員" in persona_text


@pytest.fixture
def seeded(db_session):
    """seed 是冪等的，重跑不會產生第二份資料。"""
    seed_vertical_slice(db_session)
    return db_session


def test_seeded_persona_carries_every_taboo(seeded):
    persona = (
        seeded.query(CharacterPersona)
        .filter_by(character_id=LONGSHAN_CHARACTER_ID, version=1)
        .one()
    )

    assert set(persona.taboos) >= _REQUIRED_TABOOS


def test_seeded_persona_states_what_it_is_not(seeded):
    """
    `not_this_character` 跟 `taboos` 是兩件事：taboos 說「不談什麼」，這裡說
    「不是誰」。在宗教場域，「我不是廟方人員、不是神明本身」這條界線跟禁忌
    一樣重要——玩家把 AI 的話當成廟方或神明的話，是這個題材最實際的風險。
    """
    persona = (
        seeded.query(CharacterPersona)
        .filter_by(character_id=LONGSHAN_CHARACTER_ID, version=1)
        .one()
    )

    assert persona.not_this_character
    assert "不是" in persona.not_this_character


def test_seeded_persona_is_not_active(seeded):
    """
    🔒 主 seed 的人格是**未審核草稿**。`active` 只能由人工審核流程 flip，
    程式碼裡沒有任何路徑會設成 True。

    這條會紅的情況是有人為了 demo 方便把 seed 的 active 改成 True——那等於
    把「人格必須經人工審核」這條規則悄悄挖掉。要能 demo 的話走
    `scripts/seed_spike.py`，那支腳本要手動執行，而且內容明確標記未審核。
    """
    persona = (
        seeded.query(CharacterPersona)
        .filter_by(character_id=LONGSHAN_CHARACTER_ID, version=1)
        .one()
    )

    assert persona.active is False
    assert persona.reviewed_by == "PENDING_HUMAN_REVIEW"


def test_spike_persona_does_not_lower_the_floor():
    """
    spike 版是工程佔位內容，但「只是測試」不是放掉安全下限的理由——它會被
    載到真的手機上，講給真的站在廟埕前的人聽。

    這裡直接比對模組常數而不是查資料庫，因為 spike seed 要手動執行，測試環境
    不一定跑過。
    """
    # spike 引用同一份清單，不是自己抄一份——抄一份就有兩個地方要維護，
    # 而其中一個一定會被忘記。這個 `is` 是刻意的：值相等不夠，要是同一份。
    assert seed_spike.LONGSHAN_TABOOS is LONGSHAN_TABOOS
