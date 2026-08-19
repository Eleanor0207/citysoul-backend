"""Story arc importer rejects invalid prerequisite graphs before writing."""

from __future__ import annotations

import pathlib

import pytest

from scripts import import_story_arcs


class _WriteTrackingEngine:
    def __init__(self) -> None:
        self.begin_calls = 0

    def begin(self):
        self.begin_calls += 1
        raise AssertionError("invalid story content must not open a write transaction")


@pytest.mark.parametrize(
    ("name", "beats", "message", "offending"),
    [
        (
            "dangling",
            [
                {"beat_id": "beat_a", "prerequisite_beat_ids": ["missing"]},
            ],
            "dangling prerequisite_beat_ids",
            "beat_a",
        ),
        (
            "cycle",
            [
                {"beat_id": "beat_root", "prerequisite_beat_ids": []},
                {"beat_id": "beat_a", "prerequisite_beat_ids": ["beat_root", "beat_b"]},
                {"beat_id": "beat_b", "prerequisite_beat_ids": ["beat_a"]},
            ],
            "cycle in prerequisite graph",
            "beat_a",
        ),
        (
            "island",
            [
                {"beat_id": "beat_root", "prerequisite_beat_ids": []},
                {"beat_id": "beat_island", "prerequisite_beat_ids": ["beat_island"]},
            ],
            "island beat",
            "beat_island",
        ),
    ],
)
def test_broken_fixture_is_rejected_before_any_write(
    name: str,
    beats: list[dict],
    message: str,
    offending: str,
    monkeypatch: pytest.MonkeyPatch,
):
    document = {"arc": {"arc_id": "arc_fixture", "title": "Fixture"}, "beats": beats}
    monkeypatch.setattr(
        import_story_arcs,
        "load_story_document",
        lambda _path: document,
    )
    engine = _WriteTrackingEngine()

    with pytest.raises(import_story_arcs.StoryImportError, match=message) as exc_info:
        import_story_arcs.import_story(engine, pathlib.Path("broken_story.md"))

    assert offending in str(exc_info.value)
    assert engine.begin_calls == 0, name

