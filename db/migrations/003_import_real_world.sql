-- 003 — Adeguamenti emersi dai CSV reali (Trade Republic, Revolut).
--
-- Motivazioni, tutte da dati veri e non da ipotesi:
--
-- 1. Trade Republic espone `transaction_id`: un id nativo è una chiave di
--    deduplica migliore di qualsiasi hash calcolato. Lo salviamo in
--    `external_id`; `dedup_hash` diventa sha256(external_id) quando c'è,
--    così resta UN solo vincolo di unicità e UN solo percorso nel codice.
--
-- 2. Trade Republic espone anche `mcc_code`. L'assunzione precedente
--    ("l'MCC arriva solo dal PSD2") era sbagliata.
--
-- 3. Trade Republic tiene `fee` e `tax` FUORI da `amount`. Esempio reale:
--    riga TAX_OPTIMIZATION con amount=0.00 e tax=-8.50 (bollo).
--    Importando solo `amount` si perderebbero quei soldi.
--    → importo effettivo = amount + fee + tax
--
-- 4. Revolut esporta un estratto conto multi-sezione: 62 righe di preambolo,
--    una riga finale "Total" da escludere, importi con simbolo valuta e
--    segno PRIMA del simbolo (-€19.41), date in inglese ("Mar 14, 2024").
--
-- 5. Sia Revolut che Trade Republic forniscono già una categoria propria:
--    utile come suggerimento iniziale, non come verità.

ALTER TABLE transactions
  ADD COLUMN external_id VARCHAR(128) NULL AFTER dedup_hash,
  ADD COLUMN mcc CHAR(4) NULL AFTER external_id,
  ADD KEY idx_tx_external (account_id, external_id),
  ADD KEY idx_tx_mcc (mcc);

ALTER TABLE import_profiles
  -- colonne opzionali della sorgente
  ADD COLUMN col_external_id VARCHAR(64) NULL AFTER col_amount_out,
  ADD COLUMN col_mcc VARCHAR(64) NULL AFTER col_external_id,
  ADD COLUMN col_fee VARCHAR(64) NULL AFTER col_mcc,
  ADD COLUMN col_tax VARCHAR(64) NULL AFTER col_fee,
  ADD COLUMN col_currency VARCHAR(64) NULL AFTER col_tax,
  ADD COLUMN col_category_hint VARCHAR(64) NULL AFTER col_currency,
  -- caratteri da rimuovere prima di interpretare un importo (es. "€")
  ADD COLUMN currency_symbols VARCHAR(16) NOT NULL DEFAULT '' AFTER col_category_hint,
  -- se il valore della prima colonna è questo, l'import si ferma
  -- (Revolut chiude la sezione movimenti con una riga "Total")
  ADD COLUMN stop_at_value VARCHAR(64) NULL AFTER currency_symbols,
  -- righe che non si riescono a interpretare: saltarle invece di fallire
  -- (necessario per i file multi-sezione tipo Revolut)
  ADD COLUMN skip_unparsable TINYINT(1) NOT NULL DEFAULT 0 AFTER stop_at_value;
