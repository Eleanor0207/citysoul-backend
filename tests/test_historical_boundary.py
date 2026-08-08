"""
B5．史實邊界規則注入（issue #19）。

純函式測試——不需要資料庫，也不需要 GCP 憑證。

⚠️ **這裡不逐字比對整段文案。** AC 明訂「不要逐字比對整段文字，那會讓每次潤稿
都變成破壞性變更」。文案還是待審核草稿（見 `RULES_REVIEW_STATUS`），敘事負責人
一定會改字。這些測試守的是**語意有沒有還在**，不是句子有沒有被動過。
"""
import pytest

from app.modules.brain.historical_boundary import (
    RULES_REVIEW_STATUS,
    compose_factual_boundary,
    factual_boundary_for_persona,
    get_historical_boundary_rules,
)


# ── 純函式：穩定可測 ────────────────────────────────────────────────────

def test_rules_are_identical_across_calls():
    """
    AC：連續呼叫兩次回傳完全相同的字串。

    這層是安全邊界。安全邊界如果本身有不確定性（時間戳、隨機取樣、模型生成），
    那它就不是邊界，只是一個傾向。
    """
    assert get_historical_boundary_rules() == get_historical_boundary_rules()


def test_rules_are_not_empty():
    assert get_historical_boundary_rules().strip()


# ── 三條核心限制 ───────────────────────────────────────────────────────

# 語意 → 可辨識的關鍵字。潤稿可以改句子，但這些概念要留著。
#
# 若審核後的文案讓某個關鍵字消失，要**刻意**更新這裡並確認新文案真的還表達了
# 那個概念，而不是順手把測試改綠。
_CORE_LIMITS = {
    "不武斷定論": "定論",
    "不替爭議議題選邊": "爭議",
    "不編造未經證實的細節": "不確定",
}


@pytest.mark.parametrize("concept,keyword", sorted(_CORE_LIMITS.items()))
def test_rules_express_every_core_limit(concept, keyword):
    """AC：文字中可辨識出三條核心限制。"""
    assert keyword in get_historical_boundary_rules(), f"通則沒有表達「{concept}」"


def test_rules_distinguish_legend_from_fact():
    """
    CONTEXT.md「史實邊界」要求明確區分已知史實、民間傳說與角色想像。

    單獨釘住這一條，是因為它是三條裡最容易在潤稿時被稀釋成「保持謙虛」那種
    無法執行的句子的——模型看到「保持謙虛」不知道要做什麼。
    """
    rules = get_historical_boundary_rules()

    assert "傳說" in rules


# ── 宗教場所補充規則（#41 交付物 D）────────────────────────────────────

def test_religious_rules_included_by_default():
    """
    預設涵蓋宗教補充規則——刻意往安全的方向倒。

    兩種錯誤的代價不對稱：對天文館多注入三條，只是浪費 token；對龍山寺漏掉，
    角色可能對教義或神蹟下判斷，而那發生在一座真實運作的廟前面。

    ⚠️ 斷言的關鍵字必須是**只出現在宗教區塊**的。這條測試第一版寫成
    `"宗教" in rules or "信仰" in rules`，而通則第 2 條本來就有「信仰立場的
    爭議」——結果宗教區塊整段拿掉它照樣綠。mutation 驗證才抓到，所以改成跟
    只含通則的版本對照，讓「多出來的是什麼」自己說話。
    """
    default = get_historical_boundary_rules()
    general_only = get_historical_boundary_rules(include_religious_site_rules=False)

    assert len(default) > len(general_only)
    assert "神蹟" in default
    assert "神蹟" not in general_only


def test_religious_rules_can_be_opted_out_explicitly():
    """要拿掉必須明講。呼叫端忘記傳參數時得到的是比較嚴格的版本。"""
    with_religious = get_historical_boundary_rules()
    without_religious = get_historical_boundary_rules(include_religious_site_rules=False)

    assert without_religious != with_religious
    assert len(without_religious) < len(with_religious)
    # 拿掉宗教規則不能連三條通則一起拿掉。
    assert "定論" in without_religious


def test_religious_rules_refuse_adjudication_in_both_directions():
    """
    §12.2／§3 要的是「不裁決」，不是「否定」。

    「那只是傳說而已」跟「那是真的」同樣是一種裁決，而前者更容易被誤當成安全
    的回答。這條釘住規則文字有把雙向都講到。
    """
    rules = get_historical_boundary_rules()

    assert "背書" in rules or "否定" in rules


# ── 疊加行為 ───────────────────────────────────────────────────────────

_PERSONA_BOUNDARY = "龍山寺 1738 年創建，但早期沿革有多種說法。"
_OTHER_BOUNDARY = "這座橋的通車年份在地方志與報紙記載不一致。"


def test_composition_contains_both_layers():
    """AC：結果同時包含通則與個別規則，兩者互不取代。"""
    composed = compose_factual_boundary(_PERSONA_BOUNDARY)

    assert _PERSONA_BOUNDARY in composed
    assert "定論" in composed  # 通則還在


def test_general_rules_are_identical_across_different_personas():
    """
    AC 反向驗證：換一張個別規則不同的人格卡，通則部分維持不變。

    這條擋的是「把個別設定寫進通則」這種實作——那樣做的話，一張人格卡的內容
    會滲透到所有其他靈魂身上。
    """
    general = get_historical_boundary_rules()

    first = compose_factual_boundary(_PERSONA_BOUNDARY)
    second = compose_factual_boundary(_OTHER_BOUNDARY)

    assert first.startswith(general)
    assert second.startswith(general)
    # 而且彼此不會沾到對方的個別規則。
    assert _OTHER_BOUNDARY not in first
    assert _PERSONA_BOUNDARY not in second


@pytest.mark.parametrize("empty", [None, "", "   ", "\n\n"])
def test_missing_persona_boundary_falls_back_to_general_only(empty):
    """
    AC：人格卡沒有個別史實邊界時回傳只含通則的結果，**不拋例外**。

    人格卡是人工編輯的內容，缺欄位、留白都是預期中的狀態。一張還沒填完的人格卡
    不該讓對話端點掛掉。
    """
    assert compose_factual_boundary(empty) == get_historical_boundary_rules()


# ── 從人格卡取值 ───────────────────────────────────────────────────────

class _PersonaWithBoundary:
    imagination_license = _PERSONA_BOUNDARY


class _LegacyPersonaWithoutTheField:
    """舊版 schema 的人格卡——欄位根本不存在（AC 明列的情境）。"""

    archetype = "沉靜的守望者"


def test_reads_the_boundary_from_a_persona():
    composed = factual_boundary_for_persona(_PersonaWithBoundary())

    assert _PERSONA_BOUNDARY in composed


def test_legacy_persona_without_the_field_does_not_raise():
    """
    AC：欄位不存在的人格卡也要能運作。

    ⚠️ 這不是假設性的相容包袱。migration `0005` 把 `persona_cards` 的單張 JSONB
    換成三層具名欄位，而 issue #19 的 AC 寫的 `factual_boundary` 就是舊 schema
    的欄位——它現在**確實不存在**。見 historical_boundary.py 的模組註解。
    """
    composed = factual_boundary_for_persona(_LegacyPersonaWithoutTheField())

    assert composed == get_historical_boundary_rules()


def test_none_persona_does_not_raise():
    """
    人格卡查不到時回傳通則，而不是讓對話端點 500。

    少了這條，一次找不到人格卡的查詢會變成整支 API 掛掉——而 fallback 本來就
    是這個專案處理內容缺漏的既定策略。
    """
    assert factual_boundary_for_persona(None) == get_historical_boundary_rules()


# ── 文案審核狀態 ───────────────────────────────────────────────────────

def test_rules_are_marked_as_pending_review():
    """
    AC：文案標記為待審核。

    這條會在文案通過審核、有人把標記改成審核者與日期時變紅——**那時候紅是對的**，
    表示該回來確認這份規則真的被審過了，不是預設值放到上線。
    """
    assert RULES_REVIEW_STATUS == "PENDING_NARRATIVE_REVIEW"


def test_review_marker_is_not_injected_into_the_prompt():
    """
    審核標記是給人看的，不該進 prompt。

    少了這條，`PENDING_NARRATIVE_REVIEW` 這串字會被送進 System Instruction，
    模型可能把它當成指示的一部分。
    """
    assert RULES_REVIEW_STATUS not in get_historical_boundary_rules()
    assert RULES_REVIEW_STATUS not in compose_factual_boundary(_PERSONA_BOUNDARY)
