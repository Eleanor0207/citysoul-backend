# 分支追蹤與決策紀錄

起因是 2026-08-10（一）那次「4 人各自獨立開發、再比較結果」的比較週期，
原本只是本機的個人筆記。後來裡面累積了一些**只存在於這個檔案**的東西
（#19 的 AC 與 schema 對不上、GitHub dependency graph 漏了 #11→#41 那條
邊），而它當時是 gitignore 掉、完全沒進 git 的單一份檔案——`git clean`
或誤刪就沒了。所以改成進版控（`.gitignore` 開例外，比照 `docs/agents/`）。

⚠️ **這份會被 push，隊友看得到。** 原本是寫給自己看的口吻，內容沒有改寫，
但要記得：現在寫進來的東西是公開的。

切分支後如果 `alembic upgrade head` 噴「找不到 revision」，跑
`./scripts/reset_local_db.sh`（本機開發資料本來就不留，砍掉重來最快）。

| Issue | 分支 | 狀態 | 關鍵決定 / 備註 |
|---|---|---|---|
| #30 | `issue-30-api-contract` | 本機已 commit（2 次），未 push | enum、統一 ErrorResponse、spirits 方位欄位（migration 0006）、`contracts/openapi.json` + 匯出腳本、CI 破壞性變更閘門（`contract.yml`，PR-only，`oasdiff`）＋ 閘門自我測試 job |
| #12 | `issue-12-prompt-builder` | 本機已 commit（1 次），未 push | B2 Prompt 組裝引擎：`embeddings.py`（EmbeddingClient 抽象＋Fake，embedding 模型還沒拍板）、`historical_boundary.py`（B5 規則文字先給一個站得住的預設，非佔位假文字）、`prompt_builder.py`（System Instruction／User Turn 組裝，找不到生效人格回 None）。`tests/test_prompt_builder.py` 10 個測試全過，全套 232 passed / 1 skipped（skip 是既有的 Gemini 真實呼叫測試，跟這張票無關） |
| #32 | `issue-32-quota` | 本機已 commit（1 次），未 push | 從 `main` 分出來（不含 #12／#30），所以全套基準是 223（222+1 skip），加 10 個新測試 =233。`quota.py` 新增 `consume()`＋`QuotaExceededError`：Redis `INCRBY` 原子遞增、超過才 `DECRBY` 回滾（不是先查再寫）；key 用 Asia/Taipei 日期命名，換日自動重置。`main.py` 註冊 exception handler 轉 429，內容只含資源類型／自己的上限／重置時間。issue body 範例的資源名稱（`dialogue_turns_daily`）跟實際 seed 的名稱（`dialogue_calls_daily`）不一致，照實際 seed 的名稱做 |
| #21 | `issue-21-tts` | 本機已 commit（1 次），未 push | `tts.py`：`TTSClient`／`TTSResult`（只含 `audio_url`）／`FakeTTSClient`，跟 B1 同一種「呼叫端只依賴抽象介面」取捨。**`GoogleCloudTTSClient` 刻意沒寫**——已跟人確認：`synthesize_speech` 回傳的是原始位元組不是 URL，這個 repo 沒有任何物件儲存設定（沒有 bucket、沒有 `google-cloud-storage` 依賴），儲存策略是要人決定的基礎設施問題，不是我該瞎猜的。連帶 AC5（真實實作的語言代碼 grep）、AC7（真實呼叫手動驗證）都暫緩。`tests/test_tts_client.py` 用 AST 掃 `app/` 底下所有識別碼確認沒有殘留 `viseme`（故意不掃註解／docstring，說明「為何移除」的文字留著沒關係）。6 個測試全過 |
| #22 | `issue-22-landmark-recognition` | 本機已 commit（1 次），未 push | `landmark_recognition.py`：`recognize_landmark(db, image_bytes, spirit_id, client=None) -> bool`，多模態 Gemini 呼叫，任何失敗（找不到地標、連線錯誤、逾時、例外）一律回 False，不拋例外。順手把 `gemini.py` 裡 ADC client 建構邏輯抽成共用的 `create_genai_client()`，B13 直接重用，不重複寫一份。隱私約束（image_bytes 不落地）用兩種方式驗證：靜態字串掃描原始碼＋動態在 `tmp_path` 跑一次辨識確認沒有新增檔案。AC6（辨識失敗不阻擋任務完成）沒有整合測試——#34（任務完成端點）還沒落地，等它出現時再驗整合行為。15 個測試全過，全套 238（237+1 skip） |
| #20 | `issue-20-daily-event-content` | 本機已 commit（1 次），未 push | `daily_event_content.py`：`generate_daily_event_content(place_id, event_date, festival_name=, official_events=, reviewed_material=, gemini_client=) -> DailyEventContent`，純函式，沒有 `db` 參數。三類輸入全空時直接回退、**不呼叫 Gemini**（避免模型在沒有審核素材時自由發揮）；輸入齊全時呼叫 B1，B1 自己保證不拋例外／不回空字串，B9 不需要再包一層 try/except。三類輸入實際上哪裡來（節日判定、官方活動）刻意留給 #26，同 #12 對 daily_narrative 的處理方式。AC2／AC5 用 AST 掃識別碼驗證沒有天氣／社群 API、沒有排程／快取／DB 相關名稱——用 AST 而不是純文字 grep，因為模組自己的 docstring 就會提到這些被禁止的詞。9 個測試全過，全套 232（231+1 skip） |
| #26 | `issue-26-daily-event-api`（**疊在 `issue-20-daily-event-content` 上**，不是分自 main） | 本機已 commit（1 次），未 push | 這張票真的需要 #20 的程式碼（`trigger_daily_event_generation` 直接呼叫 `generate_daily_event_content`），不是概念上依賴而已，所以照規則疊分支，不分自 main。新增 `daily_event_cache` 表（migration 0006，PK `(place_id, event_date)`，FK 到 spirits）、`app/modules/body/daily_event.py`（`trigger_daily_event_generation`／`get_daily_event_for_spirit`）、`GET /api/v1/spirits/{placeId}/daily-event`（不需要 token，地標不存在才 404，沒有今天內容一律 200＋保底鏈路：今天→昨天→人工預寫）。重複觸發用「先寫、撞到 PK 衝突就回讀」處理，同 #16 的教訓。**編號衝突已知且記錄**：這支的 migration 0006 跟 `issue-30-api-contract` 的 0006（spirits 方位）都是從 main 的 0005 分出來的，兩邊合併時一定要有一支改成 0007。11 個測試全過，全套 243（242+1 skip） |

| #34 | `issue-34-quest-complete` | 本機已 commit（1 次），未 push | 分自 main（不依賴任何未合併的票）。`POST /api/v1/quests/{questId}/complete`：Session＋Encounter token，確定性判定（**完全不碰腦袋**，有測試把兩個 Gemini client 的 `generate` 都換成一叫就爆的 fake 來證明）、寫 `quest_progress`、共鳴 +20。去重完全靠 S5 既有的 UNIQUE 約束，重複提交回 200 不是 500。Sense token 不論放自己的 header 還是塞進 `X-Encounter-Token` 都過不了（金鑰與 purpose 都不同），兩種都測了。`unlock_story`／`quest_wrapper_text` 一律 null（連跨門檻時也是），敘事屬 #43。新增 `spirit_id_for_quest()`，格式不符回 None → 404，不猜——猜錯會拿別人的靈魂去比對 encounter token。22 個測試全過，全套 245（244+1 skip） |

| #25 | `issue-25-unlock-story` | 本機已 commit（1 次），未 push | 分自 main。`unlock_story.py`：`generate_unlock_story(player_id, spirit_id, stage)` 依階段查 `_STAGE_BRIEFS` 組不同 prompt（初識→熟識→老朋友），三階段的回退台詞也各自不同。**兩層回退**：`try/except` 兜底＋偵測 B1 回傳自己的 `FALLBACK_REPLY`（那句是寫給「我不知道怎麼回答」的，當解鎖獎勵讀起來很突兀）。另外提供 `generate_unlock_stories()` 處理一次跨多門檻——#16 讓 `newly_unlocked_stages` 回 list 就是為了這件事，有 plural 版本 #43 就不會寫成 `stages[-1]` 而讓中間那段靜默消失。沒有 `db` 參數（v2.1 §6.4 呼叫順序），`player_id` 收在簽章但不進 prompt。**跑過 AC 要求的 mutation 驗證**：把三個 stage 改成同一個 prompt → 4 個測試變紅。16 個測試全過，全套 239（238+1 skip） |

## 2026-08-08 整併（consolidation）

8 個分支全部併回本機 `main`（**還沒 push**）。整併前打了 `pre-consolidation-backup`
tag，要退回就 `git reset --hard pre-consolidation-backup`。

整併結果：**330 passed, 1 skipped**（skip 是既有的 Gemini 真實呼叫測試）。
7 支 migration 從空資料庫建得起來、`downgrade base` → `upgrade head` 來回
都過、`alembic check` 沒有漂移。

整併過程中真的抓到的問題（這就是整併的價值）：

1. **migration 撞號**：#26 的 `0006_daily_event_cache` 與 #30 的
   `0006_spirit_orientation` 都宣告 `down_revision = "0005"`。把後者改成
   0007 接在前者後面。兩者互不相干，順序對調也沒差。
2. **契約快照過期**：#26／#34 各加了一支端點，但 `contracts/openapi.json`
   是在那之前產生的，所以 #30 的漂移偵測測試在整併後直接變紅——**那正是它
   存在的目的**。重新產生快照，並替兩支新端點宣告錯誤碼。
3. **順手補的守門測試**：`_EXPECTED_DECLARED_ERROR_CODES` 是一張手寫的表，
   逐支比對測試只走表裡有的項目——新端點沒加進表就會被**靜默跳過**。加了
   一條測試確認那張表涵蓋所有 `/api/v1` 端點。
4. `schemas.py`／`router.py` 有三方 append 衝突（#26／#34／#30 都往檔尾加
   東西），手動解掉，三邊的內容都保留。

**沒有**因此驗證到的事（不要誤以為整併＝全部 AC 都過）：GitHub 上的 AC
checkbox 沒有人勾過；#21 的真實 `GoogleCloudTTSClient`、#30 的 CI 契約閘門
（要真的開 PR 才會跑）、#19 的 schema 缺口、#41／#48 的人工判斷項目，全都
仍然沒做。

### 整併後補做：#22 AC6（已完成）

整併讓 #22 與 #34 進到同一棵樹，AC6「辨識失敗不阻擋任務完成」終於測得到
真的整合行為（做 #22 當下 `POST /quests/{questId}/complete` 還不存在，
只能先驗「函式自己不拋例外」）。補了 3 個測試：辨識失敗、辨識成功（完成
結果**完全相同**，徽章是額外獎勵不是門檻）、辨識服務本身壞掉。跑過 mutation
驗證：把完成流程改成「辨識沒過就 403」→ 剛好這 3 個變紅。

順帶修掉 `spirit` fixture 的 teardown——完成任務會寫 resonance／
resonance_events，兩張表都外鍵指向 spirits，不先清掉的話 fixture 收尾會
撞 FK 而不是安靜刪掉。

全套現在是 **333 passed, 1 skipped**。

### 整併後補做：#43 任務完成串接解鎖敘事（已完成）

直接做在 `main` 上（#34／#25 都已經在裡面了，不用再疊分支）。新增
`app/modules/brain/quest_wrapper.py`、`tests/test_quest_narrative.py`。

幾個值得記的決定：

- **`generate_quest_wrapper` 沒有放進 `prompt_builder.py`**：票上把它歸類
  成 B2，但 #12 定義那支模組是「對話用的 prompt 組裝器」，任務包裝是另一
  種生成任務，塞進去會讓那支同時有兩個不相干的責任。
- **改了 #34 的兩處測試**（都是因為它們釘住的是被 #43 取代掉的中間狀態）：
  「完成流程不呼叫腦袋」原本打整支端點，改成打 `complete_quest`——
  CONTEXT.md 約束的是「判定」不是「事後包裝」；「敘事欄位永遠是 null」
  那兩條直接被真實行為的測試取代，原地留註解說明去哪了。
- **`get_gemini_client` 做成 FastAPI dependency，conftest 預設注入 fake**。
  少了這個，任何打生成端點的測試都會真的去試 ADC：沒憑證的機器上一個檔案
  從 0.4 秒變 42 秒，有憑證的機器上會真的花錢。
- **多門檻的回應形狀有個已知限制**：每個新解鎖 stage 都會各自生成一段
  （不讓中間那段靜默消失），但 §7.5 的 `unlock_story` 只裝得下一個，所以
  回最高那階。MVP 的 +20 跨不過兩個門檻（10 到 40 差 30）所以現在碰不到，
  要支援得先改 §7.5。
- 跑過 AC 要求的兩個 mutation：只取最後一個 stage → 多門檻那條紅；把生成
  搬到 DB 寫入之前 → 順序那條紅。**第一次跑 mutation 時多門檻那條沒紅**
  ——因為它原本直接呼叫服務層，繞過了端點；改成走完整條端點才真的守得到。

全套 **339 passed, 1 skipped**。

## 其他觀察

- #11（B4 安全邊界）body 寫的 blocked by 是 #8、#41，但 GitHub 的 native
  dependency graph 只記了 #8——**native graph 漏了 #41 這條邊**。我嘗試用
  `gh api ... /dependencies/blocked_by` 補上，但目前這個帳號對這個 repo的
  labels／dependencies 寫入都是 404（讀取正常，寫入沒有權限）。之後有權限
  記得補：`#11` 應該 blocked by `#41`（ready-for-human，內容治理，還沒完成）。
- 本機這個帳號的 `gh` token 對 city-soul-taipei/citysoul-backend 只有讀權限，
  沒有 label/dependency 的寫權限（label create 跟 dependency POST 都 404）。
- **#19（B5 史實邊界規則）的 AC 對不上目前的 schema。** AC3／AC4 要求把通則
  文字跟人格卡的 `factual_boundary` 欄位疊加，但 `factual_boundary` 是**舊的**
  單張 JSONB `persona_cards`（#2 時期）才有的欄位——0005 把它換成三層具名
  欄位（`archetype`／`speech_style`／`taboos`／`not_this_character`／
  `imagination_license`／`quest_themes`／`tone_override`），現在的
  `brain.character_personas` 完全沒有 `factual_boundary` 這一欄。#19 的
  spec 是在 schema 換掉之前寫的，沒有跟著更新。
  開始做 #19 之前要先決定：(a) 新增一支 migration 補一個
  `factual_boundary` 欄位，或 (b) 改成跟某個既有欄位（`not_this_character`？
  `taboos`？都不是完全對得上的語意）疊加，或 (c) 回頭跟開 票的人確認這條 AC
  該怎麼改寫。**先跳過 #19，去做 #32。**
- 2026-08-08 檢查 GitHub：main 沒有新 commit（本機反而領先 1 個 commit——
  `reset_local_db.sh`，還沒 push）。新增 #47（已關）、#48（ready-for-human，
  被 #41 卡住）、#49（Phase 5 全串通驗收，被 #30＋幾乎所有端點票卡住）。
  三張都不影響目前的 wave 排序。沒有人在 GitHub 上 claim 任何 issue。

---

# 2026-08-08：origin/main 大幅分歧，本機 9 張票被重複實作

## 怎麼發現的

新增 `scripts/preflight.sh`（fetch ＋ 雙向 commit 比對 ＋ open/closed issue
清單）。第一次跑就抓到：短短幾分鐘內 `origin/main` 從 `def683c` 又前進到
`47d2243`。**在此之前完全沒有這道檢查，這就是 #30／#32 被做兩次的原因。**

⚠️ `preflight.sh` **只看得到 push 上去的東西**。同事在本機做到一半還沒 push
的分支看不到——4 人不協調的先天限制，檢查過了不代表沒人在做同一張票。

## 分歧狀況

共同祖先 `70f10bf`。本機領先 23 個 commit、origin 領先 17 個，**不是
fast-forward**。

| | 本機 | origin |
|---|---|---|
| 測試函式數（靜態數 `def test_`） | 306 | 397 |
| 實際跑過 | ✅ 339 passed / 1 skipped | ❌ 沒跑過（只讀原始碼比對） |

**被重複實作的 9 張**：#12、#20、#21、#22、#25、#30、#32、#34、#43
（origin 那邊都已經 close）。

**只有本機有**：**#26**（S10 當日情境排程／快取／對外 API，含 migration
與端點）。origin 上 #26 仍是 open。

**只有 origin 有**：#11（B4 安全邊界）、#19（B5 史實邊界）、#41（龍山寺
內容治理草稿）、#42（dialogue 端到端）、#46（PostGIS），以及把 #41 拆出
#50／#51。

## 比較結果（讀原始碼，沒跑他們的測試）

### 他們明顯比較好

- **#21 TTS：拆成兩個接縫，比我的設計好。** 我卡在「TTS 回傳位元組、契約
  要 URL」，把它當成「要人決定的基礎設施問題」上報，然後**整個真實 client
  都沒做**。他們拆成 `TTSClient`（合成）＋ `AudioStorage`（存放），存放是
  注入的，所以**不需要先決定 bucket 就能把真實 client 寫完**。242 行 vs 我
  68 行，差的是真實能力不是廢話。這是他們最明確的一勝。
- **#19：他們做完了，我只留 stub。** 把 `factual_boundary` 映射到既有的
  `imagination_license`，映射只集中在 `factual_boundary_for_persona()` 一個
  函式（欄位再搬家只要改那一處），並在模組裡寫明規格漂移。那正是我當時提的
  選項 (b)——當時決定跳過 #19 先做 #32，所以這一半是流程結果、不純粹是品質差距。

### 本機比較好

**#30 契約閘門，兩個具體點：**

1. 他們用 `oasdiff/oasdiff-action/breaking@main`。那個 action 自己的文件寫著
   `@main` 是「跑未發布的 tip，只適合早期試用，**不適合正式使用**」。本機
   釘的是 `@v0.1.12`。
2. 本機有**閘門自我測試**（`tests/fixtures/contract_gate/` 兩組 fixture，
   證明閘門真的擋得下破壞性變更、也真的放行純新增）。他們沒有，而且他們自己
   的註解承認「這個 job 還沒有在真實 PR 上跑過，第一次開 PR 時請確認它不會噴」。
   **沒有人驗證過的閘門，正是那個自我測試存在要防的失效模式。**

### 平手但值得知道

他們注意到一件我漏掉的事：**Pydantic 的 docstring 會進
`contracts/openapi.json`，等於送到客戶端**，所以 schema 的 docstring 該寫短。
洞察是對的，但他們自己沒有做得比我好——他們契約裡的 description 總字數
2701，本機 2640。

## 給 Monday 的建議

**#21 的 `AudioStorage` 接縫**與**#30 的版本釘選＋閘門自我測試**是可以
**各自單獨採用**的。合併結果可以取兩邊各自較好的一半，不必整邊挑一邊。

⚠️ **合併時的既知障礙**：兩邊都有 revision `0006` 且都宣告
`down_revision = "0005"`（他們是 `0006_postgis_extension`，本機是
`0006_daily_event_cache`），兩邊也都有內容不同的 `0007_spirit_orientation`。
直接合會讓 Alembic 撞到重複 revision id，必須先把其中一條鏈重新編號。

---

# 2026-08-10：與 dev 合併（本次）

## 先更正上面一段已經過期的判斷

⚠️ 「**只有本機有 #26**」那句話，寫的當下是對的，現在不是了。`dev` 上有
`24f313a Add daily event scheduling, cache and public API (S10, #26)` 與
`0008_daily_event_cache`。**兩邊十張票全部重疊，本機沒有任何一張是獨有的。**
上面 2026-08-08 那節的判斷請以這一節為準。

## 決定：整棵樹取 dev，本機的貢獻另外疊上去

用 `git merge -s ours main` 併進 `yian/merge-into-dev`（分自 `origin/dev`）。
`-s ours` 會**記錄**這次合併（`main` 從此是祖先，來歷留得住），但內容整棵
取 dev。

**理由不是 dev 的程式碼比較長，是 import 圖：**

| 模組 | 本機的呼叫者 | dev 的呼叫者 |
|---|---|---|
| `prompt_builder` | **沒有** | `router.py`（對話端點 #42） |
| `landmark_recognition` | **沒有** | `router.py`（照片端點 #44） |
| `tts` | **沒有** | `router.py` + `schemas.py` |
| `unlock_story` | `router.py` | `router.py` |
| `historical_boundary` | 1 支 | 4 支 |

本機那三支是「寫好、測好、從來沒接上端點」。dev 那三支是承重的。挑本機的
版本＝要把 dev 能跑的 `router.py` 改成配合一個沒有呼叫者的 API，純虧。

順帶解決了 migration 撞號：整棵取 dev 之後鏈是乾淨的 0001→0011，本機的
`0006_daily_event_cache` 直接不存在（dev 的 `0008` 已經建了同一張表
`daily_event_cache`——**兩邊檔名不同、revision id 不同，git 不會報衝突，
但兩支都留下來的話 `alembic upgrade head` 會炸在 duplicate table**）。

## 沒有跟著取 dev 的東西（本次另外疊上去）

- `.gitignore`：兩邊都要，手動併。dev 需要 `!docs/content-governance/`
  （它真的有追蹤那個檔）跟 `.agents/`；本機需要 `.claude/settings.local.json`
  跟 `!docs/dev-notes/`。
- `scripts/preflight.sh`、`scripts/reset_local_db.sh`、`docs/dev-notes/`：dev 沒有。
- 契約閘門的版本釘選與閘門自我測試（見下一節）。

## 主動放棄的東西

- **`app/modules/brain/embeddings.py` 不帶過去。** 上面 2026-08-08 那節沒有
  提到它，但比對時一度以為它補得上 dev 自己註記的缺口（dev 的 `prompt_builder`
  寫著「目前 repo 裡還沒有產生 embedding 的模組」）。**實際讀過內容後推翻**：
  那支只有 `EmbeddingClient`（ABC）跟 `FakeEmbeddingClient`，**沒有真實實作**，
  所以補不上那個缺口；而且 dev 的 `build_prompt` 收的是 `query_embedding:
  Sequence[float] | None`（原始向量），不是 client 物件，這層抽象跟 dev 的
  架構也對不上。帶過去就會變成又一支沒有呼叫者的死程式碼——那正是本機這次
  被判輸的理由，不該自己再犯一次。等 B6 真的要做 embedding 生成時再談。
- `quest_wrapper.py`、`daily_event_content.py`、`body/daily_event.py`、
  `0006_daily_event_cache.py`、`export_openapi.py`、`test_quest_narrative.py`：
  dev 都有對應物（檔名不同），全部由 dev 版本取代。
