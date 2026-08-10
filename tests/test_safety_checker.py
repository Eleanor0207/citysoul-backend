"""
Ticket #11．B4 角色安全邊界檢查層單元測試。
"""
import pytest

from app.modules.brain.safety import (
    DEFAULT_REFUSAL_REPLY,
    FakeSafetyChecker,
    SafetyResult,
)


@pytest.fixture
def checker():
    return FakeSafetyChecker()


def test_safe_inputs_pass(checker):
    """驗證正常地標、歷史、文化提問正常放行。"""
    safe_queries = [
        "這座廟最早是什麼時候蓋的？",
        "龍山寺周邊有什麼好吃的古早味美食？",
        "廟宇建築上的雕刻代表什麼意義？",
    ]
    for query in safe_queries:
        res = checker.check(query)
        assert res.is_safe is True
        assert res.refusal_reply is None


def test_high_risk_medical_refused(checker):
    """驗證高風險醫療詢問觸發婉拒。"""
    res = checker.check("我這個症狀是不是癌症？")
    assert res.is_safe is False
    assert res.refusal_reply == DEFAULT_REFUSAL_REPLY
    assert "這類專業或個人選擇的問題" in res.refusal_reply


def test_high_risk_legal_refused(checker):
    """驗證高風險法律詢問觸發婉拒。"""
    res = checker.check("鄰居占用我家土地，我可以告他嗎？")
    assert res.is_safe is False
    assert res.refusal_reply == DEFAULT_REFUSAL_REPLY


def test_high_risk_financial_refused(checker):
    """驗證高風險財務投資詢問觸發婉拒。"""
    res = checker.check("神明大人，請問明天明牌幾號？我該買哪支股票？")
    assert res.is_safe is False
    assert res.refusal_reply == DEFAULT_REFUSAL_REPLY


def test_religious_dogma_refused(checker):
    """驗證教義裁決與神明顯靈詢問觸發婉拒。"""
    res = checker.check("聖籤是否準確？神明真的存在嗎？")
    assert res.is_safe is False
    assert res.refusal_reply == DEFAULT_REFUSAL_REPLY
