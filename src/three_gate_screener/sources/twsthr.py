"""norway.twsthr.info scraper — weekly derived holder metrics, ~3 years history.

This is the ONLY free source that gives us historical (2023+) shareholder
distribution data for backtesting. Schema is DERIVED metrics (big-holder
concentration), not the raw 17-level buckets. See holders_derived in cache.py.

Be polite: the site runs on somebody's infra. Default 1 req/sec.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable

import pandas as pd
import requests
from bs4 import BeautifulSoup

from three_gate_screener import cache


URL_TEMPLATE = "https://norway.twsthr.info/StockHolders.aspx?stock={stock_id}"
USER_AGENT = "Mozilla/5.0 (three-gate-screener research bot)"

# A complete weekly record is 15 flat cells: date + 14 metrics.
# Columns 14 and 15 are empty padding in the rendered HTML.
RECORD_LEN = 15
DATE_RE = re.compile(r"^20[12][0-9][01][0-9][0-3][0-9]$")


def _parse_int(s: str) -> int | None:
    s = s.replace(",", "").strip()
    if not s or s in {"-", "--", "N/A"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_float(s: str) -> float | None:
    s = s.replace(",", "").strip()
    if not s or s in {"-", "--", "N/A"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_html(stock_id: str, timeout: int = 30) -> str:
    url = URL_TEMPLATE.format(stock_id=stock_id)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse(html: str, stock_id: str) -> pd.DataFrame:
    """Extract weekly derived records from the main table."""
    soup = BeautifulSoup(html, "html.parser")
    tables = sorted(
        soup.find_all("table"),
        key=lambda t: len(t.find_all("tr")),
        reverse=True,
    )
    if len(tables) < 3:
        return pd.DataFrame()

    # The 3rd largest table (index 2) is where the weekly records live,
    # based on our probe of 2330's page.
    target = tables[2]
    all_cells = [
        c.get_text(strip=True)
        for r in target.find_all("tr")
        for c in r.find_all(["td", "th"])
    ]

    seen: set[str] = set()
    rows: list[dict] = []
    i = 0
    while i < len(all_cells):
        if DATE_RE.match(all_cells[i]):
            block = all_cells[i : i + RECORD_LEN]
            if len(block) == RECORD_LEN:
                raw_date = block[0]
                if raw_date not in seen:
                    seen.add(raw_date)
                    rows.append(
                        {
                            "date": f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}",
                            "stock_id": stock_id,
                            "total_lots": _parse_int(block[1]),
                            "total_holders": _parse_int(block[2]),
                            "avg_lots_per_holder": _parse_float(block[3]),
                            "whale_400_lots": _parse_int(block[4]),
                            "whale_400_percent": _parse_float(block[5]),
                            "whale_400_count": _parse_int(block[6]),
                            "holders_400_600": _parse_int(block[7]),
                            "holders_600_800": _parse_int(block[8]),
                            "holders_800_1000": _parse_int(block[9]),
                            "holders_over_1000": _parse_int(block[10]),
                            "whale_1000_percent": _parse_float(block[11]),
                            "close_price": _parse_float(block[12]),
                        }
                    )
            i += RECORD_LEN
        else:
            i += 1

    return pd.DataFrame(rows)


@dataclass
class ScrapeResult:
    stock_id: str
    rows: int
    earliest: str | None
    latest: str | None
    elapsed_s: float
    error: str | None = None


def scrape_one(stock_id: str) -> ScrapeResult:
    t0 = time.time()
    try:
        html = fetch_html(stock_id)
        df = parse(html, stock_id)
    except Exception as exc:
        return ScrapeResult(stock_id, 0, None, None, time.time() - t0, str(exc))

    if df.empty:
        return ScrapeResult(stock_id, 0, None, None, time.time() - t0)

    with cache.connect() as conn:
        n = cache.upsert_df(conn, "holders_derived", df)
        cache.log_fetch(
            conn,
            dataset="TWSTHR_DERIVED",
            key=stock_id,
            start_date=df["date"].min(),
            end_date=df["date"].max(),
            rows=n,
        )
    return ScrapeResult(
        stock_id=stock_id,
        rows=n,
        earliest=df["date"].min(),
        latest=df["date"].max(),
        elapsed_s=time.time() - t0,
    )


def scrape_many(
    stock_ids: Iterable[str],
    sleep_s: float = 1.0,
    skip_already_scraped: bool = True,
) -> list[ScrapeResult]:
    cache.init_db()
    results: list[ScrapeResult] = []
    ids = list(stock_ids)
    total = len(ids)

    with cache.connect() as conn:
        already = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT key FROM fetch_log WHERE dataset = 'TWSTHR_DERIVED'"
            ).fetchall()
        }

    for n, sid in enumerate(ids, start=1):
        if skip_already_scraped and sid in already:
            continue
        res = scrape_one(sid)
        results.append(res)
        status = f"OK rows={res.rows:3d} range={res.earliest}..{res.latest}"
        if res.error:
            status = f"ERR {res.error[:60]}"
        print(f"[{n:>4d}/{total}] {sid}: {status} ({res.elapsed_s:.1f}s)")
        if n < total:
            time.sleep(sleep_s)

    return results


if __name__ == "__main__":
    # Quick smoke test
    res = scrape_one("2330")
    print(res)
