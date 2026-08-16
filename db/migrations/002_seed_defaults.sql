-- 002 — Dati di partenza: preferenze e albero categorie di base.
-- Tutto modificabile dall'interfaccia: qui c'è solo un punto di partenza
-- ragionevole, niente di hardcodato nella logica.
--
-- Valori neutri: questo file gira su ogni installazione, non solo sulla
-- propria. Il nome visualizzato si cambia dalle impostazioni dell'app.

INSERT INTO settings (setting_key, value) VALUES
  ('display_name',     'User'),
  ('default_currency', 'EUR'),
  ('month_start_day',  '1'),
  ('theme',            'auto');

-- Categorie principali
INSERT INTO categories (name, color, icon, is_income) VALUES
  ('Home',            '#8d6e63', 'home',      0),
  ('Food',            '#ef6c00', 'utensils',  0),
  ('Transport',       '#1e88e5', 'car',       0),
  ('Health',          '#e53935', 'heart',     0),
  ('Leisure',         '#8e24aa', 'gamepad',   0),
  ('Subscriptions',   '#00897b', 'repeat',    0),
  ('Shopping',        '#d81b60', 'bag',       0),
  ('Education',       '#3949ab', 'book',      0),
  ('Taxes and fees',  '#546e7a', 'bank',      0),
  ('Income',          '#43a047', 'arrow-up',  1),
  ('Transfers',       '#78909c', 'exchange',  0),
  ('Uncategorised',   '#9e9e9e', 'question',  0);

-- Sottocategorie
INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Groceries',     id, '#fb8c00', 0 FROM categories WHERE name = 'Food' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Restaurants',   id, '#f4511e', 0 FROM categories WHERE name = 'Food' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Bars and cafes', id, '#ff7043', 0 FROM categories WHERE name = 'Food' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Fuel',            id, '#1565c0', 0 FROM categories WHERE name = 'Transport' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Public transport', id, '#42a5f5', 0 FROM categories WHERE name = 'Transport' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Rent',      id, '#6d4c41', 0 FROM categories WHERE name = 'Home' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Utilities',  id, '#a1887f', 0 FROM categories WHERE name = 'Home' AND parent_id IS NULL;

INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Salary',    id, '#2e7d32', 1 FROM categories WHERE name = 'Income' AND parent_id IS NULL;
INSERT INTO categories (name, parent_id, color, is_income)
SELECT 'Refunds',   id, '#66bb6a', 1 FROM categories WHERE name = 'Income' AND parent_id IS NULL;

-- Un conto "Cash" c'è sempre: è dove finiscono gli inserimenti manuali.
INSERT INTO accounts (name, type, currency) VALUES ('Cash', 'cash', 'EUR');
