-- Migration 003: rename SQ -> XYZ (Block Inc ticker change in 2025).
--
-- Square Inc renamed itself "Block, Inc." in 2021, but the ticker
-- only flipped from SQ to XYZ in late 2025. Tiingo and SEC EDGAR no
-- longer recognize SQ, so /watch was stuck on "MIXED SQ score 0" and
-- /refresh_sharia logged "data sources failed" for it.
--
-- Touches every symbol-bearing table to maintain referential integrity
-- under PRAGMA foreign_keys=ON. Naturally idempotent: WHERE symbol='SQ'
-- matches at most one set of rows on first run, zero on every rerun.
--
-- FK toggle: foreign_keys is ON for our connections, which would block
-- the parent UPDATE while children still reference SQ. executescript
-- runs in autocommit, so toggling the pragma off/on around the renames
-- is safe and the connection is back to FK=ON before any other code
-- reads it.

PRAGMA foreign_keys = OFF;

UPDATE stocks_metadata          SET symbol='XYZ', updated_at=datetime('now') WHERE symbol='SQ';
UPDATE financial_ratios_history SET symbol='XYZ' WHERE symbol='SQ';
UPDATE compliance_alerts        SET symbol='XYZ' WHERE symbol='SQ';
UPDATE user_positions           SET symbol='XYZ' WHERE symbol='SQ';
UPDATE signals                  SET symbol='XYZ' WHERE symbol='SQ';
UPDATE agent_outputs            SET symbol='XYZ' WHERE symbol='SQ';
UPDATE heuristic_scores         SET symbol='XYZ' WHERE symbol='SQ';
UPDATE prescreen_results        SET symbol='XYZ' WHERE symbol='SQ';
UPDATE api_costs                SET symbol='XYZ' WHERE symbol='SQ';

PRAGMA foreign_keys = ON;
