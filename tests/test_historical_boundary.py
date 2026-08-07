"""
Ticket #19．史實邊界規則注入（B5）。

驗收標準對照見 GitHub issue #19。純函式測試，不需要資料庫或 GCP 憑證。
"""
from app.modules.brain.historical_boundary import (
    combine_with_persona_boundary,
    get_historical_boundary_rules,
)


# ── 通則文字本身（AC1／AC2）───────────────────────────────────────────

def test_returns_the_same_string_across_calls():
    """純函式：不含時間戳或隨機成分，兩次呼叫要回傳完全相同的字串。"""
    assert get_historical_boundary_rules() == get_historical_boundary_rules()


def test_covers_the_three_core_constraints():
    """
    只斷言關鍵語意片段，不逐字比對整段文字——那會讓每次潤稿都變成
    破壞性變更（issue #19 AC2 明訂）。
    """
    rules = get_historical_boundary_rules()
    assert "武斷" in rules  # 不武斷定論
    assert "選邊" in rules  # 不替爭議議題選邊
    assert "編造" in rules  # 不編造未經證實的細節


# ── 跟人格卡個別規則疊加（AC3／AC4）───────────────────────────────────

def test_overlay_contains_both_general_and_persona_specific_text():
    persona_text = "龍山寺 1738 年創建，但早期沿革有多種說法，不同文獻記載不一。"

    combined = combine_with_persona_boundary(persona_text)

    assert get_historical_boundary_rules() in combined
    assert persona_text in combined


def test_general_rules_appear_before_persona_specific_text():
    """通則在前、個別規則在後——順序本身也是這個函式承諾的一部分。"""
    persona_text = "個別規則內容"

    combined = combine_with_persona_boundary(persona_text)

    assert combined.index(get_historical_boundary_rules()) < combined.index(persona_text)


def test_general_rules_unchanged_across_different_personas():
    """
    反向驗證：換一張人格卡（不同的個別規則），疊加結果中通則部分維持不變
    ——不是「有個別規則就整段換掉通則」。
    """
    combined_a = combine_with_persona_boundary("角色 A 的史實立場")
    combined_b = combine_with_persona_boundary("角色 B 的史實立場，完全不同的內容")

    assert get_historical_boundary_rules() in combined_a
    assert get_historical_boundary_rules() in combined_b


# ── 缺欄位時仍可運作（AC5）────────────────────────────────────────────

def test_none_persona_boundary_returns_only_general_rules():
    assert combine_with_persona_boundary(None) == get_historical_boundary_rules()


def test_empty_string_persona_boundary_returns_only_general_rules():
    """空字串（欄位存在但沒填）跟 None（欄位不存在）視為同一種情況。"""
    assert combine_with_persona_boundary("") == get_historical_boundary_rules()
