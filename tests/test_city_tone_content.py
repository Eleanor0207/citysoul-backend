"""`content/city.yaml`（brain.city_souls 那唯一一列）的內容驗收。

## 為什麼這一份要單獨測

城市層只有一列。地標層有九份，一份寫壞了還有八份撐著；城市層寫壞了就是整層壞掉。
而它目前**沒有執行期效果**（`prompt_builder` 還沒讀 city_souls），所以壞掉不會有
任何一條生成路徑會叫——只有測試會。

## 高度：九個地標都成立才算城市層

最容易犯的錯是拿某一區的基調頂替（`--city-tone-from` 就是那條路）。那會讓臺北用
萬華或士林的口吻講話，正是 migration `0016` 把區級基調拆出去要避免的事。所以這裡
會擋下「城市層跟任何一份地標檔的區級 city_tone 一字不差」。
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from scripts.import_landmarks import CITY_YAML, CONTENT_DIR, PLACEHOLDERS, validate_city

# 只有某一區才成立的字眼。出現在城市層代表高度掉下去了。
DISTRICT_ONLY_WORDS = ("香火", "菸廠", "星空", "展場", "雙年展", "觀音", "文物")


@pytest.fixture(scope="module")
def city() -> dict:
    return yaml.safe_load(CITY_YAML.read_text(encoding="utf-8"))


def test_city_yaml_exists() -> None:
    assert CITY_YAML.exists(), "城市層沒有內容檔，import_landmarks 會整層跳過"


def test_passes_importer_validation(city: dict) -> None:
    assert validate_city(city) == []


def test_no_placeholders(city: dict) -> None:
    blob = yaml.safe_dump(city, allow_unicode=True)
    for ph in PLACEHOLDERS:
        assert ph not in blob


def test_active_stays_false_until_prompt_builder_reads_it(city: dict) -> None:
    """
    `prompt_builder` 還沒讀 city_souls。翻成 true 的那一次改動，應該同時是接上
    prompt 的那一次——這條測試就是為了讓「先翻了但沒接」被擋下來。
    """
    assert city["active"] is False


def test_summary_is_city_altitude(city: dict) -> None:
    summary = city["macro_history_summary"]
    assert len(summary) >= 150, "城市層太短，撐不起九個地標共用的那一層"
    for word in DISTRICT_ONLY_WORDS:
        assert word not in summary, f"「{word}」只有某一區成立，不該出現在城市層"


def test_not_borrowed_from_a_district(city: dict) -> None:
    """城市層不能是某一份地標檔的區級 city_tone 原封搬過來。"""
    for path in sorted(pathlib.Path(CONTENT_DIR).glob("*.yaml")):
        tone = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("city_tone")
        if not tone:
            continue
        assert tone.get("macro_history_summary") != city["macro_history_summary"], (
            f"城市層跟 {path.name} 的區級基調一字不差"
        )
        assert tone.get("core_tone_descriptors") != city["core_tone_descriptors"], (
            f"城市層跟 {path.name} 的區級基調一字不差"
        )


def test_validate_city_catches_placeholder() -> None:
    bad = {
        "city_id": "taipei",
        "name": "臺北",
        "macro_history_summary": "PENDING_NARRATIVE_REVIEW",
        "core_tone_descriptors": ["盆地"],
        "shared_values": ["共存"],
    }
    assert any("macro_history_summary" in e for e in validate_city(bad))


def test_validate_city_catches_empty_list() -> None:
    bad = {
        "city_id": "taipei",
        "name": "臺北",
        "macro_history_summary": "一段夠長的敘述。" * 20,
        "core_tone_descriptors": [],
        "shared_values": ["共存"],
    }
    assert any("core_tone_descriptors" in e for e in validate_city(bad))
