"""Story-string import validation and atomic backfill behavior."""

from __future__ import annotations

import pathlib

import pytest

from scripts import import_story_strings


def test_source_seed_has_the_eleven_referenced_keys() -> None:
    raw = import_story_strings.DEFAULT_STORY_FILE.read_text(encoding="utf-8")
    texts, title = import_story_strings.parse_story_strings(raw)

    document = import_story_strings.load_story_document(
        import_story_strings.DEFAULT_STORY_FILE
    )
    assert import_story_strings.collect_text_key_references(document) == set(texts)
    assert len(texts) == 11
    assert title == "留給街的信"
    assert texts["wanhua.prologue.look.figure"].startswith("水痕帶走了臉")
    # 信件全文，不是年代簿旁白——見附錄 C。
    assert texts["wanhua.prologue.letter_body"].startswith("我畫了她很多次")


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
