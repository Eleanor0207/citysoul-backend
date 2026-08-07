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
