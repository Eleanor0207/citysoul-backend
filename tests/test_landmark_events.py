"""
`landmark_events`：地標官方公開活動的匯入、審核閘與當日挑選（0022）。

分兩半，對應兩個模組：

- **純函式**（`body/landmark_events.py`）——座標過濾、比對、檔期。不碰資料庫，
  所以「一筆髒座標會不會被丟掉」可以直接餵值驗證。
- **資料庫**（`body/landmark_events_service.py`）——UPSERT 的審核退回行為、
  挑選規則，對真實 Postgres 跑。

⚠️ 這裡最重要的一組是**審核閘**：沒有任何自動路徑會把 `active` 設成 true，
而且內容變了要把已放行的退回。那是「靠自動抓取的外部內容」唯一的攔截點。
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.modules.body import landmark_events as rules
from app.modules.body import landmark_events_service as service
from app.modules.body import models
from app.modules.body.daily_event_service import refresh_from_landmark_events
from app.modules.body.quests import taipei_today
from app.modules.brain.gemini import FakeGeminiClient

# 龍山寺附近。第二個點刻意放在約 200 公尺外——在預設 300m 半徑內。
_LAT, _LON = 25.037398, 121.499732
_NEARBY_LAT, _NEARBY_LON = 25.039200, 121.499732  # 約 200 m 北
_FAR_LAT, _FAR_LON = 25.060000, 121.499732  # 約 2.5 km 北

_TODAY = date(2026, 8, 18)


# ══ 純函式：座標品質 ════════════════════════════════════════════════════


def test_zero_island_is_rejected():
    """`0, 0` 是場館沒填座標的典型結果，不是幾內亞灣外海的展覽。"""
    assert rules.is_plausible_taipei_coord(0, 0) is False


def test_other_cities_are_rejected():
    """全國資料集本來就包含全國。高雄的展覽不該對到台北的地標。"""
    assert rules.is_plausible_taipei_coord(22.6273, 120.3014) is False


def test_swapped_lat_lon_is_rejected():
    """
    🔒 經緯度寫反是最陰險的一種髒資料：距離計算不會報錯，只會安靜地永遠對不到
    任何地標。表現出來是「那個地標永遠沒有活動」，而那看起來像內容不足。
    """
    assert rules.is_plausible_taipei_coord(121.4997, 25.0374) is False


def test_a_real_taipei_venue_is_accepted():
    assert rules.is_plausible_taipei_coord(_LAT, _LON) is True


def test_non_numeric_coords_are_rejected():
    assert rules.is_plausible_taipei_coord("", "") is False
    assert rules.is_plausible_taipei_coord(None, None) is False
    assert rules.is_plausible_taipei_coord("北緯25度", "121.5") is False


# ══ 純函式：比對 ════════════════════════════════════════════════════════


def _candidates():
    return [
        ("longshan_temple", _LAT, _LON),
        ("far_away_place", _FAR_LAT, _FAR_LON),
    ]


def test_nearest_within_picks_the_closer_landmark():
    hit = rules.nearest_within(_NEARBY_LAT, _NEARBY_LON, _candidates(), radius_m=300)

    assert hit is not None
    assert hit[0] == "longshan_temple"


def test_nothing_within_the_radius_returns_none():
    """半徑外就是沒對上——不是「勉強算最近的那個」。"""
    assert rules.nearest_within(_NEARBY_LAT, _NEARBY_LON, _candidates(), radius_m=50) is None


def test_ties_break_deterministically():
    """
    兩個地標到同一個場館等距離幾乎不可能，但不穩定的結果會讓同一批資料重跑兩次
    得到兩個答案——那種 bug 極難追。平手取 `spirit_id` 較小的。
    """
    tied = [("b_place", _LAT, _LON), ("a_place", _LAT, _LON)]

    first = rules.nearest_within(_LAT, _LON, tied, radius_m=300)
    second = rules.nearest_within(_LAT, _LON, list(reversed(tied)), radius_m=300)

    assert first[0] == second[0] == "a_place"


def test_unmatched_records_are_returned_not_dropped():
    """
    🔒 沒對上的數量是判斷半徑設得對不對的**唯一依據**。只回對上的話，
    「radius 設太小」跟「今天真的沒活動」在輸出上長得一模一樣。
    """
    records = [
        {"event_id": "a", "title": "近的", "latitude": _NEARBY_LAT, "longitude": _NEARBY_LON},
        {"event_id": "b", "title": "遠的", "latitude": 25.09, "longitude": 121.60},
    ]

    matched, unmatched = rules.match_to_spirits(records, _candidates(), radius_m=300)

    assert [row["event_id"] for row in matched] == ["a"]
    assert [row["event_id"] for row in unmatched] == ["b"]


def test_matched_rows_drop_the_coordinates():
    """
    ⚠️ 場館座標只是比對的中間值，不落地。沒有用途的欄位留在資料裡，
    下一個人會開始拿它做別的事。
    """
    records = [
        {"event_id": "a", "title": "近的", "latitude": _NEARBY_LAT, "longitude": _NEARBY_LON}
    ]

    matched, _ = rules.match_to_spirits(records, _candidates(), radius_m=300)

    assert "latitude" not in matched[0]
    assert "longitude" not in matched[0]
    assert matched[0]["spirit_id"] == "longshan_temple"


# ══ 純函式：檔期 ════════════════════════════════════════════════════════


def test_event_without_an_end_date_is_never_featured():
    """
    🔒 沒有結束日期就不推。

    另一邊更糟：一筆日期解析失敗的活動如果被當成「永遠有效」，它會從此每次
    輪到那個地標都被推出來，而且沒有任何東西會提醒我們。
    """
    assert rules.is_running_on(date(2026, 1, 1), None, _TODAY) is False


def test_finished_event_is_not_featured():
    assert rules.is_running_on(date(2026, 1, 1), date(2026, 8, 17), _TODAY) is False


def test_event_ending_today_is_still_featured():
    """展到今天的展覽，今天仍然看得到。邊界含當日。"""
    assert rules.is_running_on(date(2026, 1, 1), _TODAY, _TODAY) is True


def test_a_future_event_is_not_running_yet():
    """`is_running_on` 只回答「現在進行中嗎」。還沒開演就不是。"""
    assert rules.is_running_on(date(2026, 9, 1), date(2026, 12, 1), _TODAY) is False


def test_missing_start_date_is_treated_as_already_started():
    """場館常常只填結束日期，那筆資料仍然可用。"""
    assert rules.is_running_on(None, date(2026, 12, 1), _TODAY) is True


# ══ 純函式：推播資格（前置期） ══════════════════════════════════════════


def test_a_single_day_show_is_featurable_before_it_happens():
    """
    🔒 這一條是整個功能能不能用的關鍵。

    2026-08-18 實測：抓回來的 22 筆有 18 筆是**單日**演出。只推「今天正在進行」
    的話，一場 9/11 的音樂會只有 9/11 那天推得出來，而 DailyFeature 十天才輪到
    那個地標一次——兩件事撞在一起的機率趨近於零。

    而且廣告本來就需要前置期：演出當天才打廣告，玩家已經買不到票了。
    """
    concert = date(2026, 9, 11)  # _TODAY 之後 24 天

    assert rules.is_running_on(concert, concert, _TODAY) is False
    assert rules.is_featurable_on(concert, concert, _TODAY) is True


def test_events_beyond_the_lead_window_are_not_featured_yet():
    """
    太早開始推，同一場活動會反覆出現到玩家開始無視它。
    """
    far = _TODAY + timedelta(days=rules.FEATURE_LEAD_DAYS + 1)

    assert rules.is_featurable_on(far, far, _TODAY) is False


def test_the_lead_window_boundary_is_inclusive():
    """邊界剛好第 30 天要推得出來——差一天的規則最容易寫錯，所以釘住它。"""
    edge = _TODAY + timedelta(days=rules.FEATURE_LEAD_DAYS)

    assert rules.is_featurable_on(edge, edge, _TODAY) is True


def test_a_finished_event_is_never_featurable():
    """前置期只放寬「還沒開始」那一端，**結束了就是結束了**。"""
    assert rules.is_featurable_on(date(2026, 1, 1), date(2026, 8, 17), _TODAY) is False


def test_an_event_without_an_end_date_is_never_featurable():
    """
    🔒 跟 `is_running_on` 同一條規則：日期解析失敗的活動如果被當成永遠有效，
    它會從此每次都被推出來，而且沒有任何東西會提醒我們。
    """
    assert rules.is_featurable_on(date(2026, 1, 1), None, _TODAY) is False


# ══ 純函式：日期解析 ════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026/08/18", date(2026, 8, 18)),
        ("2026-08-18", date(2026, 8, 18)),
        ("2026/08/18 00:00:00", date(2026, 8, 18)),
        ("2026-08-18T10:30:00", date(2026, 8, 18)),
        ("20260818", date(2026, 8, 18)),
    ],
)
def test_parses_the_date_formats_open_data_actually_uses(raw, expected):
    assert rules.parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "近期", "2026/13/45", "洽詢主辦單位"])
def test_unparseable_dates_return_none_instead_of_raising(raw):
    """
    一筆日期壞掉不該讓整批匯入失敗——那會讓幾百筆好資料陪葬。壞掉的那筆會在
    `is_running_on()` 被擋下來。
    """
    assert rules.parse_date(raw) is None


# ══ 純函式：正規化 ══════════════════════════════════════════════════════


def _iculture_record(uid="A1", **overrides):
    raw = {
        "UID": uid,
        "title": "浮光：當代攝影特展",
        "descriptionFilterHtml": "<p>本展覽<b>展出</b>三十位攝影家的作品。</p>",
        "startDate": "2026/08/01",
        "endDate": "2026/10/31",
        "sourceWebPromote": "https://example.org/show/A1",
        "showInfo": [
            {
                "latitude": str(_NEARBY_LAT),
                "longitude": str(_NEARBY_LON),
                "locationName": "測試館",
                "startTime": "2026/08/01",
                "endTime": "2026/10/31",
            }
        ],
    }
    raw.update(overrides)
    return raw


def test_html_is_stripped_from_the_summary():
    """開放資料的簡介欄位夾著 `<p>`／`&nbsp;`，直接進 prompt 是在餵標籤給模型。"""
    record = rules.normalize_iculture([_iculture_record()])[0]

    assert "<" not in record["summary"]
    assert "本展覽 展出 三十位攝影家的作品。" == record["summary"]


def test_summary_is_capped():
    """
    🔴 簡介唯一的去處是 Gemini 的 prompt。開放資料的描述動輒上千字，而
    「AI 對話成本與延遲」是 SDD 標的高風險項。
    """
    long_text = "字" * (rules.SUMMARY_MAX_CHARS + 500)
    record = rules.normalize_iculture(
        [_iculture_record(descriptionFilterHtml=long_text)]
    )[0]

    assert len(record["summary"]) <= rules.SUMMARY_MAX_CHARS + 1  # +1 是省略號


def test_records_without_a_uid_or_title_are_dropped():
    """沒有穩定識別碼就沒辦法冪等更新；沒有標題就沒有東西可以顯示。"""
    assert rules.normalize_iculture([_iculture_record(UID="")]) == []
    assert rules.normalize_iculture([_iculture_record(title="")]) == []


def test_a_touring_show_becomes_one_record_per_venue():
    """一個展覽巡迴多個場館，而我們是把**場館**對到地標的。"""
    raw = _iculture_record(
        showInfo=[
            {"latitude": str(_LAT), "longitude": str(_LON), "locationName": "甲館"},
            {
                "latitude": str(_NEARBY_LAT),
                "longitude": str(_NEARBY_LON),
                "locationName": "乙館",
            },
        ]
    )

    records = rules.normalize_iculture([raw])

    assert len(records) == 2
    assert len({r["event_id"] for r in records}) == 2


def test_venue_index_is_stable_across_runs():
    """
    🔒 序號會變的話，同一場活動每次抓都會被當成新的一筆——舊的那筆永遠不會被
    更新，也永遠不會消失。

    驗證方式是把 showInfo 的順序打亂，`event_id` 對應到的場館必須不變。
    """
    venues = [
        {"latitude": str(_LAT), "longitude": str(_LON), "locationName": "甲館"},
        {
            "latitude": str(_NEARBY_LAT),
            "longitude": str(_NEARBY_LON),
            "locationName": "乙館",
        },
    ]

    forward = rules.normalize_iculture([_iculture_record(showInfo=venues)])
    backward = rules.normalize_iculture([_iculture_record(showInfo=list(reversed(venues)))])

    assert {r["event_id"]: r["venue_name"] for r in forward} == {
        r["event_id"]: r["venue_name"] for r in backward
    }


def test_duplicate_sessions_at_one_venue_collapse():
    """同一個場館的多個場次會重複出現，但那是一個地方、一場活動。"""
    same = {"latitude": str(_LAT), "longitude": str(_LON), "locationName": "甲館"}

    records = rules.normalize_iculture([_iculture_record(showInfo=[same, dict(same)])])

    assert len(records) == 1


def test_venues_outside_taipei_are_dropped_but_the_show_survives():
    """全國巡迴展只留台北那一站，不是整場丟掉。"""
    raw = _iculture_record(
        showInfo=[
            {"latitude": "22.6273", "longitude": "120.3014", "locationName": "高雄館"},
            {"latitude": str(_LAT), "longitude": str(_LON), "locationName": "台北館"},
        ]
    )

    records = rules.normalize_iculture([raw])

    assert len(records) == 1
    assert records[0]["venue_name"] == "台北館"


def test_session_dates_win_over_the_overall_dates():
    """巡迴展在每個場館的檔期不同，場次日期比較準。"""
    raw = _iculture_record(
        startDate="2026/01/01",
        endDate="2026/12/31",
        showInfo=[
            {
                "latitude": str(_LAT),
                "longitude": str(_LON),
                "startTime": "2026/08/01",
                "endTime": "2026/08/31",
            }
        ],
    )

    record = rules.normalize_iculture([raw])[0]

    assert record["start_date"] == date(2026, 8, 1)
    assert record["end_date"] == date(2026, 8, 31)


# ══ 純函式：內容指紋 ════════════════════════════════════════════════════


def test_fingerprint_changes_when_the_content_changes():
    base = {"spirit_id": "s", "title": "甲展", "end_date": date(2026, 9, 1)}
    changed = dict(base, title="乙展")

    assert rules.content_fingerprint(base) != rules.content_fingerprint(changed)


def test_fingerprint_ignores_fields_that_are_not_content():
    """
    ⚠️ `fetched_at` 每次抓都會變。納入指紋的話，每一次抓取都會把全部活動打回
    未審核——審核閘就變成了「永遠審不完」。
    """
    base = {"spirit_id": "s", "title": "甲展", "end_date": date(2026, 9, 1)}
    noisy = dict(base, fetched_at="2026-08-18", active=True, distance_m=12.3)

    assert rules.content_fingerprint(base) == rules.content_fingerprint(noisy)


# ══ 資料庫：fixtures ════════════════════════════════════════════════════


@pytest.fixture
def spirit(db_session, unique_spirit_id):
    row = models.Spirit(
        spirit_id=unique_spirit_id,
        display_name="測試地標",
        latitude=_LAT,
        longitude=_LON,
        summon_radius_meters=50,
        sense_radius_meters=150,
        is_active=True,
    )
    db_session.add(row)
    db_session.commit()
    yield row
    # 先清子表：landmark_events.spirit_id 是指向 spirits 的外鍵。
    db_session.query(models.LandmarkEvent).filter_by(spirit_id=unique_spirit_id).delete()
    db_session.query(models.DailyEventCache).filter_by(place_id=unique_spirit_id).delete()
    db_session.delete(row)
    db_session.commit()


def _record(spirit_id, *, event_id="iculture:T1:0", title="甲展", end_date=None, **extra):
    row = {
        "event_id": event_id,
        "spirit_id": spirit_id,
        "source": rules.SOURCE_ICULTURE,
        "title": title,
        "summary": "一段簡介",
        "venue_name": "測試館",
        "start_date": date(2026, 1, 1),
        "end_date": end_date or date(2026, 12, 31),
        "source_url": "https://example.org/show",
    }
    row.update(extra)
    row["content_hash"] = rules.content_fingerprint(row)
    return row


def _get(db_session, event_id):
    db_session.expire_all()
    return db_session.query(models.LandmarkEvent).filter_by(event_id=event_id).first()


# ══ 資料庫：抓取白名單 ══════════════════════════════════════════════════


def test_only_whitelisted_spirits_are_fetched_for(db_session, spirit):
    """
    🔒 AC：預設只對 `VERIFIED_VENUE_SPIRITS` 抓活動。

    地理比對回答不了「這場活動是誰辦的」——霞海城隍廟 42 公尺外就是大稻埕戲苑，
    比西門紅樓到自己的劇場還近（2026-08-18 實測）。放進來的後果是城隍廟的靈魂
    開始講隔壁戲苑的歌仔戲，而審核時光看標題不見得看得出來。

    `spirit` fixture 造的是隨機 id，一定不在白名單裡，所以它不該出現。
    """
    candidates = service.spirit_candidates(db_session)

    ids = {row[0] for row in candidates}
    assert spirit.spirit_id not in ids
    assert ids <= set(rules.VERIFIED_VENUE_SPIRITS)


def test_the_whitelist_can_be_overridden_for_diagnosis(db_session, spirit):
    """`--diagnose --all-spirits` 要看得到全部。傳空的 tuple 代表不套白名單。"""
    ids = {row[0] for row in service.spirit_candidates(db_session, only=())}

    assert spirit.spirit_id in ids


def test_inactive_spirits_are_never_candidates(db_session, spirit):
    """下架的地標不該累積之後要清理的資料。白名單開著或關著都一樣。"""
    spirit.is_active = False
    db_session.commit()

    ids = {row[0] for row in service.spirit_candidates(db_session, only=())}

    assert spirit.spirit_id not in ids


# ══ 資料庫：審核閘 ══════════════════════════════════════════════════════


def test_imported_events_start_unreviewed(db_session, spirit):
    """
    🔒 AC：抓取寫進去的內容一律未審核。

    這條是整個功能的安全前提——外部資料在有人看過之前不會出現在玩家面前。
    """
    service.upsert_events(db_session, [_record(spirit.spirit_id)])

    assert _get(db_session, "iculture:T1:0").active is False


def test_reimporting_unchanged_content_keeps_the_approval(db_session, spirit):
    """
    每天都會重抓。內容沒變卻把審核狀態洗掉的話，放行過的活動隔天就消失了
    ——那等於這個功能永遠推不出東西。
    """
    record = _record(spirit.spirit_id)
    service.upsert_events(db_session, [record])
    service.approve(db_session, record["event_id"], reviewer="AL")

    service.upsert_events(db_session, [_record(spirit.spirit_id)])

    row = _get(db_session, record["event_id"])
    assert row.active is True
    assert row.reviewed_by == "AL"


def test_changed_content_revokes_the_approval(db_session, spirit):
    """
    🔒 AC：內容變了（改期、改名）→ `active` 退回 false、審核簽名清空。

    內容換了而簽名留著，等於讓上一次的審核替這一次的文字背書——那比一開始就
    沒有審核更糟，因為它看起來是有審核的。同
    `import_landmarks.UPSERT_DISTRICT`。
    """
    record = _record(spirit.spirit_id)
    service.upsert_events(db_session, [record])
    service.approve(db_session, record["event_id"], reviewer="AL")

    service.upsert_events(db_session, [_record(spirit.spirit_id, title="改名後的展覽")])

    row = _get(db_session, record["event_id"])
    assert row.active is False
    assert row.reviewed_by is None
    assert row.reviewed_at is None


def test_a_date_change_also_revokes_the_approval(db_session, spirit):
    """改期是最常見的變動，而且它直接決定「今天要不要推」。"""
    record = _record(spirit.spirit_id)
    service.upsert_events(db_session, [record])
    service.approve(db_session, record["event_id"], reviewer="AL")

    service.upsert_events(
        db_session, [_record(spirit.spirit_id, end_date=date(2027, 6, 30))]
    )

    assert _get(db_session, record["event_id"]).active is False


def test_approve_requires_a_named_reviewer(db_session, spirit):
    """
    🔒 預設一個 `system` 之類的簽名，等於允許沒有人負責的審核——那審核閘就只剩
    形式。
    """
    service.upsert_events(db_session, [_record(spirit.spirit_id)])

    with pytest.raises(ValueError):
        service.approve(db_session, "iculture:T1:0", reviewer="  ")


def test_revoke_clears_the_signature(db_session, spirit):
    """簽名留著的話，下次看會以為它是審過的。"""
    service.upsert_events(db_session, [_record(spirit.spirit_id)])
    service.approve(db_session, "iculture:T1:0", reviewer="AL")

    service.revoke(db_session, "iculture:T1:0")

    row = _get(db_session, "iculture:T1:0")
    assert row.active is False
    assert row.reviewed_by is None


def test_upsert_counts_inserts_and_updates_separately(db_session, spirit):
    """
    ⚠️ 不要用 `rowcount` 判斷新增或更新：psycopg3 在 `INSERT ... ON CONFLICT`
    上會回 -1，而 `bool(-1)` 是 True——2026-08-18 在 collections_service 踩過
    同一個坑，這裡改用 `RETURNING (xmax = 0)`。
    """
    first = service.upsert_events(db_session, [_record(spirit.spirit_id)])
    second = service.upsert_events(db_session, [_record(spirit.spirit_id)])

    assert (first["inserted"], first["updated"]) == (1, 0)
    assert (second["inserted"], second["updated"]) == (0, 1)


# ══ 資料庫：挑選 ════════════════════════════════════════════════════════


def test_unapproved_events_are_never_featured(db_session, spirit):
    """🔒 這是審核閘真正生效的地方。"""
    service.upsert_events(db_session, [_record(spirit.spirit_id)])

    assert service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY) is None


def test_approved_event_is_featured(db_session, spirit):
    service.upsert_events(db_session, [_record(spirit.spirit_id)])
    service.approve(db_session, "iculture:T1:0", reviewer="AL")

    row = service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY)

    assert row is not None
    assert row.title == "甲展"


def test_finished_events_are_not_featured_even_when_approved(db_session, spirit):
    """放行過的展覽也會結束。檔期判斷在挑選時做，不是放行時做。"""
    service.upsert_events(
        db_session, [_record(spirit.spirit_id, end_date=date(2026, 8, 1))]
    )
    service.approve(db_session, "iculture:T1:0", reviewer="AL")

    assert service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY) is None


def test_an_upcoming_single_day_show_is_featured(db_session, spirit):
    """
    🔒 端到端版的前置期規則：單日演出在開演前就要推得出來。

    這是 2026-08-18 從真實資料發現的——只推「今天正在進行」的話，抓回來的 22 筆
    有 18 筆永遠不會出現在玩家面前。
    """
    concert = _TODAY + timedelta(days=20)
    service.upsert_events(
        db_session,
        [_record(spirit.spirit_id, title="單日音樂會", start_date=concert, end_date=concert)],
    )
    service.approve(db_session, "iculture:T1:0", reviewer="AL")

    row = service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY)

    assert row is not None
    assert row.title == "單日音樂會"


def test_the_soonest_to_end_wins(db_session, spirit):
    """
    「限時」是這個功能的賣點：剩三天的展覽比剩三個月的更值得現在推。

    並列時用 `event_id` 決勝——不穩定的話，同一天重新排程會換一場活動，
    而昨天生成的敘事就跟今天的活動卡對不起來了。
    """
    service.upsert_events(
        db_session,
        [
            _record(spirit.spirit_id, event_id="iculture:A:0", title="慢的", end_date=date(2026, 12, 1)),
            _record(spirit.spirit_id, event_id="iculture:B:0", title="快的", end_date=date(2026, 8, 25)),
        ],
    )
    service.approve(db_session, "iculture:A:0", reviewer="AL")
    service.approve(db_session, "iculture:B:0", reviewer="AL")

    row = service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY)

    assert row.title == "快的"


def test_a_running_exhibition_beats_a_show_that_has_not_opened(db_session, spirit):
    """
    🔒 玩家人已經站在地標前了：走幾步就進得去的，勝過還要另外買票挑一天再來的。

    這是 2026-08-19 從真實資料發現的。松山文創放行 21 筆後推演，櫻桃小丸子特展
    （6/18～9/28）**整個檔期只有最後一天 9/28 會被推出去**——另外 17 場單日演出
    的 `end_date` 全排在它前面，一路把它壓到最後。

    這裡的音樂會結束得比展覽早，照舊規則會贏；照新規則展覽贏，因為它今天就開著，
    而音樂會還有 10 天才開演（＞ `PRIORITY_LEAD_DAYS`，還沒到插隊的時候）。
    """
    concert = _TODAY + timedelta(days=10)
    service.upsert_events(
        db_session,
        [
            _record(
                spirit.spirit_id,
                event_id="iculture:EXPO:0",
                title="正在展出的特展",
                start_date=_TODAY - timedelta(days=30),
                end_date=_TODAY + timedelta(days=40),
            ),
            _record(
                spirit.spirit_id,
                event_id="iculture:GIG:0",
                title="還沒開演的單日音樂會",
                start_date=concert,
                end_date=concert,
            ),
        ],
    )
    service.approve(db_session, "iculture:EXPO:0", reviewer="AL")
    service.approve(db_session, "iculture:GIG:0", reviewer="AL")

    row = service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY)

    assert row.title == "正在展出的特展"


def _running_expo_and_a_show_opening_in(db_session, spirit, days):
    """一檔正在展出的長展覽，加一場 `days` 天後開演的單日演出。兩筆都放行。"""
    show = _TODAY + timedelta(days=days)
    service.upsert_events(
        db_session,
        [
            _record(
                spirit.spirit_id,
                event_id="iculture:EXPO:0",
                title="正在展出的特展",
                start_date=_TODAY - timedelta(days=30),
                end_date=_TODAY + timedelta(days=40),
            ),
            _record(
                spirit.spirit_id,
                event_id="iculture:GIG:0",
                title="即將開演",
                start_date=show,
                end_date=show,
            ),
        ],
    )
    service.approve(db_session, "iculture:EXPO:0", reviewer="AL")
    service.approve(db_session, "iculture:GIG:0", reviewer="AL")
    return service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY)


def test_a_show_opening_within_a_week_jumps_the_queue(db_session, spirit):
    """
    🔒 插隊層**不能只有「今天開著」**，否則單日演出的售票前置期會被長展覽吃光。

    只看「今天開著」的話，只要有一檔展覽開著，演出就只剩開演當天露得出來——
    而那天玩家已經來不及訂票，等於把 2026-08-18 加前置期的理由整個抵消掉。
    """
    assert _running_expo_and_a_show_opening_in(db_session, spirit, 5).title == "即將開演"


def test_the_priority_lead_window_has_an_edge(db_session, spirit):
    """邊界要釘住：第 7 天算插隊，第 8 天不算。"""
    assert _running_expo_and_a_show_opening_in(
        db_session, spirit, rules.PRIORITY_LEAD_DAYS
    ).title == "即將開演"


def test_a_show_just_outside_the_lead_window_does_not_jump(db_session, spirit):
    assert _running_expo_and_a_show_opening_in(
        db_session, spirit, rules.PRIORITY_LEAD_DAYS + 1
    ).title == "正在展出的特展"


def test_priority_lead_is_shorter_than_feature_lead():
    """
    ⚠️ 兩個前置期回答不同的問題，沿用同一個數字會讓插隊層失去作用。

    `FEATURE_LEAD_DAYS`(30) 決定推不推得出去；`PRIORITY_LEAD_DAYS`(7) 決定誰先推。
    若兩者相等，松山文創那種資料形狀下十幾場演出會全部擠進插隊層。
    """
    assert rules.PRIORITY_LEAD_DAYS < rules.FEATURE_LEAD_DAYS


def test_the_soonest_to_end_still_wins_among_running_events(db_session, spirit):
    """第一層只分「開著沒有」，同一層之內仍然是最快結束的優先。"""
    service.upsert_events(
        db_session,
        [
            _record(
                spirit.spirit_id,
                event_id="iculture:LONG:0",
                title="開很久的",
                start_date=_TODAY - timedelta(days=10),
                end_date=_TODAY + timedelta(days=60),
            ),
            _record(
                spirit.spirit_id,
                event_id="iculture:SHORT:0",
                title="快結束的",
                start_date=_TODAY - timedelta(days=10),
                end_date=_TODAY + timedelta(days=3),
            ),
        ],
    )
    service.approve(db_session, "iculture:LONG:0", reviewer="AL")
    service.approve(db_session, "iculture:SHORT:0", reviewer="AL")

    row = service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY)

    assert row.title == "快結束的"


def test_upcoming_shows_are_still_featured_when_nothing_is_running(db_session, spirit):
    """
    ⚠️ 新的第一層**不能**把前置期規則吃掉。

    大多數地標大多數日子沒有任何展覽開著，這時候仍然要推得出即將開演的單日演出
    ——那正是 2026-08-18 加前置期的理由，22 筆有 18 筆是單日。
    """
    concert = _TODAY + timedelta(days=20)
    service.upsert_events(
        db_session,
        [
            _record(
                spirit.spirit_id,
                event_id="iculture:GIG:0",
                title="即將開演",
                start_date=concert,
                end_date=concert,
            )
        ],
    )
    service.approve(db_session, "iculture:GIG:0", reviewer="AL")

    row = service.featured_event(db_session, spirit_id=spirit.spirit_id, on_date=_TODAY)

    assert row is not None
    assert row.title == "即將開演"


def test_event_view_has_no_summary(db_session, spirit):
    """
    ⚠️ 簡介只餵 B9 的 prompt，不給玩家看。多帶一個沒有人顯示的欄位，
    只會讓下一個人以為它該顯示。
    """
    service.upsert_events(db_session, [_record(spirit.spirit_id)])
    row = _get(db_session, "iculture:T1:0")

    view = service.event_view(row)

    assert "summary" not in view
    assert view["title"] == "甲展"


def test_event_view_carries_the_licence_attribution(db_session, spirit):
    """
    🔒 政府資料開放授權條款第 1 版要求標示出處，**這不是可選項**。

    標示由後端帶出去，客戶端照抄——換資料源不該要我們發一版 App。
    """
    service.upsert_events(db_session, [_record(spirit.spirit_id)])

    view = service.event_view(_get(db_session, "iculture:T1:0"))

    assert "iCulture" in view["source_label"]


def test_prompt_text_includes_the_summary(db_session, spirit):
    """
    展覽名稱常常是抽象的（「浮光」「間隙」），只給名稱的話模型無從發揮，寫出來
    跟沒有輸入時的保底台詞差不多——那等於白花一次模型呼叫。
    """
    service.upsert_events(db_session, [_record(spirit.spirit_id)])

    text = service.prompt_text(_get(db_session, "iculture:T1:0"))

    assert "甲展" in text
    assert "一段簡介" in text


# ══ 資料庫：接上 S10 ════════════════════════════════════════════════════


def _today(now=None):
    return taipei_today(now or datetime.now(timezone.utc))


def test_refresh_feeds_the_event_into_the_prompt(db_session, spirit):
    """
    🔒 AC：已放行且在檔期內的活動 → B9 的 prompt 裡看得到它。

    這一條是「`official_events` 白名單欄位終於有人填」的實際驗證。
    """
    today = _today()
    service.upsert_events(
        db_session,
        [_record(spirit.spirit_id, title="限時展覽", end_date=today + timedelta(days=10))],
    )
    service.approve(db_session, "iculture:T1:0", reviewer="AL")
    brain = FakeGeminiClient(response="今天館裡人特別多。")

    refresh_from_landmark_events(db_session, brain, place_id=spirit.spirit_id)

    assert brain.call_count == 1
    assert "限時展覽" in brain.prompts[0]


def test_refresh_snapshots_the_event_into_the_cache(db_session, spirit):
    """
    🔒 活動卡是**跟著那一天的快取存下來的快照**，不是讀取時現查。

    保底路徑會回退到昨天的敘事；現查的話玩家會看到「昨天的敘事＋今天的活動卡」，
    兩段文字互相矛盾而且沒有任何錯誤訊息。
    """
    today = _today()
    service.upsert_events(
        db_session,
        [_record(spirit.spirit_id, title="限時展覽", end_date=today + timedelta(days=10))],
    )
    service.approve(db_session, "iculture:T1:0", reviewer="AL")

    row = refresh_from_landmark_events(
        db_session, FakeGeminiClient(response="敘事"), place_id=spirit.spirit_id
    )

    assert row.content["official_event"]["title"] == "限時展覽"


def test_a_revoked_event_does_not_rewrite_history(db_session, spirit):
    """
    收回放行之後，**已經生成的那天仍然自洽**——我們不回頭竄改已經發生過的一天。

    這是存快照而不是現查的第二個好處。
    """
    today = _today()
    service.upsert_events(
        db_session,
        [_record(spirit.spirit_id, title="限時展覽", end_date=today + timedelta(days=10))],
    )
    service.approve(db_session, "iculture:T1:0", reviewer="AL")
    refresh_from_landmark_events(
        db_session, FakeGeminiClient(response="敘事"), place_id=spirit.spirit_id
    )

    service.revoke(db_session, "iculture:T1:0")

    db_session.expire_all()
    cached = (
        db_session.query(models.DailyEventCache)
        .filter_by(place_id=spirit.spirit_id, event_date=today)
        .one()
    )
    assert cached.content["official_event"]["title"] == "限時展覽"


def test_no_event_still_caches_the_fallback(db_session, spirit):
    """
    大多數地標大多數日子沒有展覽。那時 B9 走人工預寫保底，而那**仍然要寫進
    快取**——不寫的話排程看起來像從沒成功過，真正的故障就被掩蓋了。
    """
    brain = FakeGeminiClient(response="不會被用到")

    row = refresh_from_landmark_events(db_session, brain, place_id=spirit.spirit_id)

    assert row.content["is_fallback"] is True
    assert brain.call_count == 0
    # 沒有活動時**不寫這個 key**，而不是寫 null——跟「這列是加欄位之前寫的」
    # 在讀取端長得一樣，兩者都由回應模型的預設值涵蓋。
    assert "official_event" not in row.content


# ══ 對外端點 ═══════════════════════════════════════════════════════════


def test_endpoint_returns_the_event_card(client, db_session, spirit):
    today = _today()
    service.upsert_events(
        db_session,
        [_record(spirit.spirit_id, title="限時展覽", end_date=today + timedelta(days=10))],
    )
    service.approve(db_session, "iculture:T1:0", reviewer="AL")
    refresh_from_landmark_events(
        db_session, FakeGeminiClient(response="敘事"), place_id=spirit.spirit_id
    )

    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event").json()

    assert body["official_event"]["title"] == "限時展覽"
    assert "iCulture" in body["official_event"]["source_label"]


def test_endpoint_returns_null_when_there_is_no_event(client, db_session, spirit):
    """
    `official_event` 為 `None` 是**常態**，不是錯誤。客戶端要把整張卡片隱藏，
    而不是畫一張空卡。
    """
    refresh_from_landmark_events(
        db_session, FakeGeminiClient(response="敘事"), place_id=spirit.spirit_id
    )

    body = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event").json()

    assert body["official_event"] is None


def test_cache_rows_written_before_this_feature_still_deserialise(client, db_session, spirit):
    """
    🔒 0022 之前寫的快取列沒有 `official_event` 這個 key。回應模型的預設值必須
    涵蓋它——不然這個功能一上線，所有既有快取都會讓端點 500。
    """
    db_session.add(
        models.DailyEventCache(
            place_id=spirit.spirit_id,
            event_date=_today(),
            content={"narrative_text": "舊格式的內容", "is_fallback": False, "sources": []},
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/spirits/{spirit.spirit_id}/daily-event")

    assert response.status_code == 200
    assert response.json()["official_event"] is None
