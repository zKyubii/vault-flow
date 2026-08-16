-- 004 — Movimenti che spostano soldi senza essere spese.
--
-- Problema emerso confrontando gli export di due banche diverse: quando si
-- spostano soldi fra due conti propri, gli stessi euro compaiono DUE volte.
--
--   10/03/2025  Banca A  "To Mario Rossi"                 -500.00
--   10/03/2025  Banca B  "Incoming transfer from Mario"   +500.00
--
-- Senza un modo per escluderli, i totali di spesa risultano gonfiati e i
-- grafici mensili diventano inutilizzabili proprio nei mesi in cui si
-- spostano più soldi.
--
-- Stesso meccanismo per il **saldo iniziale**: quando si inizia a tracciare
-- un conto che ha già dei soldi dentro, serve una riga di apertura che faccia
-- quadrare il saldo senza risultare né entrata né spesa.
--
-- Nota: i conti veri NON vanno messi nelle migrazioni. Le migrazioni
-- finiscono nel repo pubblico e girano per chiunque cloni: i conti sono dati
-- personali e si creano dall'app.

ALTER TABLE categories
  ADD COLUMN exclude_from_stats TINYINT(1) NOT NULL DEFAULT 0 AFTER is_income;

-- I giroconti fra conti propri non sono spese.
UPDATE categories SET exclude_from_stats = 1
  WHERE name = 'Trasferimenti' AND parent_id IS NULL;

-- Riga di apertura per conti che avevano già un saldo.
INSERT INTO categories (name, color, icon, is_income, exclude_from_stats)
  VALUES ('Saldo iniziale', '#607d8b', 'flag', 0, 1);
