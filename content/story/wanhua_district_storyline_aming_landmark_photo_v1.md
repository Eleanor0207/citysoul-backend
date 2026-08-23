# 萬華年代簿・回家的畫 — 完整互動腳本 v1（景觀拍攝辨識）

> **狀態：方案 A／MVP 敘事基線／草稿。**本文件是完整、獨立的景觀拍攝辨識版本；它包含所有劇情、任務、逐句對話、資料規格與治理規則，不依賴增補或修補文件。
>
> **地標範圍**：艋舺龍山寺、西門紅樓、剝皮寮歷史街區。
>
> **寫作依據**：SDD v2.2 §20.4。史實資訊以 `../landmark/` 研究檔為正本；信件、畫作與「阿明」均為本作虛構，絕不可表述為真實館藏、史料或可考證人物。

---

## 0. 一句話大綱

一封沒有署名的信，請玩家替它「回去看一眼」；一幅名為〈回家的畫〉的畫，讓三個地標逐步顯出同一段萬華記憶。玩家最後明白：畫中人不必被找出姓名，因為她承載的是無數居民對街與家的記得。

**城市導覽承諾**：每一站都先讓玩家看見該地標的真實空間、年代與用途，再提供一段虛構敘事作為理解入口；故事不是要玩家離開現場去解謎，而是讓玩家更願意停下來看萬華。

### 0.1 區域路線與慢遊原則〔定案〕

- **每個行政區只有一個主線起始點。**萬華主線的第一站是龍山寺；**入口文件（信）在玩家首次進入萬華區圍欄時發放**，不必先走到龍山寺（見 §2.3）。
- **主線不得跨行政區強制推進。**萬華的所有必要 Beat、任務與結局都只使用萬華區內地標；其他行政區只能是可選探索、彩蛋或未來合作內容，不能作為萬華的前置。
- **可跨日、無期限、可中斷。**玩家可分數天完成三站；未完成不降好感、不失去道具、不重置已完成 Beat。
- **導向是邀請，不是導航指令。**年代簿提供下一站名稱、故事理由與可選的柔和方向提示；不顯示限時、最短路線、強制箭頭或「必須立刻前往」。
- **資訊卡是可略過的導覽補充。**每站最多一張，目標 60–90 字，故事對話後才顯示；不可用閱讀資訊卡作為任務完成條件。

---

## 1. 世界觀與內容邊界

| 項目       | 本稿採用設定                                                               |
| ---------- | -------------------------------------------------------------------------- |
| 玩家身分   | 當代來訪者與聆聽者；不是阿明後代、轉世、附身者或拯救者。                   |
| 阿明       | 署名遺失的虛構書信作者，是許多無名居民的情感載體；不具可查傳記。           |
| 阿明的呈現 | **永不現身、永不發聲、沒有剪影。**僅留下信與畫。                           |
| 城市靈魂   | 地標的擬人化集體意識；不扮演真人、不替歷史人物說話。                       |
| 信與畫     | 系統敘事物件，不是現實中的地標館藏。信可讀、脈絡破損；畫有人物與街景交疊。 |
| 歷史資訊卡 | 每張分為「史實可考」與「本作敘事」，經人工覆核才可上線。                   |

### 1.1 絕對禁止

- 不給阿明出生年、死因、家人姓名、職業或其他可被誤認為史實的個人資料。
- 不讓龍山寺靈魂轉述神明、廟方或儀式的話；不談靈驗、籤詩、吉凶、教義。
- 不讓紅樓自稱見過阿明；紅樓是 1908 年才出現的後來保管者。
- 不讓剝皮寮替特定住戶補完處境、姓名或去向，也不對地名由來下定論。
- 不把任務做成拍攝神像、參拜者、路人、店家或即時商業標示。

---

## 2. 敘事物件與開場

| 物件 ID                    | 玩家顯示名稱 | 用途                     | 取得時機                 |
| -------------------------- | ------------ | ------------------------ | ------------------------ |
| `item_wanhua_letter`     | 留給街的信   | 主線鉤子；可在年代簿重讀 | `beat_prologue_letter` |
| `item_homeward_painting` | 回家的畫     | 連結三地標的觀察物件     | `beat_longshan_clue`   |

### 2.1 信件全文（可讀部分）

> 我畫了她很多次，卻想不起她的臉。
>
> 若你還記得那條街，請替我回去看一眼。
>
> ——［署名已被水漬暈開］

### 2.2 畫作描述

一名側身人物站在畫面中央，臉被水痕模糊。她的衣褶、手勢和身後線條乍看像人像畫，細看卻像屋簷、門洞、騎樓與街道轉角。畫名不是作者留下的題字，而是玩家在年代簿中為其使用的暫稱：**〈回家的畫〉**。

### 2.3 序章互動

**觸發條件**：玩家**首次進入萬華區行政圍欄**（`brain.districts.boundary` 的多邊形內含判定成立），且未完成 `beat_prologue_letter`。不需要接近任何地標。

> **信屬於整個區，不屬於某一個地標。** 它的敘事定位是「留給這條街的信」，所以踏進萬華就該拿到；龍山寺是第一個**可以回應這封信**的靈魂，不是發信的人。
>
> **範圍〔定案：用完整官方行政區界〕**：萬華區約 8.85 km²，**西門町也在萬華區內**，捷運板南線穿越該區。因此「去西門逛街」或「開著 App 搭捷運經過萬華」都會發信——**這是要的行為**。不縮小多邊形。
>
> **玩家先到紅樓或剝皮寮怎麼辦**：照樣拿得到信（同一個區），但主線標記只亮龍山寺——`route_policy` 規定萬華固定由龍山寺開場。那兩個靈魂仍可正常感應、對話、收藏。
>
> **重複觸發**：玩家在區界附近來回或每次開 App 都會重新判定。去重靠 `player_inventory` 的 `uq_inventory_player_item` 唯一索引，不靠應用層「先查再寫」（併發下會漏）。
>
> **觸發時機**：除了 §5.5 的前景輪詢，**進入 MapScene 時必須判定一次**；否則住在萬華、但很少在移動中開 App 的玩家可能一直拿不到入口。

**系統呈現〔定案〕**：**不自動全螢幕、不打斷玩家。** 圍欄觸發的時機完全不受控——玩家可能在捷運上、在西門町人擠人、在騎車等紅燈。因此發放與閱讀分開：

| 階段                          | 發生什麼                                                                                                                                              |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| **① 發放（自動）**     | HUD 任務 icon 出現**驚嘆號**；任務列表主線段新增一項 **「留給街的信」**，同樣帶驚嘆號。**龍山寺主線標記在這一刻就亮**，不等玩家讀信 |
| **② 開啟（玩家主動）** | 點開該項目 → 全螢幕：信件淡入 → 畫作局部顯現 → 信與畫上浮現三個可看的位置                                                                              |
| **③ 觀看**             | 玩家點畫面上任一位置 → 年代簿敘述 → **看過一處即可收束**（不強制看完三處）→ 項目轉為「進行中」，下一站顯示龍山寺                                                                        |

**任務名稱**：**「留給街的信」**，與 `item_wanhua_letter` 的 `display_name` 一致。

> ⚠️ **不得叫「阿明的信」。** 序章明寫「信沒有地址，也沒有可辨識的署名」，而阿明這個名字是靠三個靈魂的轉述才逐步浮現的。任務列表若一開始就寫出名字，玩家會比信本身更早知道寫信的人是誰，反轉的地基就沒了。

**視覺語言**：**信與畫上的三個可看位置，不是對話氣泡、也不是問句選單。** 這個節點沒有靈魂在場（`character_id: null`，speaker 是「年代簿」）；氣泡選單會讓玩家以為有人正在跟他說話，然後發現沒有。

> ⚠️ **人不會跟一封信對話。〔定案〕** 原本三個選項寫成疑問句（她是誰？為何留下？街在哪裡？）而信給出回覆，等於**信在應答**——破綻在這裡，不在「有選項」。
>
> 但人一定會**盯著一封信看**：看畫裡的人、翻過來看摺痕、湊近看角落那幾筆。所以動詞從「問」換成「看」：玩家點的是畫面上的位置，**說話的仍然是年代簿**，沒有任何人在應答。
>
> 三段敘述本來就不是「回答」，而是「你注意到了什麼」——錯位一直只在提示詞，不在回應，也不在機制。
>
> 附帶好處：**「什麼最先抓住你的眼睛」比「你問了什麼」更能刻畫一個人**，而且是演出來的，不是講出來的。

位置要**同時是畫面上的熱區與一個可讀的標籤**，不能只有熱區——玩家可能站在日照下的路邊單手操作，純靠圖上找點擊點在那個情境下不可行（SDD §5.8 對戶外可讀性的要求）。

**三處都可以看，看過一處就能往下走〔定案〕**：

- **不強制看完三處。** 圍欄觸發時機不受控，玩家可能站在西門町路邊，不該要求他點完三個才放行
- 已看過的位置**保持可重看**，不消失、不變灰到看不見
- `story_focus` 由**第一個看的位置**寫入，之後再看**不覆蓋**

> 這比三選一乾淨：玩家不會有「我是不是錯過了什麼」的假焦慮（他三處都看得到），而「什麼最先抓住你的眼睛」是誠實的訊號。
>
> 資料形狀因此是：`story_focus` 是**可重讀的狀態，不是消耗掉的選擇**。年代簿裡重讀這封信時三處都在，第一個看的那處有標記。

**任務列表結構〔預設，可由 Lead 推翻〕**：主線與每日任務**同一個列表、分兩段**，主線段永遠置頂。一條 arc 只有三站不會太長，而玩家在現場最常做的是「看今天要幹嘛」，多一層頁籤是每次都要付的成本。

觀看只記錄理解角度，主線仍從龍山寺開始。

| 可看的位置           | 旗標（**僅第一個**寫入）  | 年代簿的敘述                                                       |
| -------------------- | ------------------------- | ------------------------------------------------------------------ |
| **她的側影**         | `story_focus = person`  | 水痕帶走了臉，卻留著她微微側身的樣子——像是正要轉過去，應誰一聲。 |
| **信紙的摺痕**       | `story_focus = history` | 沒有地址，也沒有署名。但摺痕深得發白，像被反覆摺起、又反覆打開。   |
| **畫面一角**         | `story_focus = home`    | 畫面一角的線條像艋舺的舊街屋。筆觸有些笨拙，卻一筆一筆描得很慢。   |

> **為什麼三句都要留下一點可以握住的東西**：這是玩家與整個遊戲的第一次敘事接觸。三個回應若都是「查不到」（臉沒了、沒署名、只有線條），第一印象是冷的。神祕感靠的是「有東西但看不清」，不是「什麼都沒有」——所以三句各自保留原本的「無法辨識」這個事實，但都加上一個具體、可觸摸的痕跡。給的是溫度，不是資訊。
>
> **三句的埋線，改寫時不得抽掉**：
>
> - 「微微側身、正要轉過去」——反轉時剝皮寮靈魂認出的正是這個姿勢。**不可寫成正面、不可讓臉有任何可辨識特徵**（畫中人是投影，不是某個具體的人）
> - 「摺痕深得發白」——這封信被人帶在身上很久。它不解釋任何事，但它讓「有人放不下」這件事在第一分鐘就成立
> - 「筆觸笨拙卻描得很慢」——呼應阿明用土炭塗鴉、畫工不好的設定，剝皮寮線索裡會被靈魂溫柔地調侃。此處**只給觸感，不得出現任何名字或身分線索**

**收束**：

> 年代簿在龍山寺頁停下。那裡或許記得，什麼會被留下。

**寫入**：完成 `beat_prologue_letter`，授予 `item_wanhua_letter`，顯示龍山寺主線標記與「可於任何日子繼續」提示。

---

## 3. 劇情節點與可判定觸發

> 劇情推進的真相來源是 `players_story_progress`。所有條件只能依道具、任務完成與既有 beat 判定；不得以「玩家提到某個詞」作為必要條件。

| Beat ID                  | 地標／角色 | 觸發條件                                         | 玩家獲得             | 後續解鎖       |
| ------------------------ | ---------- | ------------------------------------------------ | -------------------- | -------------- |
| `beat_prologue_letter` | 萬華區圍欄 | 首次進入區域                                     | 留給街的信、理解角度 | 龍山寺召喚     |
| `beat_longshan_gate`   | 龍山寺     | 首次對話，且已有信                               | 龍山寺觀察任務       | 龍山寺線索條件 |
| `beat_longshan_clue`   | 龍山寺     | `q_longshan_repair_trace` 完成                 | 回家的畫             | 紅樓主線標記   |
| `beat_redhouse_gate`   | 紅樓       | 首次對話，且完成龍山寺線索                       | 紅樓觀察任務         | 紅樓線索條件   |
| `beat_redhouse_clue`   | 紅樓       | `q_redhouse_two_forms` 完成                    | 畫背面的殘句         | 剝皮寮主線標記 |
| `beat_bopiliao_gate`   | 剝皮寮     | 首次對話，且完成紅樓線索                         | 剝皮寮觀察任務       | 剝皮寮揭露條件 |
| `beat_bopiliao_reveal` | 剝皮寮     | `q_bopiliao_street_lines` 完成，且持有回家的畫 | 對畫的理解           | 結尾選擇       |
| `beat_finale`          | 年代簿     | 完成剝皮寮揭露後，開啟年代簿                     | 收藏標記、重訪內容   | 萬華餘白       |

---

## 4. 第一章：龍山寺〈被留下的東西〉

### 4.1 初次對話

**觸發條件**：持有 `item_wanhua_letter`，首次與 `longshan_watcher` 對話。

**龍山寺**：

> 你手上的信，不像是寄給誰的。
>
> 倒像有人走得太急，只來得及把一句話留在原地。

**玩家選項**：

1. 「你見過這封信嗎？」
2. 「我想知道畫裡的人是誰。」
3. 「我想先看看這裡。」

**共同回應**：

> 先別急著替它取名字。你看看這裡被修過的地方；有些事留下來，不是因為從沒壞過。

### 4.2 地標任務：重修的痕跡

| 欄位     | 規格                                                                                                                                                           |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Quest ID | `q_longshan_repair_trace`                                                                                                                                    |
| 前置     | `beat_longshan_gate`                                                                                                                                         |
| 玩家行動 | 依實地覆核後的公共觀察點，開啟兩張「修復／工法」導覽卡，選擇自己注意到的差異。                                                                                 |
| 判定     | 到場＋開啟兩個核可觀察點＋完成一次選擇。**景觀拍攝（`ls_public_roofline_photo`）為可選**，拍與不拍都能交件（§13）。不得要求玩家判讀教義或拍攝任何人。 |
| 中斷     | 保留已開啟的觀察點；下次到場續接。                                                                                                                             |
| 需實勘   | 所有觀察點與文字必須由歷史／場域審核者確認。                                                                                                                   |

### 4.3 線索對話

**觸發條件**：`q_longshan_repair_trace` 已完成，首次再次對話。

**龍山寺**：

> 你剛才看的那些痕跡，不是同一年留下的。人也是這樣，走過幾次變故，身上總會有些地方和從前不同。
>
> 這幅畫也是。它曾被放在這裡一段時間；不是什麼寶物，只是有人捨不得丟。
>
> 我一直以為畫的是一個人。後來才發現，畫裡的背景比那個人更固執。

**玩家選項與旗標回應**：

| `story_focus` | 龍山寺補句                                           |
| --------------- | ---------------------------------------------------- |
| `person`      | 你想找她的臉。但畫的人，似乎更怕忘記她站在哪裡。     |
| `history`     | 年月未必替人留下名字，倒常把一處牆、一段路留得很久。 |
| `home`        | 有人回不去的，不是一間房子，是認得路時的那種安心。   |

**交付**：授予 `item_homeward_painting`。

**章末指引**：

> 後來，畫被交到一棟很會換身分的樓裡。它比這張畫晚來很久，卻很懂得替故事留位置。

**資訊卡**：

- 【史實可考】龍山寺與艋舺街區曾多次歷經災害、修復與重建；最終文字依龍山寺研究檔與人工覆核。
- 【本作敘事】信與畫在此停留的情節為虛構，不是館藏主張。

---

## 5. 第二章：西門紅樓〈後來的保管者〉

### 5.1 初次對話

**觸發條件**：完成 `beat_longshan_clue`，首次與 `red_house_collector` 對話（一次性；不追蹤獨立 beat，見附錄 B）。

**紅樓**：

> 欸，這張畫你從哪裡翻出來的？先說好，我 1908 年才出現。
>
> 留下它的人，我沒見過。我只是後來剛好接住它——很晚，晚到連它原本想說什麼都快褪色了。

**玩家選項**：

1. 「你為什麼留下它？」
2. 「你看得出畫的是哪裡嗎？」
3. 「這裡以前是什麼樣子？」

**共同回應**：

> 一棟樓換過幾次用途，才知道東西不一定要待在原處，才能被好好保存。先陪我看看兩種形狀怎麼住在一起。

### 5.2 地標任務：兩種形狀

| 欄位     | 規格                                                                                                                           |
| -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Quest ID | `q_redhouse_two_forms`                                                                                                       |
| 前置     | `beat_longshan_clue`（透過 §5.1 初次對話門檻已確保，不需要獨立的 gate beat；見附錄 B）                                       |
| 玩家行動 | 以核可導覽覆疊，找出八角堂與十字樓相接的位置，完成兩個公共空間觀察點。                                                         |
| 判定     | 到場＋兩個導覽點完成。**景觀拍攝（`rh_public_facade_photo`）為可選**（§13）。不可依賴週末市集、店家、活動或現場人潮。 |
| 回饋     | 「一棟樓不用選一種樣子；接得起來，就能繼續用。」                                                                               |
| 需實勘   | 覆疊指向與可視性由現場確認後定稿。                                                                                             |

### 5.3 線索對話

**觸發條件**：`q_redhouse_two_forms` 已完成，首次再次對話。

**紅樓**：

> 你看，接縫不是破綻。有時候，它只是告訴你這裡曾經換過一種活法。
>
> 畫背面也有一條接縫。重新裱過時留下的紙邊，還看得到幾個字。

**畫背殘句**：

> ……不是她。
>
> 是我回不去的＿＿。

**紅樓**：

> 這下有意思了。你再看那個人的衣角，像不像一排屋簷？我猜這幅畫真正想回去的地方，不在我這裡。

**章末指引**：

> 去剝皮寮吧。別問我是不是答案；我最多只是故事中途的一個收納櫃。

**資訊卡**：

- 【史實可考】西門紅樓建於 1908 年，曾有市場、戲院等不同使用歷程；最終文字依紅樓研究檔與人工覆核。
- 【本作敘事】紅樓保管此畫為虛構。它明確不是阿明時代的見證者。

---

## 6. 第三章：剝皮寮〈畫裡的街〉

### 6.1 初次對話

**觸發條件**：完成 `beat_redhouse_clue`，首次與 `bopiliao_keeper` 對話（一次性；不追蹤獨立 beat，見附錄 B）。

**剝皮寮**：

> 你拿著什麼？……畫？
>
> 我好像看過這些線。別站在這裡猜，走近一點看。

**玩家選項**：

1. 「畫裡的人是不是你？」
2. 「信說他想回去。」
3. 「我想知道這條街記得什麼。」

**共同回應**：

> 人記得的事，常常不是一整段。先看看這些屋子怎麼把不同時候留在同一面牆上。

### 6.2 地標任務：街的線條

| 欄位     | 規格                                                                                                                         |
| -------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Quest ID | `q_bopiliao_street_lines`                                                                                                  |
| 前置     | `beat_redhouse_clue`（透過 §6.1 初次對話門檻已確保）且持有 `item_homeward_painting`                                        |
| 玩家行動 | 在核可的公共視角，將畫中門洞、屋簷、騎樓線條與導覽覆疊對照，完成三個觀察點。                                                 |
| 判定     | 到場＋三個觀察點完成。**景觀拍攝（`bp_street_facade_photo`）為可選**（§13）。不可要求拍攝塗鴉、特定住戶、招牌或人。 |
| 回饋     | 「街不是畫在地圖上的線。有人走，才算一條街。」                                                                               |
| 需實勘   | 觀察點需確認可長期存在；若環境改變，改用核可示意圖。                                                                         |

### 6.3 揭露對話

**觸發條件**：`q_bopiliao_street_lines` 已完成，首次再次對話。

**剝皮寮**：

> 你看那張畫時，先看到一個人。
>
> 可是你站在這裡，會不會覺得……那個人像一條街？

> 門洞像肩膀，屋簷像衣角，騎樓一格一格的，像有人一直往前走。
>
> 他不是忘了她的臉。是他想記住的東西，原本就沒有一張臉。

**玩家選項**：

1. 「所以『她』不是一個人。」
2. 「他想回來看的，是這裡。」
3. 「也許他只是怕沒有人記得。」

**共同收束**：

> 嗯。名字不一定留得住。可是一條街被人走過、住過、想念過，總會有點什麼留下來。

**資訊卡**：

- 【史實可考】剝皮寮保留多個時代的街屋與空間層次；最終文字依剝皮寮研究檔與人工覆核。
- 【本作敘事】畫中人物與街景重疊，是本作對無名居民記憶的虛構表達。

---

## 7. 終章：年代簿的收藏標記

**觸發條件**：完成 `beat_bopiliao_reveal` 後，玩家開啟年代簿。

**年代簿**：

> 這封信仍然沒有署名。這幅畫也仍然沒有一張能被辨認的臉。
>
> 你想怎麼替它們留下標記？

| 玩家選項                 | 寫入旗標                 | 結尾文字                                                       |
| ------------------------ | ------------------------ | -------------------------------------------------------------- |
| 給那條街。               | `ending_mark = street` | 有些地方不是因為沒變才被記住，而是因為變過，還有人願意回頭看。 |
| 給那些沒有留下名字的人。 | `ending_mark = people` | 有些名字沒進到書裡，但曾經在這裡過日子的人，並沒有因此不存在。 |
| 給還在找回家路的人。     | `ending_mark = home`   | 回家未必是回到原來的樣子；有時只是終於知道，自己想回去看什麼。 |

**共通最後一句**：

> 〈回家的畫〉被收進萬華年代簿。不是答案，而是一個讓人願意再回來看看的位置。

**寫入**：完成 `beat_finale`，設定 `wanhua_story_completed = true`，解鎖「萬華餘白」。

> **年代簿的呈現方式〔定案〕**：年代簿**不是獨立場景**，以 UI 選項／面板呈現，掛在既有場景之下（可由 ProfileScene 或全域 UI 入口開啟）。因此 SDD §5.2 的場景清單不變。`beat_finale` 的觸發條件「開啟年代簿」指的是開啟該面板，不是載入新場景。

### 7.1 MVP 個人化年代簿頁〔定案〕

MVP 結局只產生**一頁**個人化年代簿短札；它依玩家在主線的三次選擇組合，不需要玩家另寫文字，也不改變結局可完成性：

| 選擇鍵          | 來源                     | 影響短札的段落                     |
| --------------- | ------------------------ | ---------------------------------- |
| `story_focus` | 信件開場：人／歷史／家   | 開頭：玩家最初想尋找的是什麼。     |
| `reveal_lens` | 剝皮寮揭露：街／人／回家 | 中段：玩家如何理解畫與萬華。       |
| `ending_mark` | 年代簿結尾：街／人／家   | 收束：玩家選擇把這段記憶留給什麼。 |

短札以三段預寫片段組合，最長 90 字；只能是玩家的導覽回顧，不得替阿明補寫身世、宣稱新史實或推論敏感身分。`repair_lens`、`building_lens` 與親近度可微調用詞或排序，但不得產生另一個結局或增加門檻。

---

## 8. 主線後：萬華餘白（未來規劃，MVP 暫不實作）

主線完成後不再反覆解釋阿明。每日輪播、重訪任務與「萬華餘白」短札**不納入 MVP**，不建立排程、任務目錄、API 或 UI；以下內容僅保留為未來規劃。每個靈魂的既有共鳴值仍可顯示為獨立親近度（初識／熟悉／默契），但 MVP 只用於個人化年代簿頁的用詞或排序。

| 地標   | 任務 ID                     | 任務名稱       | 導覽重點                           |
| ------ | --------------------------- | -------------- | ---------------------------------- |
| 龍山寺 | `q_longshan_rebuild_line` | 重修的線       | 重修、材料與時間留下的痕跡。       |
| 龍山寺 | `q_longshan_shared_space` | 停留的地方     | 公共空間如何容納停留、相遇與離開。 |
| 紅樓   | `q_redhouse_market_trace` | 市場留下的構造 | 市場時期與建築構造。               |
| 紅樓   | `q_redhouse_new_use`      | 換一種用途     | 建築如何持續被不同使用者使用。     |
| 剝皮寮 | `q_bopiliao_columns`      | 柱上的年代     | 亭仔腳與修補痕跡。                 |
| 剝皮寮 | `q_bopiliao_facades`      | 立面的拼接     | 不同時代的街屋立面。               |

未來若實作，每完成三項重訪任務，年代簿可解鎖一則不超過 60 字的「萬華餘白」短札。短札不得補寫阿明身世或宣稱新的歷史真相。

### 8.1 個人化年代簿與未來合作邊界

- 年代簿依 `story_focus`、`reveal_lens`、`ending_mark` 與各靈魂親近度，呈現只屬於該玩家的頁面；它是紀錄與回顧，**不鎖定**其他行政區、主線或遊玩路徑。
- 未來若納入觀光推廣、場館或在地店家禮物／優惠，必須是玩家主動開啟的獨立「合作」頁籤，不能出現在靈魂劇情、任務必要條件或親近度門檻中。
- 合作單位只可驗證一次性兌換碼；不得取得玩家身分、選項、對話、親近度或位置歷史。

### 8.2 親近度的可選導覽內容〔定案〕

每位靈魂的既有共鳴值同時作為獨立親近度，只增加該地標的**可選導覽短札與細節**，不增加主線門檻、不遮蔽核心歷史卡、不鎖其他區域或路線：

- 龍山寺守望者：修復、照看與日常持續的細節。
- 紅樓收藏家：空間形狀、用途變遷與人們聚集的細節。
- 剝皮寮記錄者：居住、無名居民與街區記憶的細節。

---

## 9. 完成主線後的重訪對話池

### 龍山寺

- 那封信的事，你已經看過了。現在不用急著找答案，看看今天這裡又留下了什麼。
- `story_focus = person`：記得一個人，本來就不只靠一張臉。
- `story_focus = history`：年月之外，還有每一次重新把日子過起來的人。
- `story_focus = home`：回家不一定是回到原來的樣子。能再走回來，也算一種。
- 任務完成：看見了就好。不必替它下結論。

### 西門紅樓

- 畫的事先收著吧。今天我比較想讓你看看：一棟樓換了用途，會不會還是同一棟樓？
- `story_focus = person`：你想找一張臉，我懂。但有些故事留到最後，反而是背景最清楚。
- `story_focus = history`：年代很會換人，也很會換用途。別只盯著年份，看看它怎麼繼續活。
- `story_focus = home`：回不去的地方，有時不是消失了，是換了個方式等人。
- 任務完成：不錯。你今天看的不是老東西，是還在變的東西。

### 剝皮寮

- 那張畫，你已經看懂一點了。現在看看腳下，這條街每天還在換說法。
- `story_focus = person`：她沒有臉，也不代表她什麼都不是。很多人本來就沒被好好畫下來。
- `story_focus = history`：別急著把它叫成以前。你站在這裡，它就是現在。
- `story_focus = home`：你覺得，家是門牌，還是有人記得你走過？
- 任務完成：嗯，這次看得仔細。下次再來，可能又會看到別的。

---

## 10. 實作與審核清單

1. 每一個 `trigger_condition` 只能讀取 beat、道具與任務完成狀態；不得依賴 LLM 對玩家文字的理解。
2. 劇情、任務包裝、重訪對話都必須注入當前人格卡與 taboo。
3. 所有任務觀察點先實勘，再寫進正式內容；觀察點變動時改用已審核的示意圖，不要求玩家找不存在的物件。
4. 歷史資訊卡與宗教場域台詞在**正式啟用／公開玩家路徑**前均須人工覆核；MVP 開發與內測可使用明確標示 `DRAFT`／`INTERNAL` 的假資料或測試內容，不得誤標為已審核或公開啟用。新版人格卡在審核、匯入與人工啟用前，僅能視為草稿。
5. 對話選擇採分支後匯流：保留 `story_focus` 與 `ending_mark` 的可見回應，但不創造互斥主線或錯過結局。

---

## 11. 逐句互動對話稿（審核底稿）

> 本節是玩家實際閱讀的文字底稿。每一個選項都有專屬回應；選項完成後在同一個章節節點匯流。`[資訊卡]` 代表 UI 顯示卡，不是角色台詞。

### 11.1 序章：留給街的信

**觸發**：首次進入萬華區圍欄，未完成 `beat_prologue_letter`。

```text
[系統／年代簿]
一封信夾在空白頁裡。紙邊有水漬，署名已經看不清。

[信]
我畫了她很多次，卻想不起她的臉。
若你還記得那條街，請替我回去看一眼。

[可看的位置]
· 她的側影
· 信紙的摺痕
· 畫面一角
```

| 位置           | 年代簿的敘述                                                                 | 寫入（僅第一個）        | 匯流                 |
| -------------- | ---------------------------------------------------------------------------- | ----------------------- | -------------------- |
| 她的側影       | 水痕帶走了臉，卻留著她微微側身的樣子——像是正要轉過去，應誰一聲。           | `story_focus=person`  | 年代簿翻到龍山寺頁。 |
| 信紙的摺痕     | 沒有地址，也沒有署名。但摺痕深得發白，像被反覆摺起、又反覆打開。             | `story_focus=history` | 年代簿翻到龍山寺頁。 |
| 畫面一角       | 畫面一角的線條像艋舺的舊街屋。筆觸有些笨拙，卻一筆一筆描得很慢。             | `story_focus=home`    | 年代簿翻到龍山寺頁。 |

> ⚠️ **三處皆可看、可重看；看過一處即可匯流**（不強制看完三處）。`story_focus` 只由**第一個看的位置**寫入，之後不覆蓋。呈現規格與理由見 §2.3。
>
> ⚠️ 本表的敘述文字與 §2.3 的表**必須逐字相同**。此處曾與 §2.3 分歧過（「正在回頭的姿勢」vs「微微側身」），而分歧的劇本沒有任何測試會抓到。

**匯流文字**：

```text
[系統／年代簿]
龍山寺或許記得，什麼會被留下。
```

### 11.2 第一章：龍山寺〈被留下的東西〉

**初次觸發**：持有 `item_wanhua_letter`，未完成 `beat_longshan_gate`。

```text
[龍山寺]
你手上的信，不像是寄給誰的。
倒像有人走得太急，只來得及把一句話留在原地。

[玩家選項]
1. 你見過這封信嗎？
2. 我想知道畫裡的人是誰。
3. 我想先看看這裡。
```

| 選項                   | 龍山寺回應                                                 | 匯流           |
| ---------------------- | ---------------------------------------------------------- | -------------- |
| 你見過這封信嗎？       | 信我不敢說見過。像它這樣沒有收件人的話，這裡倒留下過不少。 | 指向觀察任務。 |
| 我想知道畫裡的人是誰。 | 急著找一張臉，很容易漏掉她站著的地方。先看看這裡吧。       | 指向觀察任務。 |
| 我想先看看這裡。       | 好。先慢慢走。這裡塌過也修過——一八一五年那場地動，艋舺一帶倒了不少屋子，後來一次又一次蓋回來。看清楚一個地方，常比急著問它記得誰更有用。 | 指向觀察任務。 |

**任務完成後觸發**：`q_longshan_repair_trace` 完成，未完成 `beat_longshan_clue`。

```text
[龍山寺]
你剛才看的那些痕跡，不是同一年留下的。
人也是這樣，走過幾次變故，身上總會有些地方和從前不同。

這幅畫曾被放在這裡一段時間；不是什麼寶物，只是有人捨不得丟。
我一直以為畫的是一個人。後來才發現，畫裡的背景比那個人更固執。
```

| 條件                    | 龍山寺補句                                           |
| ----------------------- | ---------------------------------------------------- |
| `story_focus=person`  | 你想找她的臉。但畫的人，似乎更怕忘記她站在哪裡。     |
| `story_focus=history` | 年月未必替人留下名字，倒常把一處牆、一段路留得很久。 |
| `story_focus=home`    | 有人回不去的，不是一間房子，是認得路時的那種安心。   |

```text
[龍山寺]
後來，畫被交到一棟很會換身分的樓裡。
它比這張畫晚來很久，卻很懂得替故事留位置。
```

**[資訊卡：龍山寺與重建]**

- 史實可考：龍山寺與艋舺街區曾歷經災害、修復與重建。最終文字與來源待研究檔覆核。
- 本作敘事：信與畫在此停留的情節為虛構，不是館藏主張。

### 11.3 第二章：西門紅樓〈後來的保管者〉

**初次觸發**：完成 `beat_longshan_clue`，首次與紅樓對話（一次性；見附錄 B）。

```text
[紅樓]
欸，這張畫你從哪裡翻出來的？先說好，我 1908 年才出現。
留下它的人，我沒見過。我只是後來剛好接住它——很晚，晚到連它原本想說什麼都快褪色了。

[玩家選項]
1. 你為什麼留下它？
2. 你看得出畫的是哪裡嗎？
3. 這裡以前是什麼樣子？
```

| 選項                   | 紅樓回應                                                                       | 匯流           |
| ---------------------- | ------------------------------------------------------------------------------ | -------------- |
| 你為什麼留下它？       | 因為它看起來不像一個答案，像一個會把人帶去別處的問題。我對這種東西沒抵抗力。   | 指向觀察任務。 |
| 你看得出畫的是哪裡嗎？ | 看得出一點，又不敢裝懂。畫裡的線條不像我，可像某種老街的走法。                 | 指向觀察任務。 |
| 這裡以前是什麼樣子？   | 我換過不少用途。一開始這裡叫新起街市場，後來有戲院，再後來是各種聚人說話的地方。先看看我的兩種形狀怎麼接起來。 | 指向觀察任務。 |

**任務完成後觸發**：`q_redhouse_two_forms` 完成，未完成 `beat_redhouse_clue`。

```text
[紅樓]
你看，接縫不是破綻。有時候，它只是告訴你這裡曾經換過一種活法。
畫背面也有一條接縫。重新裱過時留下的紙邊，還看得到幾個字。

[畫背殘句]
……不是她。
是我回不去的＿＿。

[紅樓]
這下有意思了。你再看那個人的衣角，像不像一排屋簷？
我猜這幅畫真正想回去的地方，不在我這裡。
```

**[資訊卡：紅樓的多重用途]**

- 史實可考：西門紅樓建於 1908 年，曾有市場、戲院等不同使用歷程。最終文字與來源待研究檔覆核。
- 本作敘事：紅樓保管此畫為虛構；它不是阿明時代的見證者。

### 11.4 第三章：剝皮寮〈畫裡的街〉

**初次觸發**：完成 `beat_redhouse_clue`，首次與剝皮寮對話（一次性；見附錄 B）。

```text
[剝皮寮]
你拿著什麼？……畫？
我好像看過這些線。別站在這裡猜，走近一點看。

[玩家選項]
1. 畫裡的人是不是你？
2. 信說他想回去。
3. 我想知道這條街記得什麼。
```

| 選項                     | 剝皮寮回應                                                                   | 匯流           |
| ------------------------ | ---------------------------------------------------------------------------- | -------------- |
| 畫裡的人是不是你？       | 我？我不是一張畫裡的人。我只是這條街記得下來的聲音。……但那些線，確實很熟。 | 指向觀察任務。 |
| 信說他想回去。           | 回去哪裡？人常把這句話說得很短，可裡面裝的東西很多。                         | 指向觀察任務。 |
| 我想知道這條街記得什麼。 | 記得有人住過、走過、說過話。這一帶也被叫過土炭市，那時候門口堆的東西跟現在不一樣。名字不一定還在，路倒還認得一些。 | 指向觀察任務。 |

**任務完成後觸發**：`q_bopiliao_street_lines` 完成，且持有 `item_homeward_painting`。

```text
[剝皮寮]
你看那張畫時，先看到一個人。
可是你站在這裡，會不會覺得……那個人像一條街？

門洞像肩膀，屋簷像衣角，騎樓一格一格的，像有人一直往前走。
他不是忘了她的臉。是他想記住的東西，原本就沒有一張臉。

[玩家選項]
1. 所以「她」不是一個人。
2. 他想回來看的，是這裡。
3. 也許他只是怕沒有人記得。
```

| 選項                     | 剝皮寮回應                                                             | 寫入                   |
| ------------------------ | ---------------------------------------------------------------------- | ---------------------- |
| 所以「她」不是一個人。   | 嗯。她也可以是一條街、一盞燈、一段每天都會走過的路。                   | `reveal_lens=place`  |
| 他想回來看的，是這裡。   | 也許。回去看一眼，不一定是要把從前找回來；有時只是確認它沒有白白離開。 | `reveal_lens=return` |
| 也許他只是怕沒有人記得。 | 那你現在記得了。不是全部，但夠讓這件事不只剩一張沒人看的畫。           | `reveal_lens=memory` |

**[資訊卡：剝皮寮的街屋層次]**

- 史實可考：剝皮寮保留多個時代的街屋與空間層次。最終文字與來源待研究檔覆核。
- 本作敘事：畫中人物與街景重疊，是本作對無名居民記憶的虛構表達。

### 11.5 終章：年代簿的收藏標記

**觸發**：完成 `beat_bopiliao_reveal` 後首次開啟年代簿。

```text
[年代簿]
這封信仍然沒有署名。這幅畫也仍然沒有一張能被辨認的臉。
你想怎麼替它們留下標記？
```

| 玩家選項                 | 年代簿回應                                                     | 寫入                   |
| ------------------------ | -------------------------------------------------------------- | ---------------------- |
| 給那條街。               | 有些地方不是因為沒變才被記住，而是因為變過，還有人願意回頭看。 | `ending_mark=street` |
| 給那些沒有留下名字的人。 | 有些名字沒進到書裡，但曾經在這裡過日子的人，並沒有因此不存在。 | `ending_mark=people` |
| 給還在找回家路的人。     | 回家未必是回到原來的樣子；有時只是終於知道，自己想回去看什麼。 | `ending_mark=home`   |

```text
[年代簿]
〈回家的畫〉被收進萬華年代簿。
不是答案，而是一個讓人願意再回來看看的位置。
```

---

## 12. 後續故事資料格式底稿（提案）

> 此為內容作者、後端與 Unity 對話層對接的**資料形狀草案**，不是既有 API 或資料庫 schema。正式欄位、匯入器與版本策略須另行審核；`story_beats` 的推進仍以後端確定性規則為準。

```yaml
arc:
  arc_id: arc_wanhua_homeward_painting
  version: 1
  title: 萬華年代簿・回家的畫
  district_id: wanhua
  entry_spirit_id: longshan_temple
  route_policy: district_scoped_fixed_entry
  continuation_policy: multi_day_no_deadline
  review_status: PENDING_HUMAN_REVIEW
  entry_beat_id: beat_prologue_letter

variables:
  story_focus: [person, history, home]
  reveal_lens: [place, return, memory]
  ending_mark: [street, people, home]
  relationship_stage: [initial, familiar, kindred]

items:
  - item_id: item_wanhua_letter
    display_name: 留給街的信
    source_beat_id: beat_prologue_letter
  - item_id: item_homeward_painting
    display_name: 回家的畫
    source_beat_id: beat_longshan_clue

beats:
  - beat_id: beat_prologue_letter
    character_id: null
    trigger:
      all:
        - first_verified_entry_district: wanhua      # 行政區圍欄內含判定首次成立
        - beat_not_completed: beat_prologue_letter
    commands:
      - grant_item: item_wanhua_letter
      - show_asset: story_letter_and_painting
    nodes:
      - type: line
        speaker: 年代簿
        text_key: wanhua.prologue.open
      - type: line
        speaker: 信
        text_key: wanhua.prologue.letter_body   # 修正（見附錄 B）：這才是「信件全文」，
                                                 # StoryArc.intro_document_content 要回填的是這個 key，
                                                 # 不是上面 wanhua.prologue.open 那句年代簿旁白
      # ⚠️ 不是問句選單，是「看向哪裡」——人不會跟一封信對話（§2.3）。
      #    三處皆可看、可重看；看過一處即可往下走，不強制看完。
      #    story_focus 用 set_once：只由第一個看的位置寫入，之後不覆蓋。
      - type: look_points
        exclusive: false
        min_viewed_to_proceed: 1
      # 修正（2026-08-22）：label_text_key 是玩家**選之前**看到的字（[可看的位置]
      #   那三行），text_key 是**選之後**年代簿的敘述（本節表格那一欄）。
      #   先前只有 text_key，客戶端因此把敘述當成選項標籤畫出來——三處的答案
      #   在玩家選擇之前就全部攤開，「看向哪裡」這個動作完全失去意義。
      # target 的三個 *_reply 節點從未被寫出來（附錄 B）。敘述本身就是回應，
      #   所以移除 target，不留指向不存在節點的欄位。
        options:
          - id: look_figure
            label_text_key: wanhua.prologue.look.figure.label
            text_key: wanhua.prologue.look.figure
            set_once: {story_focus: person}
          - id: look_crease
            label_text_key: wanhua.prologue.look.crease.label
            text_key: wanhua.prologue.look.crease
            set_once: {story_focus: history}
          - id: look_corner
            label_text_key: wanhua.prologue.look.corner.label
            text_key: wanhua.prologue.look.corner
            set_once: {story_focus: home}
      # 原本是 `jump: prologue_merge`，但 prologue_merge 從來沒有被寫成節點。
      # 匯流文字本節已經有了，直接當成一句台詞收束。
      - type: line
        speaker: 年代簿
        text_key: wanhua.prologue.merge
    completion:
      mark_beat_completed: beat_prologue_letter
      unlock_spirit_id: longshan_temple

  - beat_id: beat_longshan_gate
    character_id: longshan_watcher
    # 走到這個地標、跟這隻靈魂講到話就推進（0032）。
    advance_on_dialogue: true
    trigger:
      all:
        - beat_completed: beat_prologue_letter
        - beat_not_completed: beat_longshan_gate
    requires: [item_wanhua_letter]
    nodes:
      - type: line
        speaker: longshan_watcher
        text_key: wanhua.longshan.gate.open
      - type: choice
        exclusive: true
        min_viewed_to_proceed: 1
        options:
          - id: ask_letter
            label_text_key: wanhua.longshan.gate.ask_letter.label
            text_key: wanhua.longshan.gate.ask_letter
          - id: ask_person
            label_text_key: wanhua.longshan.gate.ask_person.label
            text_key: wanhua.longshan.gate.ask_person
          - id: ask_place
            label_text_key: wanhua.longshan.gate.ask_place.label
            text_key: wanhua.longshan.gate.ask_place
    completion:
      mark_beat_completed: beat_longshan_gate

  - beat_id: beat_redhouse_gate
    character_id: red_house_collector
    # 走到這個地標、跟這隻靈魂講到話就推進（0032）。
    advance_on_dialogue: true
    trigger:
      all:
        - beat_completed: beat_longshan_clue
        - beat_not_completed: beat_redhouse_gate
    requires: [item_homeward_painting]
    nodes:
      - type: line
        speaker: red_house_collector
        text_key: wanhua.redhouse.gate.open
      - type: choice
        exclusive: true
        min_viewed_to_proceed: 1
        options:
          - id: ask_why
            label_text_key: wanhua.redhouse.gate.ask_why.label
            text_key: wanhua.redhouse.gate.ask_why
          - id: ask_where
            label_text_key: wanhua.redhouse.gate.ask_where.label
            text_key: wanhua.redhouse.gate.ask_where
          - id: ask_past
            label_text_key: wanhua.redhouse.gate.ask_past.label
            text_key: wanhua.redhouse.gate.ask_past
    completion:
      mark_beat_completed: beat_redhouse_gate

  - beat_id: beat_bopiliao_gate
    character_id: bopiliao_keeper
    # 走到這個地標、跟這隻靈魂講到話就推進（0032）。
    advance_on_dialogue: true
    trigger:
      all:
        - beat_completed: beat_redhouse_clue
        - beat_not_completed: beat_bopiliao_gate
    requires: [item_homeward_painting]
    nodes:
      - type: line
        speaker: bopiliao_keeper
        text_key: wanhua.bopiliao.gate.open
      - type: choice
        exclusive: true
        min_viewed_to_proceed: 1
        options:
          - id: ask_portrait
            label_text_key: wanhua.bopiliao.gate.ask_portrait.label
            text_key: wanhua.bopiliao.gate.ask_portrait
          - id: ask_return
            label_text_key: wanhua.bopiliao.gate.ask_return.label
            text_key: wanhua.bopiliao.gate.ask_return
          - id: ask_street
            label_text_key: wanhua.bopiliao.gate.ask_street.label
            text_key: wanhua.bopiliao.gate.ask_street
    completion:
      mark_beat_completed: beat_bopiliao_gate

  - beat_id: beat_longshan_clue
    character_id: longshan_watcher
    trigger:
      all:
        - beat_completed: beat_longshan_gate
        - quest_completed: q_longshan_repair_trace
        - beat_not_completed: beat_longshan_clue
    requires: [item_wanhua_letter]
    # conditional_line：依玩家在序章看了哪裡選一句。這是 story_focus 第一次
    # 真的產生差異——沒有這種節點的話，那個變數就只是記著沒用的值。
    nodes:
      - type: line
        speaker: longshan_watcher
        text_key: wanhua.longshan.clue.open
      - type: conditional_line
        speaker: longshan_watcher
        variable: story_focus
        cases:
          person: wanhua.longshan.clue.focus.person
          history: wanhua.longshan.clue.focus.history
          home: wanhua.longshan.clue.focus.home
      - type: line
        speaker: longshan_watcher
        text_key: wanhua.longshan.clue.close
    commands:
      - grant_item: item_homeward_painting
      - show_info_card: card_longshan_rebuild
    completion:
      mark_beat_completed: beat_longshan_clue
      unlock_spirit_id: ximen_red_house

  - beat_id: beat_redhouse_clue
    character_id: red_house_collector
    trigger:
      all:
        - beat_completed: beat_redhouse_gate
        - quest_completed: q_redhouse_two_forms
        - beat_not_completed: beat_redhouse_clue
    requires: [item_homeward_painting]
    nodes:
      - type: line
        speaker: red_house_collector
        text_key: wanhua.redhouse.clue.open
      # 說話的不是紅樓，是畫背面的殘字。speaker 刻意不是角色。
      - type: line
        speaker: 畫背殘句
        text_key: wanhua.redhouse.clue.fragment
      - type: line
        speaker: red_house_collector
        text_key: wanhua.redhouse.clue.close
    commands:
      - show_info_card: card_redhouse_uses
      - show_story_fragment: painting_back_inscription
    completion:
      mark_beat_completed: beat_redhouse_clue
      unlock_spirit_id: bopiliao_historic_block

  - beat_id: beat_bopiliao_reveal
    character_id: bopiliao_keeper
    trigger:
      all:
        - beat_completed: beat_redhouse_clue   # 修正（見文末附錄 B）：原本漏了這條，
                                                # 玩家理論上能跳過紅樓直達剝皮寮反轉
        - beat_completed: beat_bopiliao_gate
        - quest_completed: q_bopiliao_street_lines
        - has_item: item_homeward_painting
        - beat_not_completed: beat_bopiliao_reveal
    nodes:
      - type: line
        speaker: bopiliao_keeper
        text_key: wanhua.bopiliao.reveal.open
      - type: choice
        exclusive: true
        min_viewed_to_proceed: 1
        options:
          - id: lens_place
            label_text_key: wanhua.bopiliao.reveal.lens_place.label
            text_key: wanhua.bopiliao.reveal.lens_place
            set_once: {reveal_lens: place}
          - id: lens_return
            label_text_key: wanhua.bopiliao.reveal.lens_return.label
            text_key: wanhua.bopiliao.reveal.lens_return
            set_once: {reveal_lens: return}
          - id: lens_memory
            label_text_key: wanhua.bopiliao.reveal.lens_memory.label
            text_key: wanhua.bopiliao.reveal.lens_memory
            set_once: {reveal_lens: memory}
    commands:
      - show_info_card: card_bopiliao_layers
      - show_story_asset: homeward_painting_overlay
    completion:
      mark_beat_completed: beat_bopiliao_reveal
      unlock_beat_id: beat_finale

  - beat_id: beat_finale
    character_id: null
    trigger:
      all: [beat_completed: beat_bopiliao_reveal, open_screen: chronicle]
    # 補上 nodes（2026-08-22）：§11.5 的文字早就寫好了，但從來沒有進 beats:
    # 資料——結局因此是一個沒有任何台詞、也不可能寫入 ending_mark 的空節點。
    #
    # exclusive: true——結局標記只能選一個，這跟序章「三處都可看」相反。
    nodes:
      - type: line
        speaker: 年代簿
        text_key: wanhua.finale.open
      - type: choice
        exclusive: true
        min_viewed_to_proceed: 1
        options:
          - id: mark_street
            label_text_key: wanhua.finale.mark.street.label
            text_key: wanhua.finale.mark.street
            set_once: {ending_mark: street}
          - id: mark_people
            label_text_key: wanhua.finale.mark.people.label
            text_key: wanhua.finale.mark.people
            set_once: {ending_mark: people}
          - id: mark_home
            label_text_key: wanhua.finale.mark.home.label
            text_key: wanhua.finale.mark.home
            set_once: {ending_mark: home}
      - type: line
        speaker: 年代簿
        text_key: wanhua.finale.close
    completion:
      mark_beat_completed: beat_finale
      set: {wanhua_story_completed: true}
      unlock_content_group: wanhua_afterstory

info_cards:
  - card_id: card_longshan_rebuild
    review_status: PENDING_HUMAN_REVIEW
    historical_text_key: wanhua.card.longshan.historical
    fiction_text_key: wanhua.card.longshan.fiction
  - card_id: card_redhouse_uses
    review_status: PENDING_HUMAN_REVIEW
    historical_text_key: wanhua.card.redhouse.historical
    fiction_text_key: wanhua.card.redhouse.fiction
  - card_id: card_bopiliao_layers
    review_status: PENDING_HUMAN_REVIEW
    historical_text_key: wanhua.card.bopiliao.historical
    fiction_text_key: wanhua.card.bopiliao.fiction

afterstory:
  condition: wanhua_story_completed
  relationship:
    source: per_spirit_resonance
    stages:
      initial: [0, 9]
      familiar: [10, 39]
      kindred: [40, null]
    never_decrease: true
    no_route_gating: true
  daily_rotation:
    longshan_temple: [q_longshan_rebuild_line, q_longshan_shared_space]
    ximen_red_house: [q_redhouse_market_trace, q_redhouse_new_use]
    bopiliao_historic_block: [q_bopiliao_columns, q_bopiliao_facades]
```

### 12.1 資料格式使用規則

- `unlock_spirit_id` 解鎖的是**該靈魂的主線標記與主線對話**，不是靈魂本身。所有靈魂在地圖上一律可見、可感應、可對話、可收藏（`GET /spirits` 回傳全部召喚點）——不得因為主線未推進就把紅樓或剝皮寮從地圖上藏起來。
- `text_key` 對應可審核的字串表；不要把玩家可見文案散落在程式條件中。
- `trigger` 與 `completion` 僅供後端狀態機判定；LLM 只能生成受人格卡與 beat 指令約束的補充對話，不能自行完成 beat。
- `review_status=PENDING_HUMAN_REVIEW` 的卡片、台詞或人格不得進入正式玩家路徑；MVP 內測只能以 `DRAFT`／`INTERNAL` 隔離內容使用，不能把此狀態視為公開啟用。
- Unity 只依序呈現 `line`／`choice`／`command`，不自行改寫劇情條件；存檔需保存目前 node、所有變數與已讀文字。
- `first_verified_entry_district` 必須由**後端當次圍欄判定**產生；不得用歷史 GPS、其他區域到訪或 Unity 本機旗標替代。判定在伺服器端（不下發多邊形給客戶端：資料要下載且客戶端判定可偽造）。座標算完即丟，只持久化「已發放入口文件」這個結果。
- `route_policy=district_scoped_fixed_entry` 表示「萬華固定由龍山寺開場」；它不允許任何跨區必要 Beat，也不把完成萬華作為其他區域 arc 的前置。`district_id` 同時用於限制 `next_target` 只能是同區地標。

## 13. 景觀拍攝辨識任務（本版本正式規則）

景觀拍攝是「我真的停下來看」的儀式，不是攝影競賽，**也不是完成條件**。每站在公共觀察完成後，玩家**可以選擇**以 App 內相機拍一次核可公共景觀，使用 Session + Encounter Token 呼叫既有 `POST /api/v1/quests/{quest_id}/landmark-photo`。影像只在傳輸與伺服器記憶體辨識時存在，隨即釋放；不得保存影像、EXIF、座標、雜湊或作訓練資料。

| 地標   | 必要拍攝點                   | 構圖與角色引導                                                                                                       | 辨識成功／未辨識                                                                                         |
| ------ | ---------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 龍山寺 | `ls_public_roofline_photo` | 公共外觀／屋頂輪廓；避開神像、供品、香客、儀式、人臉。「不用拍得漂亮，也不用拍到誰。只讓這座地方自己在畫面裡說話。」 | 成功：「屋簷的線還在。」未辨識：「它沒有認出這個角度，也沒關係。你已經在這裡停下來看過了。」             |
| 紅樓   | `rh_public_facade_photo`   | 八角堂公共外觀；避開人潮、店家、展演、招牌。「替那個八角留下輪廓吧；別拍裡面的人。」                                 | 成功：「這個形狀，很會把不同時代的人聚到一起。」未辨識：「這裡的角度本來就不只一種；不想重拍也能繼續。」 |
| 剝皮寮 | `bp_street_facade_photo`   | 公共街屋立面；避開門牌、室內、路人、車牌、塗鴉。「只拍街，不拍住在街裡的人。」                                       | 成功：「你拍下的是一條線，線的另一邊總有人回家。」未辨識：「街不會因為一次辨認失敗就少了什麼。」         |

**景觀拍攝全程可選。** 玩家可以完全不拍就交件，主線不受任何影響——Beat、物件、結局、親近度都一樣。拍了只多一枚景觀印記。

因此以下三種情況**都不卡關**，且不該讓玩家看到像是出錯的畫面：

| 情況                                     | 處置                                                             |
| ---------------------------------------- | ---------------------------------------------------------------- |
| 玩家選擇不拍                             | 直接可交件，不提示、不勸拍                                       |
| 拍了但送不出去（無訊號、逾時、配額 429） | 視同已完成拍攝動作；可選在恢復連線時補送以取得印記，不補送也無妨 |
| 送達了但`landmark_recognized=false`    | 已取得拍攝動作，可交件；只顯示可選重拍                           |

**為什麼上傳不能是硬條件**：戶外收訊、配額與逾時都會讓玩家「人在現場、照片也拍了，卻交不了件」。那違反 SDD §18.7.3——這些是包裝，玩家的進度是真的。

Unity 不提供相簿入口，但後端不能由 JPEG 判斷來源，因此到場保證仍是 Encounter Token，與拍照與否無關。

```yaml
photo_evidence:
  # optional: true  = 不拍也能交件；upload 與 recognition 都不是完成條件
  ls_public_roofline_photo: {optional: true, required_upload: false, recognition_required: false}
  rh_public_facade_photo:   {optional: true, required_upload: false, recognition_required: false}
  bp_street_facade_photo:   {optional: true, required_upload: false, recognition_required: false}
```

每個拍攝點公開啟用前須完成公共視角、安全／無障礙、場域同意、替代示意圖、配額與辨識誤判測試。MVP 仍不做每日重訪或輪播；景觀印記不得成為路線鎖。

---

## 附錄 B：BE#59 匯入審閱發現的前置缺口（2026-08-19 修正）

**發現方式**：`scripts/import_story_arcs.py`（issue #59）匯入本文件 §12 的 `beats:` 區塊時如實轉錄，結果被拿去對照 §5.1／§5.2／§6.1／§6.2 的敘事文字才發現落差——兩處寫的順序關卡不一致。

**落差是什麼**：§5.2、§6.2 的任務前置欄位曾寫 `beat_redhouse_gate`、`beat_bopiliao_gate`，但 §12 的 `beats:` 資料區塊從未定義過這兩個 ID。`beat_bopiliao_reveal` 的 `trigger` 因此只檢查「持有 `item_homeward_painting`」（龍山寺那站發的）與「完成剝皮寮任務」，**沒有**卡「必須先完成紅樓那一站」——資料上玩家理論上能跳過紅樓直達剝皮寮的反轉。

**判定**：這不是匯入器的錯，是本文件自己的草稿內部沒對齊——§12 開頭本來就標了 `review_status: PENDING_HUMAN_REVIEW`，是「資料形狀草案，不是既有 schema」。§5.1／§6.1 的「初次對話」描述的其實是有台詞內容的一次性場景，但那兩場對話從未被建成正式 beat（沒有 `nodes:`、沒有進 `beats:` 清單）——`beat_redhouse_gate`／`beat_bopiliao_gate` 比較像是敘事者想像中「這場對話算不算發生過」的旗標，不是真的資料模型。

**採用方案 B（Lead 決定）**：不把兩個 gate 升格成正式 `StoryBeat`——它們除了「擋一下」之外沒有自己的 `commands`／`nodes`，多兩張表列沒有換到任何敘事內容。改為直接在 `beat_bopiliao_reveal.trigger` 補一條 `beat_completed: beat_redhouse_clue`，堵住那個跳關的資料缺口。

> ⚠️ **2026-08-22：方案 B 已被推翻（Lead 決定）。三個 gate 都建成正式 beat 了。**
>
> 方案 B 的理由是「它們除了擋一下之外沒有自己的 `nodes`」——但那句話成立的前提是**初次對話的台詞不打算進資料**。現在三章的初次對話都接進 `beats:` 了（§11.2／§11.3／§11.4 的文字一直都在，只是沒有出口），gate 有了自己的內容，理由就不成立。
>
> 現在的鏈是八個節點，`beat_longshan_gate`／`beat_redhouse_gate`／`beat_bopiliao_gate` 各自承載該地標的初次對話與三個提問選項。跳關的缺口仍然堵著——每個 `*_clue` 都以對應的 `*_gate` 為前置。
>
> 同時修正的還有：`§5.2`／`§6.2` 那兩個曾經懸空的 gate ID 現在**真的存在**了，兩處的前置欄位因此不再是「指向不存在的東西」。

**同步修正**：§5.2、§6.2 的前置欄位改指向真正存在的 beat_id（`beat_longshan_clue`、`beat_redhouse_clue`）；§5.1／§6.1／§11.3／§11.4 拿掉「未完成 `beat_*_gate`」那半句，因為那個 ID 從來不是可查詢的資料狀態。

**未處理、留給未來的內容設計**：§5.1／§6.1（含 §11.3／§11.4 的逐句版本）描述的「初次對話」場景——紅樓「你為什麼留下它？」三選項、剝皮寮「畫裡的人是不是你？」三選項——目前完全沒有對應的 `beat_id`，`beats:` 清單裡不存在它們。這些台詞現在只活在文件裡，沒有進資料庫，也不會被任何執行期邏輯播放。要不要把它們建成正式 beat（各自帶 `nodes:`），是下一次修訂 arc 內容時才要做的決定，不在這次修正範圍內。

## 附錄 C：`intro_document_content` 回填指錯 key（2026-08-19 修正）

#67 完成後查資料庫，發現 `StoryArc.intro_document_content` 被填成「一封信夾在空白頁裡，紙邊有水漬，署名已經看不清」——這是 §11.1 `[系統／年代簿]` 的**場景旁白**，不是信本身。真正的信件全文（`[信]` 那段：「我畫了她很多次，卻想不起她的臉。若你還記得那條街，請替我回去看一眼。」）在 §12 的 `beats:` 資料裡從來沒有對應的節點與 `text_key`——這不是匯入器的錯，是委派時給的 key 名稱本身指錯了。

`beat_prologue_letter.nodes` 已補上一個 `speaker: 信` 的節點、`text_key: wanhua.prologue.letter_body`。`intro_document_content` 之後應該從這個新 key 回填，不是 `wanhua.prologue.open`。

尚待執行：重跑 `import_story_arcs.py`（`nodes:` 結構變了）與 `import_story_strings.py`（新增一個 key、修正回填目標）。
