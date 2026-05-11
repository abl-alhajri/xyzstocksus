-- Migration 005: comprehensive watchlist refresh.
--
-- Hard-deletes 19 tickers that are no longer on the watchlist:
--   • 9 HARAM (failed Sharia screening):
--       MARA, DIS, MCD, NKE, PEP, PYPL, COIN, TMO, PFE
--   • 10 EXPENSIVE (last close > $300 — outside the new price band):
--       LLY, COST, CAT, META, MA, AVGO, MSFT, UNH, V, HD
--
-- New tickers are seeded on the next boot via db.migrate._seed_watchlist
-- from the updated config/watchlist.py — no INSERTs needed here.
--
-- SAFETY: db.migrate._precheck_005 runs in Python BEFORE this SQL executes.
-- It aborts the migration with a clear error if any OPEN row exists in
-- user_positions for one of the 19 tickers. Closed positions are deleted
-- here along with all other history.
--
-- FK toggle: same pattern as migrations 003 and 004. foreign_keys is ON
-- for our connections; toggling off lets us delete the parent without
-- enumerating every child. Restored to ON at the end.

PRAGMA foreign_keys = OFF;

DELETE FROM heuristic_scores         WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM prescreen_results        WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM agent_outputs            WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM signals                  WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM financial_ratios_history WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM compliance_alerts        WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM user_positions           WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM api_costs                WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');
DELETE FROM stocks_metadata          WHERE symbol IN ('MARA','DIS','MCD','NKE','PEP','PYPL','COIN','TMO','PFE','LLY','COST','CAT','META','MA','AVGO','MSFT','UNH','V','HD');

PRAGMA foreign_keys = ON;
