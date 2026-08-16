-- 004 — Transactions that move money without being spending.
--
-- Problem found by comparing exports from two different banks: when money is
-- moved between two of your own accounts, the same euros appear TWICE.
--
--   10/03/2025  Bank A  "To Mario Rossi"                 -500.00
--   10/03/2025  Bank B  "Incoming transfer from Mario"   +500.00
--
-- Without a way to exclude them the spending totals come out inflated and
-- the monthly charts become useless in exactly the months when the most
-- money is moved around.
--
-- The same mechanism covers the **opening balance**: when you start tracking
-- an account that already holds money, you need an opening row that makes the
-- balance add up without counting as income or spending.
--
-- Note: real accounts do NOT belong in migrations. Migrations end up in the
-- public repository and run for anyone who clones it: accounts are personal
-- data and are created from the app.

ALTER TABLE categories
  ADD COLUMN exclude_from_stats TINYINT(1) NOT NULL DEFAULT 0 AFTER is_income;

-- Transfers between your own accounts are not spending.
UPDATE categories SET exclude_from_stats = 1
  WHERE name = 'Transfers' AND parent_id IS NULL;

-- Opening row for accounts that already held a balance.
INSERT INTO categories (name, color, icon, is_income, exclude_from_stats)
  VALUES ('Opening balance', '#607d8b', 'flag', 0, 1);
