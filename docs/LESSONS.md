# 踩雷與教訓

這份文件記錄整個專案中踩到的坑，包含資料面的技術細節、策略驗證上的誤判、和過程中學到的紀律。如果你考慮做類似的台股量化研究，值得先看這一份。

---

## 資料面

### FinMind 免費 tier 的真實樣貌

規格寫的是 600 requests/hour，但實測下來：

- **Dataset-level 封鎖**：`TaiwanStockTradingDailyReport`（券商分點）與 `TaiwanStockHoldingSharesPer`（集保戶數分佈）**整個 dataset 對免費 tier 不開放**，即使指定單一 `stock_id` 也擋。這不是 rate limit 問題，是 tier 限制。
- **實際 rate limit 比文件低**：跑 500 檔 × 2 datasets = 1000 reqs 時，大約第 250-300 req 就撞到 `Requests reach the upper limit`。估計實際限制是 **每小時約 250 reqs**，不是 600。
- **rate limit window 不是滾動**：撞到後睡 30 分鐘重試，常常還撞。像是 **固定時段 quota**（每個 calendar hour 獨立計算），而非 trailing 60-minute window。

**教訓**：規劃 backfill 時以「每小時 200 reqs 安全值」為基準，寫 daemon 式的自動 retry 腳本（見 `jobs/backfill_finmind_top500_daemon.py`）。不要期待一次把全市場拉完。

### `get_object` parquet 後門不存在

我們一度期待用 FinMind 的 `storage_objects` endpoint 拿整日 parquet 繞過 rate limit。實測結果：**這個 endpoint 只接受 `TaiwanStockPriceTick` 這一個 dataset**，其他都擋；而且 `TaiwanStockPriceTick` 本身也要 Sponsor tier。

**教訓**：在程式碼裡看到的 API 不等於可以用，要實測。

### TDCC opendata 會靜默覆寫

`https://opendata.tdcc.com.tw/getOD.ashx?id=1-5` 永遠只回傳**當週最新**的快照。歷史週次不在任何公開 endpoint 上，且沒有警告文字。錯過週五抓取等於永遠失去那週的資料。

**教訓**：
1. 專案一開始第一個動作就是抓本週 TDCC 快照入袋（即使還沒寫完 pipeline）
2. 週五自動抓取要成為 cron 工作，不能靠手動

### Agent 的「發現」是宣稱，不是事實

用 agent 搜尋歷史 TDCC 備份時，agent 回報「twsthr.info 可追溯至 2017 年，十年歷史」。實際驗證：**真實深度是 2023-01-13 起，3.3 年**。agent 看到頁面上某個「2017」字串就當作是資料起點。

另一個誤判：agent 回報 twsthr 有「完整 17 級分佈」。實際上是**大戶人數、大戶持股 % 等衍生指標**，不是 raw 分級。Schema 完全不相容。

**教訓**：agent 的 summary 是「意圖」而非「事實」。關鍵 claim（時間範圍、schema、欄位）必須自己跑一個 request 驗證，花 30 秒。

### Stock ID 格式差異

同一個股票代號在不同資料源有不同呈現：

| 來源 | 2330 的字串 |
|---|---|
| FinMind API | `"2330"` |
| TDCC CSV | `"2330  "`（padding 到 6 字元，trailing spaces）|
| twsthr.info | `"2330"` |
| lisa 備份 | `"2330"` |

Parse TDCC 時如果沒 `.str.strip()` 就 join 別的 table，會完全 join 不到（且 SQLite 不會報錯，只是結果是空的）。我們第一次 parse TDCC 沒 strip，發現 `df[df.stock_id == '2330']` 回空才找出這個 bug。

**教訓**：任何從 CSV 讀進來的字串欄位都先 `strip()` 再用。建立 schema 時明確要求 normalized。

### lisa4930007 repo 沒有 LICENSE

GitHub 上找到的 13 週 TDCC 備份，原 repo **完全沒有 LICENSE 檔案**。依 GitHub 預設這等於「保留所有權利」，**我們無權 redistribute**。即使是「重新組織過的 CSV」也一樣。

**教訓**：找免費資料時，「存在」不等於「可用」。確認 LICENSE 後再決定能否 commit 進自己的 repo，或只能要求使用者自行下載。我們最後選了後者（寫在 `jobs/backfill_lisa_tdcc.py`，讓使用者自己跑）。

### FinMind client 的 `taiwan_stock_daily` 隱含 call

當你呼叫 `taiwan_stock_trading_daily_report(date=T)` 不帶 `stock_id` 時，client 內部會先 call 一次 `taiwan_stock_daily(start_date=T)` 去拿當日有交易的股票清單，**這個 pre-call 會撞上 tier 限制而報錯**，錯誤訊息看起來像是原本的 API 壞了。

**教訓**：FinMind client 是封裝過的，出錯時看 stack trace 底層才知道真的是哪支 API 擋住。

---

## 策略 / 回測面

### 固定持有 N 天不是回測，是估算

最初的 backtest 用固定 +5/+10/+20 天 horizon 計算報酬。跑出來 gate3 訊號 +20 日平均 -1.9%。

切換成 walk-forward + 2.5% 停損後，**同一組訊號同一份資料**：
- Gate3 平均 -1.06%（vs 原 -1.90%）
- Gate1 baseline +5.35%（vs 原 -1.44%）

兩個數字**結論完全相反**。原因：固定 20 天硬出場會把「早該停損」和「還在飛」的兩類訊號混在同一個報酬分佈裡，訊號的停損風險被平均掉、贏家的長尾也被截斷。

**教訓**：如果策略的出場條件是「條件觸發」而非「計時到期」，回測必須實作條件觸發出場邏輯。固定持有期的 baseline 可以當 smoke test，不能當結論依據。

### 2.5% 停損是憑感覺訂的，但不是問題所在

原策略說「跌破主力成本下方 2-3%」，我們取中間值 2.5%。後來懷疑太緊才做了停損敏感度 sweep（1.5% / 2.5% / 3% / 5% / 10% / no-stop）。

結果：**所有停損閾值在 2024 和 2026 都是負 alpha**。問題不在停損設多寬。

**教訓**：調參之前要先有假設。如果「調寬停損會救回 2024」，那就試；如果「調緊停損會救回 2024」，那就試另一方向。沒有假設的 grid search 就是 p-hacking。

### 全期 alpha 掩蓋半年偏差

Gate1 的**全期 alpha +4.20%**，乍看像是有 edge。但分年看：
- 2023-H1 +16.35%
- 2025-H1 +25.42%
- 其他六個半年幾乎都為負或接近零

**兩個牛市半年把整個平均拉起來**。單看全期會誤判策略有 edge，其實只是 bull market beta 放大。

**教訓**：任何 backtest 結果都要**按時期拆分**看 consistency。我們建立的決策規則：**「4 個年度全部正 alpha 才算是可靠的 edge」**。16 組變體測試中 0 組通過這個門檻。

### 「買 + 合計」不等於「智慧錢」

原策略 Gate1 要求特定券商分點的連買指紋。我們沒有這個資料，用**外資+投信合計淨買超**代替。跑出來 2024 全年、2026 Q1 都是負 alpha。

用 V5（外資 **AND** 投信，兩個都必須淨買）重測，2026 Q1 **翻正 +4.46%**，2024 虧損也縮小到 -2.00%。訊號品質顯著提升。

這證明了一個懷疑：**合計式的代理 (`foreign + trust`) 被 noise 淹沒**（被動 ETF 流入會在 foreign 裡放大、但和實質「智慧錢」意圖無關）。越貼近「獨立確認」的代理（AND 條件），alpha 越穩定。但即使 V5 也不達 consistency 門檻。

**教訓**：代理資料的設計會改變結論。一個「策略本身沒 edge」的結論，可能只是「代理資料太 noisy」而已。在免費 tier 做代理研究，必須承認：**我們能下的結論，永遠比原策略的 scope 小**。

### Gate3 擋掉「該擋的」還是「該留的」？

Gate3 淘汰了 83% 的 Gate1/Gate2 訊號。直覺上 filter 應該提升品質，但實測：

- Gate1 alpha: +4.20%
- Gate3 (A 變體) alpha: +1.67%（扣 2.5%）
- Gate3 (C 變體，leading edge): +4.24%（幾乎持平）

**沒有一個 Gate3 變體超越 baseline**。Gate3 作為額外篩選沒有加值，有時還扣分。可能原因：
- 集保戶數是**週更的落後指標**，等 4 週都減少已經是主力吃完的後期
- 和 Gate1 有高度相關（法人連買和大戶增加是同一件事的不同角度），沒提供獨立資訊

Gate3-C 實驗（戶數「剛開始」減少，而非連續 4 週）沒能救活它。

**教訓**：**多加一個 filter ≠ 訊號更好**。加 filter 要能證明它攜帶 incremental information，否則只是縮小樣本。

### Trust Only (V4) 和 V5 的差距告訴我們什麼

| 變體 | 2024 alpha | 2026 Q1 alpha |
|---|---|---|
| V0 合計 | -3.62% | -4.69% |
| V4 投信 only | -2.13% | +5.50% |
| V5 AND | -2.00% | +4.46% |

V4 比 V0 好、V5 和 V4 接近。這暗示**「把外資加進來」反而有害**（至少在熊市）。投信 (Investment_Trust) 是主動管理、規模小、決策集中的台灣 domestic 買盤，**比較像原策略要找的「主力」**。外資則有大量被動 ETF 流入，跟「吃貨」無關。

**教訓**：`foreign + trust` 這種籠統加總在台股脈絡下不是好做法。如果要找 smart money，**投信單獨**可能比合計好。

---

## 過程紀律

### 每週五 TDCC 抓取不能忘

今天（2026-05-04）抓的 TDCC 資料是 2026-04-30 那週的。如果下週五沒抓到 2026-05-07 那週，就**永遠**失去 2026-05-07 的 data point。

**教訓**：本專案最終**必須設定 cron**（macOS launchd 或 Linux cron）每週五自動跑 `tdcc.snapshot`。否則每個禮拜的手動心智負擔會讓你最終放棄。

### 不做就沒有，做了才有

專案一開始糾結 FinMind 免費 tier 能不能做、要不要升級付費、TDCC 歷史要怎麼找。花了幾個來回討論。

實際上第一步應該就是：
1. 抓本週 TDCC（5 分鐘）— 至少有一筆入袋
2. 試 FinMind 單檔查詢（1 分鐘）— 知道哪個 endpoint 擋了
3. Google 搜歷史備份（10 分鐘）— 找到 lisa

**教訓**：cheap experiments first。用 15 分鐘的實測取代 30 分鐘的討論。

### 說明書的變數都要當假設驗證

「停損 2-3%」是說明書的經驗值，我們照做了。「Gate3 4 週單調減少」也是說明書的經驗值，我們也照做了。

**兩個參數都沒驗證過**。停損 sweep 顯示各種閾值結果都差不多；Gate3 變體實驗顯示 4 週單調減少本身可能不是最優規則。

**教訓**：任何策略說明書給的參數，都是「某人的直覺」而非「被驗證的最優」。在回測時要把它們當 hypothesis 驗證，不是當 constant 使用。

### 樣本不足時不要下結論

最初 50 檔 × 4 個月樣本跑出來 Gate3 通過的 14 個訊號平均 +20d 報酬 -1.90%。一度想下「策略沒用」結論。擴大到 251 檔 × 3 年後，Gate3 alpha 其實只是小幅為正（+1.67%），不是 -1.90%。小樣本結果方向都可能錯。

**教訓**：n < 200 的 subset 看到的數字**任何方向都不要採信**。繼續拿資料、擴大樣本，再下結論。

---

## 如果要再做一次

根據上面的教訓，再來一次的順序會是：

1. **立刻抓 TDCC 本週**（成本 5 分鐘，錯過就沒了）
2. **實測 FinMind tier capability**（3 個 request 知道擋什麼）
3. **先寫 pipeline，不要先寫策略**（cache、walk-forward engine）
4. **找歷史備份（agent 輔助，但自己驗證）**
5. **用最小樣本跑通 end-to-end smoke test**
6. **擴大樣本**
7. **分年看 consistency，不看全期總和**
8. **每個 filter 要證明它加值，不然砍掉**
9. **看到「好看的數字」先找 confound**（市場 beta？單一時期？小樣本？）
10. **結論要清楚劃定 scope**（不要把代理實作的結論推到原策略）

最重要的事：**pipeline 有價值，即使策略無 edge**。這次專案的最大 take-away 是資料 pipeline，不是策略驗證。
