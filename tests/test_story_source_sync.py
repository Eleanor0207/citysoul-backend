"""`content/story/` 的劇本副本要跟 citysoul-doc 的正本一致。

## 為什麼需要副本

`import_story_arcs` 與 `import_story_strings` 讀的是隔壁 repo 的正本。Cloud Run
的 Job 用 backend 的映像檔，裡面沒有那個目錄——劇情內容因此**從來沒有正式的
上線路徑**，線上那幾個 beat 是某次本機直連正式資料庫匯進去的。

## 為什麼需要這條測試

有了副本就有了漂移的可能：Lead 改了 doc、沒有同步，映像檔裡就是舊劇情，而
**Job 的輸出看不出來**——它會很正常地印出它讀到的那一版。

匯入器優先讀正本，所以本機不會匯錯；會出事的是容器。這條測試把那個落差變成
紅燈。
"""
import pytest

from scripts import sync_story_source


def test_vendored_copy_exists() -> None:
    """副本必須進版控——它是映像檔裡唯一的劇本來源。"""
    assert sync_story_source.VENDORED.is_file(), (
        f"缺少 {sync_story_source.VENDORED}；"
        "跑 `python -m scripts.sync_story_source` 產生並一起 commit"
    )


def test_vendored_copy_matches_the_source() -> None:
    """兩份內容要逐字相同。

    正本不在（例如 CI 只 checkout 了 backend）時 skip——那不是漂移，是環境
    沒有正本可比。真正會出事的情境是「兩份都在但不同」，那時這條會紅。
    """
    if not sync_story_source.SOURCE.is_file():
        pytest.skip("citysoul-doc 不在旁邊，無從比對")

    source = sync_story_source.SOURCE.read_text(encoding="utf-8")
    vendored = sync_story_source.VENDORED.read_text(encoding="utf-8")

    assert source == vendored, (
        "劇本副本與 citysoul-doc 的正本不一致。"
        "跑 `python -m scripts.sync_story_source` 同步，然後一起 commit"
    )


def test_importers_prefer_the_source_over_the_copy() -> None:
    """
    優先序反過來的話，Lead 改了 doc、忘了同步，本機匯入會安靜地匯進舊內容
    ——那種錯誤沒有症狀，只是劇情停在上一版。
    """
    from scripts import import_story_arcs, import_story_strings

    for module in (import_story_arcs, import_story_strings):
        if module._STORY_SOURCE.is_file():
            assert module.DEFAULT_STORY_FILE == module._STORY_SOURCE
        else:
            assert module.DEFAULT_STORY_FILE == module._STORY_VENDORED
