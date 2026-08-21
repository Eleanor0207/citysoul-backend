"""`content/daily_event_notes/*.yaml` 與匯入器驗證層的檢查。

這一池是「平常的日子有沒有東西可看」的唯一輸入（見 scripts/import_daily_event_notes.py
的模組說明），所以它的形狀錯了不會有任何執行期症狀——只會安靜地退回保底文案。
"""

import pathlib

import yaml

from scripts.import_daily_event_notes import PLACEHOLDERS, load_files, validate

NOTES_DIR = pathlib.Path(__file__).resolve().parent.parent / "content" / "daily_event_notes"
SPIRITS_FILE = pathlib.Path(__file__).resolve().parent.parent / "content" / "spirits.yaml"


def _spirit_ids() -> set[str]:
    data = yaml.safe_load(SPIRITS_FILE.read_text(encoding="utf-8"))
    return {s["spirit_id"] for s in data["spirits"]}


def test_every_spirit_has_a_rotation_pool():
    """每個上線的靈魂都要有自己的一池，否則那個地標永遠只有保底文案。"""
    covered = {data["place_id"] for _, data in load_files(NOTES_DIR)}

    assert covered == _spirit_ids()


def test_all_files_pass_the_importer_validation():
    assert validate(load_files(NOTES_DIR)) == []


def test_每池七則且輪播長度一致():
    for path, data in load_files(NOTES_DIR):
        assert len(data["notes"]) == 7, path.name


def test_no_placeholder_text_reaches_the_pool():
    for path, data in load_files(NOTES_DIR):
        for note in data["notes"]:
            for placeholder in PLACEHOLDERS:
                assert placeholder not in note["note_text"], path.name


def test_validation_rejects_a_gap_in_rotation_order():
    broken = [
        (
            pathlib.Path("broken.yaml"),
            {
                "place_id": "longshan_temple",
                "reviewed_by": "Jessie_Lee",
                "notes": [
                    {"rotation_order": 0, "note_text": "A"},
                    {"rotation_order": 2, "note_text": "B"},
                ],
            },
        )
    ]

    errors = validate(broken)

    assert any("rotation_order" in error for error in errors)


def test_validation_rejects_a_duplicate_place_id():
    card = {
        "place_id": "longshan_temple",
        "reviewed_by": "Jessie_Lee",
        "notes": [{"rotation_order": 0, "note_text": "A"}],
    }
    errors = validate([(pathlib.Path("a.yaml"), card), (pathlib.Path("b.yaml"), dict(card))])

    assert any("重複" in error for error in errors)


def test_validation_rejects_placeholder_text():
    errors = validate(
        [
            (
                pathlib.Path("a.yaml"),
                {
                    "place_id": "longshan_temple",
                    "reviewed_by": "Jessie_Lee",
                    "notes": [{"rotation_order": 0, "note_text": "PENDING_HUMAN_REVIEW"}],
                },
            )
        ]
    )

    assert any("佔位字串" in error for error in errors)
