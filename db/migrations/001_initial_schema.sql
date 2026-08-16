-- 001 — Initial schema
-- Every amount is DECIMAL: never FLOAT for money (0.1 + 0.2 != 0.3).

CREATE TABLE accounts (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name        VARCHAR(100) NOT NULL,
  type        ENUM('checking','card','cash','savings') NOT NULL DEFAULT 'checking',
  currency    CHAR(3) NOT NULL DEFAULT 'EUR',
  iban        VARCHAR(34) NULL,
  archived    TINYINT(1) NOT NULL DEFAULT 0,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_accounts_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE categories (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name        VARCHAR(80) NOT NULL,
  parent_id   INT UNSIGNED NULL,
  color       CHAR(7) NOT NULL DEFAULT '#9e9e9e',
  icon        VARCHAR(40) NULL,
  is_income   TINYINT(1) NOT NULL DEFAULT 0,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_categories_parent (parent_id),
  CONSTRAINT fk_categories_parent FOREIGN KEY (parent_id)
    REFERENCES categories(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE category_rules (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  priority    INT NOT NULL DEFAULT 100,
  field       ENUM('description','counterparty') NOT NULL DEFAULT 'description',
  match_type  ENUM('contains','starts_with','exact','regex') NOT NULL DEFAULT 'contains',
  pattern     VARCHAR(255) NOT NULL,
  category_id INT UNSIGNED NOT NULL,
  account_id  INT UNSIGNED NULL,
  enabled     TINYINT(1) NOT NULL DEFAULT 1,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_rules_eval (enabled, priority),
  CONSTRAINT fk_rules_category FOREIGN KEY (category_id)
    REFERENCES categories(id) ON DELETE CASCADE,
  CONSTRAINT fk_rules_account FOREIGN KEY (account_id)
    REFERENCES accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- The saved column mapping: the heart of the generic CSV import.
CREATE TABLE import_profiles (
  id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  name                VARCHAR(100) NOT NULL,
  account_id          INT UNSIGNED NULL,
  `delimiter`         VARCHAR(4) NOT NULL DEFAULT ',',
  encoding            VARCHAR(20) NOT NULL DEFAULT 'utf-8',
  has_header          TINYINT(1) NOT NULL DEFAULT 1,
  skip_rows           INT NOT NULL DEFAULT 0,
  date_format         VARCHAR(32) NOT NULL DEFAULT '%d/%m/%Y',
  decimal_separator   CHAR(1) NOT NULL DEFAULT ',',
  thousands_separator CHAR(1) NULL,
  -- 'signed'   = one amount column carrying the sign
  -- 'separate' = two columns, money in and money out (common in Italy)
  amount_mode         ENUM('signed','separate') NOT NULL DEFAULT 'signed',
  col_date            VARCHAR(64) NOT NULL,
  col_description     VARCHAR(64) NOT NULL,
  col_counterparty    VARCHAR(64) NULL,
  col_amount          VARCHAR(64) NULL,
  col_amount_in       VARCHAR(64) NULL,
  col_amount_out      VARCHAR(64) NULL,
  -- some banks export expenses as positive numbers
  invert_sign         TINYINT(1) NOT NULL DEFAULT 0,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_import_profiles_name (name),
  CONSTRAINT fk_profiles_account FOREIGN KEY (account_id)
    REFERENCES accounts(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Import history: this is what makes it possible to undo a bad import.
CREATE TABLE import_runs (
  id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
  profile_id     INT UNSIGNED NULL,
  account_id     INT UNSIGNED NOT NULL,
  filename       VARCHAR(255) NOT NULL,
  rows_total     INT NOT NULL DEFAULT 0,
  rows_imported  INT NOT NULL DEFAULT 0,
  rows_skipped   INT NOT NULL DEFAULT 0,
  status         ENUM('pending','completed','failed','reverted') NOT NULL DEFAULT 'pending',
  error_message  TEXT NULL,
  created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_runs_account (account_id, created_at),
  CONSTRAINT fk_runs_profile FOREIGN KEY (profile_id)
    REFERENCES import_profiles(id) ON DELETE SET NULL,
  CONSTRAINT fk_runs_account FOREIGN KEY (account_id)
    REFERENCES accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE transactions (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  account_id      INT UNSIGNED NOT NULL,
  import_run_id   INT UNSIGNED NULL,
  booked_at       DATE NOT NULL,
  value_date      DATE NULL,
  -- negative = money out, positive = money in. SUM(amount) = balance.
  amount          DECIMAL(15,2) NOT NULL,
  currency        CHAR(3) NOT NULL DEFAULT 'EUR',
  description     VARCHAR(500) NOT NULL,
  counterparty    VARCHAR(255) NULL,
  category_id     INT UNSIGNED NULL,
  -- 'manual' = category chosen by the user: re-running the rules must never
  -- overwrite it.
  category_source ENUM('rule','manual') NULL,
  source          ENUM('manual','csv') NOT NULL,
  -- SHA-256 of (date + amount + normalised description + occurrence number).
  -- The UNIQUE constraint makes the database reject duplicates, so we do not
  -- have to trust the application code.
  dedup_hash      CHAR(64) NOT NULL,
  raw             JSON NULL,
  notes           TEXT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_tx_dedup (account_id, dedup_hash),
  KEY idx_tx_account_date (account_id, booked_at),
  KEY idx_tx_date (booked_at),
  KEY idx_tx_category (category_id),
  KEY idx_tx_import_run (import_run_id),
  CONSTRAINT fk_tx_account FOREIGN KEY (account_id)
    REFERENCES accounts(id) ON DELETE CASCADE,
  CONSTRAINT fk_tx_category FOREIGN KEY (category_id)
    REFERENCES categories(id) ON DELETE SET NULL,
  CONSTRAINT fk_tx_import_run FOREIGN KEY (import_run_id)
    REFERENCES import_runs(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- App preferences (NOT secrets: those live in .env).
CREATE TABLE settings (
  setting_key VARCHAR(64) NOT NULL,
  value       TEXT NULL,
  updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (setting_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
