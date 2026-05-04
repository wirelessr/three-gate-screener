# three-gate-screener

台股「三關篩選法」量化驗證專案。用**免費資料源**實作 pipeline 與回測，實測策略是否有 alpha。

**結論先行**：在我們能測的範圍內（476 檔 × 2023-2026），**沒有找到可靠的 edge**。策略的正 alpha 集中在牛市半年期，熊市或震盪期轉負。這不能否定原策略本身，但**免費資料無法驗證它的核心假設**。實務上策略近似於 long-only beta 放大，對只想暴露於台股大盤的人而言，**直接買大盤 ETF（例如 0050）是更簡單的選擇**。詳見 [`docs/RESULTS.md`](docs/RESULTS.md)。

儘管如此，過程中建立的資料 pipeline 與回測引擎可以重用於其他台股策略研究。

---

## 這個專案做了什麼

三關篩選法（原出處：坊間籌碼面流派）要求：

1. **第一關 主力動量**：特定**券商分點**連續 3 日以上買超，張數穩定或遞增
2. **第二關 成本過濾**：現價與主力成本乖離 ≤ 5%
3. **第三關 籌碼結構**：總集保戶數連續 4 週減少

我們的實作：

- FinMind 免費 tier 無法存取**券商分點資料** → 用**外資+投信合計淨買超**作為代理（粒度粗、雜訊高）
- 集保戶數資料官方只保留**最近一週** → 多源拼湊（TDCC opendata + lisa4930007 備份 + twsthr.info 爬蟲）
- 每個設計決策與它的權衡詳見 [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md)

---

## 快速開始

### 環境需求

- Python 3.12+
- `uv`（或 pip 也可）
- 一個 [FinMind](https://finmindtrade.com/) 免費帳號拿 token

### 安裝

```bash
# Clone with submodule (tdcc-archive contains historical TDCC CSVs).
git clone --recurse-submodules https://github.com/wirelessr/three-gate-screener.git
cd three-gate-screener
uv sync                        # 安裝依賴
uv sync --extra dev            # 含 pytest

cp .env.example .env
# 編輯 .env 填入 FINMIND_TOKEN

# 若已經 clone 但沒帶 --recurse-submodules，補抓 submodule:
git submodule update --init --recursive

# 日後要拉新快照（archive repo 每日自動 commit）:
git submodule update --remote tdcc-archive
```

### 驗證環境

```bash
# 檢查 FinMind token 與 tier 能力
uv run python -m three_gate_screener.verify
```

### 資料準備（第一次執行必跑）

```bash
# 1. 初始化 SQLite 快取
uv run python -m three_gate_screener.cache

# 2. 抓本週 TDCC 股權分散表（每週五跑一次以累積歷史）
uv run python -m three_gate_screener.sources.tdcc

# 3. 一次性 ingest 歷史 TDCC 快照（從 tdcc-archive submodule 讀取）
uv run python -m three_gate_screener.jobs.backfill_tdcc_archive

# 4. 一次性爬 twsthr.info 3 年衍生指標（~50 分鐘，1 req/s）
uv run python -m three_gate_screener.jobs.backfill_twsthr

# 5. 回補 FinMind 的日 K + 三大法人（有 rate limit，可能要多次 retry）
uv run python -m three_gate_screener.jobs.backfill_finmind_top500_daemon
```

### 執行回測

```bash
# 主回測 (Gate 1/2/3 + walk-forward + alpha)
uv run python -m three_gate_screener.backtest derived

# 各種 sensitivity sweep
uv run python -m three_gate_screener.analysis.robustness        # Gate1 年度拆分
uv run python -m three_gate_screener.analysis.variant_sweep     # V0-V3 變體
uv run python -m three_gate_screener.analysis.stop_loss_sweep   # 停損敏感度
uv run python -m three_gate_screener.analysis.smart_money_sweep # V0/V4/V5
```

### 跑測試

```bash
uv run pytest tests/
```

---

## 架構概覽

```
src/three_gate_screener/
├── config.py                # 專案常數（路徑、URL）
├── cache.py                 # SQLite schema + upsert helpers
├── backtest.py              # Walk-forward 引擎 + diagnostic helpers
├── verify.py                # 初期資料源探針
│
├── sources/                 # 資料源客戶端（讀網路、寫快取）
│   ├── finmind.py           # FinMind API + cache-aware wrapper
│   ├── tdcc.py              # TDCC opendata CSV 下載
│   └── twsthr.py            # twsthr.info HTML 解析
│
├── gates/                   # 策略邏輯（純運算、無 IO）
│   ├── gate1_institutional.py  # 法人連買 streak
│   ├── gate2_cost_spread.py    # 加權成本 + 乖離過濾
│   ├── gate3_holders.py        # 戶數遞減（原始 17 級資料）
│   └── gate3_derived.py        # 戶數遞減（twsthr 衍生指標 3 變體）
│
├── jobs/                    # Batch 工作腳本
│   ├── smoke_test.py
│   ├── backfill_tdcc_archive.py    # 從 tdcc-archive submodule 讀入歷史
│   ├── backfill_twsthr.py
│   ├── backfill_finmind_pilot.py
│   ├── backfill_finmind_top500.py
│   └── backfill_finmind_top500_daemon.py  # 自動 retry 版
│
└── analysis/                # 回測 sweep 腳本（每個對應一個假設）
    ├── robustness.py
    ├── variant_sweep.py
    ├── stop_loss_sweep.py
    └── smart_money_sweep.py

tests/                       # pytest 單元測試（只測 gates/，不測 IO）
docs/                        # METHODOLOGY + RESULTS
data/                        # 本地快取，不進 git
```

### 資料流

```
FinMind API  ──┐
TDCC CSV     ──┼──► SQLite cache ──► gates filter ──► backtest walk-forward ──► alpha / stats
twsthr HTML  ──┘       ▲
                       │
                 (idempotent upsert, 重跑不會重抓網路)
```

---

## 重用到其他策略

這個專案本身雖然結論是「策略沒 edge」，但**工具部分對其他台股策略研究有用**：

| 想做什麼 | 用這些 |
|---|---|
| 抓 FinMind 單檔 日 K / 法人資料 | `sources/finmind.load_stock` |
| 建立台股快取 | `cache.init_db` + `cache.upsert_df` |
| 每週五自動抓最新集保戶數分佈 | `sources/tdcc.snapshot` |
| 長歷史大戶持股比例時序 | `sources/twsthr.scrape_one` |
| Walk-forward 回測 + 停損 | `backtest.walk_forward_exits` |
| Alpha vs benchmark 計算 | `backtest.compute_alpha` |
| 年度 robustness 檢查 | 參考 `analysis/variant_sweep.py` 的寫法 |

建議另寫自己的 `src/<your_strategy>/` 目錄，直接 `from three_gate_screener.backtest import walk_forward_exits` 重用。

---

## 授權與資料責任

- **程式碼**：MIT License（見 `LICENSE`）
- **快取資料不進 repo**：`data/` 目錄整個 gitignored。使用者需照上方「資料準備」步驟自行抓取。這是為了：
  - 尊重各資料源授權（lisa 原 repo 無 license、twsthr 網站 TOS 僅供研究參考）
  - 讓每個使用者自行承擔各資料源的使用責任
- **FinMind token 絕不進 repo**：`.env` gitignored；請使用 `.env.example` 為範本

原始資料源：
- [FinMind](https://finmindtrade.com/) — API 需註冊免費帳號
- [TDCC opendata](https://opendata.tdcc.com.tw/getOD.ashx?id=1-5) — 政府開放資料
- [wirelessr/tdcc-opendata-archive](https://github.com/wirelessr/tdcc-opendata-archive) — 本專案的 TDCC 長期存檔（git submodule at `tdcc-archive/`），每日 GHA 自動更新。納入 [lisa4930007 的 2021 Q3 歷史備份](https://github.com/lisa4930007/ownership_distribution_access_db) 作為起始資料。
- [norway.twsthr.info](https://norway.twsthr.info/) — 神秘金字塔衍生指標

---

## 免責聲明

**這不是投資建議**。本專案為策略**驗證**實驗，結論是「在我們測試的範圍內找不到 alpha」。即使結論為正，過往回測不代表未來績效。

實測過程中我們嚴格只使用免費資料，粒度和雜訊都比原策略要求的分點資料差。所謂「策略無 edge」的結論僅適用於我們的免費代理實作，不能推論到原策略本身。
