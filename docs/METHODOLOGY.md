# 方法論 (Methodology)

這份文件說明三關篩選法策略的原始定義、我們被迫做的每個替代決定、以及為什麼。

## 1. 原始策略定義

「三關篩選法」核心假設：**追蹤主力穩定吃貨的足跡**。三個依序驗證的關卡：

| 關卡 | 原始定義 | 核心意圖 |
|---|---|---|
| 第一關 主力動量 | 特定**券商分點**連續 3 日以上買超，張數穩定或遞增 | 找出主力個別操盤手的買盤指紋 |
| 第二關 成本過濾 | 現價與「該分點加權平均成本」乖離 ≤ 5% | 避免追高，進場在主力成本附近 |
| 第三關 籌碼結構 | 總集保戶數連續 4 週減少 | 籌碼從散戶流向大戶 |

進場：三關都過 → 次日進場。
出場：跌破主力成本 2-3%（主力棄守線）、跌破 20MA、正乖離過大、分點轉賣、戶數反轉。

## 2. 資料可得性與代理策略

### 2.1 FinMind 免費 tier 的限制（實測）

第一關所需的**券商分點資料**（`TaiwanStockTradingDailyReport`）在 FinMind 免費 tier **完全無法存取**（即使指定單一 `stock_id` 也擋）。第三關的 `TaiwanStockHoldingSharesPer` 也完全擋。

這是**硬限制**，不是 rate limit。

### 2.2 第一關代理 — 三大法人合計

由於無法拿到分點資料，我們用**外資 (Foreign_Investor) + 投信 (Investment_Trust) 合計淨買超**當作「主力」的代理訊號。犧牲的是：
- 粒度變粗（無法區分個別操盤手）
- 雜訊變高（被動 ETF 流入混進外資數字）
- 失去「分點指紋」這個核心概念

這個代理與原策略有本質差距，在結論上必須明確標示。

### 2.3 第二關加權成本的定義

原策略：分點連買期間**該分點的逐筆成交價**加權平均。
代理做法：連買期間**每日收盤價**以當日淨買量加權：

```
Cost = Σ(daily_net_buy × close) / Σ(daily_net_buy)
```

只要用同一代理口徑（法人合計）跑整個 pipeline，成本數字在邏輯上是自洽的。

### 2.4 第三關資料源替代

第三關的集保戶數資料雖然是「公開政府資料」，但免費版管道有幾個限制：

| 管道 | 覆蓋 | 限制 |
|---|---|---|
| TDCC 官方 `opendata.tdcc.com.tw/getOD.ashx?id=1-5` | 只有**當週**最新快照 | 歷史週次會被覆寫 |
| FinMind `TaiwanStockHoldingSharesPer` | 多年歷史 | **Sponsor 付費 tier** |
| GitHub `lisa4930007/ownership_distribution_access_db` | 2021-07 ~ 2021-09 共 13 週 | 已停更；授權不明 |
| `norway.twsthr.info` 網頁爬取 | 2023-01 至今，170 週 | **衍生指標**（大戶持股 %、>1000張人數等），非原始 17 級分佈 |

我們的解法：
- **TDCC opendata** 每週五自動快照存檔（累積未來資料）
- **lisa 備份** 一次性 ingest 13 週歷史（提供完整 17 級格式的小樣本）
- **twsthr 爬蟲** 一次性 ingest 170 週衍生指標（提供較長時序）
- 設計 `gate3_holders`（原生格式，lisa + TDCC 本週）與 `gate3_derived`（衍生格式，twsthr）兩套

### 2.5 回測期間

基於資料可得性：
- **2021 Q3 視窗**（lisa 覆蓋）：小樣本，50 檔，用來 smoke test pipeline
- **2023-01-01 ~ 2026-04-30**（twsthr 覆蓋）：主回測視窗，目標 top 500 檔

FinMind 免費 tier 的 rate limit（實測約每小時 250 req）讓一次性 backfill 500 檔需要多個 rate-limit window。使用 `backfill_finmind_top500_daemon.py` 跨 window 自動 retry。

## 3. 回測引擎設計

### 3.1 Walk-forward 機制

訊號觸發後：
- **進場**：signal_date 收盤價
- **每日檢查**：若當日收盤 < `weighted_cost × (1 - stop_loss_pct)` 則當日停損出場
- **未觸發則持有到資料盡頭**（`end_of_data`）

這不是固定持有 N 天的回測。固定持有只用於最初 smoke test，後續一律用 walk-forward。

### 3.2 為什麼用 0050 作為 benchmark

`^TAIEX` 指數在 FinMind 免費 tier 不可得；0050（元大台灣 50 ETF）是免費 tier 能拉到的最接近大盤指標。計算 alpha 的公式：

```
alpha = (exit_price - entry_price) / entry_price
      - (benchmark_exit - benchmark_entry) / benchmark_entry
```

每個訊號的 alpha 是其**實際持有區間**對比同期間 0050 報酬，非固定窗口。

### 3.3 統計嚴謹度

全樣本 alpha 可能被單一年份或單一股票拉抬，因此所有結果都 **按年份拆分** 呈現，並定義 **「consistency」決策規則**：

> 一個變體只有在「所有測試年份都正 alpha」才算是「可靠的 edge」。單看全期數字容易誤判。

實測中沒有任何變體達到這個門檻（見 `RESULTS.md`）。

## 4. 變體設計與 Anti-Overfitting 原則

由於我們在同一份資料上測試多組參數，存在 p-hacking 風險。採取的紀律：

1. **每個變體必須有事前假設**：不做 grid search、不事後挑「最好看」的參數組合
2. **consistency 門檻**：4 個年度全部正 alpha 才算通過
3. **記錄所有變體**，包括失敗的（見 `analysis/` 目錄每個檔案）

已測試的變體假設清單：

| 變體 | 假設 |
|---|---|
| V0 基準 | 外資 + 投信合計、streak≥3、spread≤5%、stop 2.5% |
| V1 | Gate2 閾值改 2%（5% 太寬可能沒過濾效果）|
| V2 | streak≥5（更長連買更強訊號）|
| V3 | V1 + V2 疊加 |
| V4 | 只用投信（主動管理，無被動 ETF 干擾）|
| V5 | 外資 AND 投信（要求兩者獨立確認）|
| stop-loss | 1.5% / 2.5% / 3% / 5% / 10% / no-stop |
| gate3 A | 1:1 port 原規則（戶數連續 4 週減少）|
| gate3 B | 戶數減少 AND 大戶 %1000 持股比例上升 |
| gate3 C | 戶數剛開始減少（leading-edge 假設）|

## 5. 我們沒做的

明確列出，讓讀者知道這份實驗的邊界：

- **沒有**跌破 20MA 停利規則（說明書提到但未實作）
- **沒有**倉位控制（每個訊號假設相同 notional）
- **沒有**手續費、稅、滑價（所有結果都是理論報酬）
- **沒有**分股退市處理（假設 2023-2026 間的股票都連續可交易）
- **沒有**全市場掃描（最多 500 檔 top-by-size）
- **沒有**實盤驗證
- **沒有**真正測試原策略（因為沒有分點資料）
