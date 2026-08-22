"""Story-string import validation and atomic backfill behavior."""

from __future__ import annotations

import pathlib

import pytest

from scripts import import_story_strings


def test_source_seed_keys_all_have_a_reference() -> None:
    """每一條字串都被 beats 引用，每一個引用也都有字串。

    2026-08-22：11 → 23 → 59。三批：

    1. 選項**標籤**（`.label`）——玩家選之前看到的字。先前只有敘述，客戶端
       因此把答案當成選項畫出來，三個答案在選擇之前就攤開
    2. 序章匯流句與終章全部（§11.5 的文字早就寫好，從來沒進 beats:）
    3. §11.2／§11.3／§11.4 三章的初次對話與線索對話，含依 story_focus 變化的
       補句與寫入 reveal_lens 的三個選項
    """
    raw = import_story_strings.DEFAULT_STORY_FILE.read_text(encoding="utf-8")
    texts, title = import_story_strings.parse_story_strings(raw)

    document = import_story_strings.load_story_document(
        import_story_strings.DEFAULT_STORY_FILE
    )
    # 道具正文沒有節點在念它，孤兒檢查對它另有例外（見 `_ITEM_BODY_KEYS`）。
    assert (
        import_story_strings.collect_text_key_references(document)
        == set(texts) - import_story_strings._ITEM_BODY_KEYS
    )
    assert len(texts) == 60
    assert title == "留給街的信"
    assert texts["wanhua.prologue.look.figure"].startswith("水痕帶走了臉")
    # 標籤與敘述是兩件事，不能對調。
    assert texts["wanhua.prologue.look.figure.label"] == "她的側影"
    # 終章三個結局標記都要有標籤與回應。
    for mark in ("street", "people", "home"):
        assert texts[f"wanhua.finale.mark.{mark}.label"]
        assert texts[f"wanhua.finale.mark.{mark}"]
    assert texts["wanhua.finale.open"].startswith("這封信仍然沒有署名")

    # 三章的初次對話與線索對話。
    for prefix in ("longshan", "redhouse", "bopiliao"):
        assert texts[f"wanhua.{prefix}.gate.open"]
    # story_focus 的三句補句——序章那個選擇第一次真的產生差異。
    for focus in ("person", "history", "home"):
        assert texts[f"wanhua.longshan.clue.focus.{focus}"]
    # reveal_lens 的三個選項。
    for lens in ("place", "return", "memory"):
        assert texts[f"wanhua.bopiliao.reveal.lens_{lens}.label"]

    # 史實：地震、古地名，都是地標自己的歷史，不與阿明相連（§1.1）。
    assert "一八一五" in texts["wanhua.longshan.gate.ask_place"]
    assert "土炭市" in texts["wanhua.bopiliao.gate.ask_street"]
    assert "新起街市場" in texts["wanhua.redhouse.gate.ask_past"]
    # 信件全文，不是年代簿旁白——見附錄 C。
    assert texts["wanhua.prologue.letter_body"].startswith("我畫了她很多次")

    # 畫的正文（§2.2）。它是道具正文，不是任何人的台詞——`GET /inventory` 顯示用。
    painting = texts["wanhua.item.homeward_painting"]
    assert painting.startswith("一名側身人物站在畫面中央")
    assert "〈回家的畫〉" in painting
    assert "**" not in painting, "粗體記號要在匯入時去掉，背包不是 Markdown 算繪器。"


def test_item_body_keys_are_the_only_orphans_allowed() -> None:
    """例外只給道具正文；其他沒人引用的字串仍然要被擋下來。"""

    import_story_strings.validate_text_key_coverage(
        {"known.key"},
        {"known.key": "已審文字", "wanhua.item.homeward_painting": "畫作描述"},
    )

    with pytest.raises(import_story_strings.StoryStringImportError, match="orphan"):
        import_story_strings.validate_text_key_coverage(
            {"known.key"},
            {"known.key": "已審文字", "wanhua.item.something_else": "不應存在"},
        )


def test_dangling_text_key_is_rejected_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = pathlib.Path(__file__)
    document = {
        "arc": {"arc_id": "arc_wanhua_homeward_painting"},
        "beats": [
            {"nodes": [{"text_key": "fixture.missing"}], "completion": {}}
        ],
        "info_cards": [],
    }
    monkeypatch.setattr(import_story_strings, "load_story_document", lambda _path: document)
    monkeypatch.setattr(
        import_story_strings,
        "parse_story_strings",
        lambda _raw: ({}, "留給街的信"),
    )

    with pytest.raises(import_story_strings.StoryStringImportError) as exc_info:
        import_story_strings.prepare_import(path)

    assert "missing text_key(s): fixture.missing" in str(exc_info.value)


def test_orphan_text_key_is_rejected() -> None:
    with pytest.raises(import_story_strings.StoryStringImportError, match="orphan.mistyped"):
        import_story_strings.validate_text_key_coverage(
            {"known.key"},
            {"known.key": "已審文字", "orphan.mistyped": "不應存在"},
        )


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))


class _RecordingTransaction:
    def __init__(self, connection: _RecordingConnection) -> None:
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class _RecordingEngine:
    def __init__(self) -> None:
        self.connection = _RecordingConnection()
        self.begin_calls = 0

    def begin(self):
        self.begin_calls += 1
        return _RecordingTransaction(self.connection)


def test_successful_import_backfills_arc_intro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = pathlib.Path(__file__)
    document = {
        "arc": {"arc_id": "arc_wanhua_homeward_painting"},
        "beats": [
            {
                "nodes": [
                    {"text_key": "wanhua.prologue.open"},
                    {"text_key": "wanhua.prologue.letter_body"},
                ],
                "completion": {},
            }
        ],
        "info_cards": [],
    }
    monkeypatch.setattr(import_story_strings, "load_story_document", lambda _path: document)
    monkeypatch.setattr(
        import_story_strings,
        "parse_story_strings",
        lambda _raw: (
            {
                "wanhua.prologue.open": "一封已審核的旁白。",
                "wanhua.prologue.letter_body": "一封已審核的信。",
            },
            "留給街的信",
        ),
    )

    engine = _RecordingEngine()
    arc_id, count = import_story_strings.import_story(engine, path)

    assert (arc_id, count) == ("arc_wanhua_homeward_painting", 2)
    assert engine.begin_calls == 1
    # 回填的是 [信] 本文，不是 [系統／年代簿] 的旁白——見附錄 C。
    assert any(
        "UPDATE brain.story_arcs" in sql
        and params["intro_document_content"] == "一封已審核的信。"
        and params["intro_document_title"] == "留給街的信"
        for sql, params in engine.connection.calls
    )
