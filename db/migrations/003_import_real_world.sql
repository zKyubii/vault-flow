-- 003 — Adjustments that came out of real CSV exports.
--
-- Every reason below comes from real data, not from guesswork:
--
-- 1. Some banks expose a `transaction_id`: a native id is a better
--    de-duplication key than any computed hash. We store it in `external_id`;
--    `dedup_hash` becomes sha256(external_id) when present, so there is still
--    ONE uniqueness constraint and ONE code path.
--
-- 2. Some exports also carry `mcc_code`. The earlier assumption ("MCC only
--    comes from PSD2") was wrong.
--
-- 3. Some banks keep `fee` and `tax` OUTSIDE `amount`. Real case: a stamp
--    duty row with amount=0.00 and tax=-8.50. Importing only `amount` would
--    lose that money.
--    -> effective amount = amount + fee + tax
--
-- 4. Some statements are multi-section: dozens of preamble rows, a final
--    "Total" row to exclude, amounts with a currency symbol and the sign
--    BEFORE it (-€19.41), English dates ("Mar 14, 2024").
--
-- 5. Several banks already provide a category of their own: useful as an
--    initial hint, not as the truth.

ALTER TABLE transactions
  ADD COLUMN external_id VARCHAR(128) NULL AFTER dedup_hash,
  ADD COLUMN mcc CHAR(4) NULL AFTER external_id,
  ADD KEY idx_tx_external (account_id, external_id),
  ADD KEY idx_tx_mcc (mcc);

ALTER TABLE import_profiles
  -- optional columns of the source file
  ADD COLUMN col_external_id VARCHAR(64) NULL AFTER col_amount_out,
  ADD COLUMN col_mcc VARCHAR(64) NULL AFTER col_external_id,
  ADD COLUMN col_fee VARCHAR(64) NULL AFTER col_mcc,
  ADD COLUMN col_tax VARCHAR(64) NULL AFTER col_fee,
  ADD COLUMN col_currency VARCHAR(64) NULL AFTER col_tax,
  ADD COLUMN col_category_hint VARCHAR(64) NULL AFTER col_currency,
  -- characters to strip before parsing an amount (e.g. "€")
  ADD COLUMN currency_symbols VARCHAR(16) NOT NULL DEFAULT '' AFTER col_category_hint,
  -- if the first column of a row equals this value, the import stops
  -- (some statements close the transaction section with a "Total" row)
  ADD COLUMN stop_at_value VARCHAR(64) NULL AFTER currency_symbols,
  -- rows that cannot be parsed: skip them instead of failing
  -- (required for multi-section files)
  ADD COLUMN skip_unparsable TINYINT(1) NOT NULL DEFAULT 0 AFTER stop_at_value;
