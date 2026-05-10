-- Migration 004: hard-delete the BTC miners that 002 disabled.
--
-- Migration 002 (commit 8de1c61) soft-disabled CLSK/WULF/CIFR/HUT/BTBT
-- when commit ca991e4 trimmed the watchlist for Tiingo's free-tier
-- 50-symbol/day cap. They've been enabled=0 ever since with no scans
-- against them. Time to remove the rows entirely so /watch stops
-- showing [disabled] ghosts and the DB is clean.
--
-- Naturally idempotent: WHERE symbol IN (...) matches at most one set
-- of rows on first run, zero thereafter.
--
-- FK toggle: same as migration 003. foreign_keys is ON for our
-- connections; toggling off lets us delete the parent without first
-- enumerating every child row. Restored to ON before any other code
-- reads the connection.

PRAGMA foreign_keys = OFF;

DELETE FROM heuristic_scores         WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM prescreen_results        WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM agent_outputs            WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM signals                  WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM financial_ratios_history WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM compliance_alerts        WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM user_positions           WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM api_costs                WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');
DELETE FROM stocks_metadata          WHERE symbol IN ('CLSK', 'WULF', 'CIFR', 'HUT', 'BTBT');

PRAGMA foreign_keys = ON;
