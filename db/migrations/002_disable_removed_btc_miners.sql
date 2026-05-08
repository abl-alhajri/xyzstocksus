-- Migration 002: disable BTC miners removed from the trimmed watchlist.
--
-- Context: commit ca991e4 trimmed the watchlist from 54 → 49 entries to fit
-- Tiingo's free-tier 50-symbol/day cap. Five tickers (CLSK, WULF, CIFR, HUT,
-- BTBT) were dropped because their BTC exposure overlaps with kept miners
-- (MARA, RIOT) plus MSTR / COIN / BTC-USD.
--
-- Naturally idempotent: WHERE symbol IN (...) matches at most 5 rows on first
-- run; subsequent runs match 0 (already disabled). DBs seeded post-trim never
-- had these symbols, so the UPDATE is a no-op there.

UPDATE stocks_metadata
SET enabled = 0,
    updated_at = datetime('now')
WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
