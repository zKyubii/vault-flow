// Stato dei filtri condiviso fra le schermate.
//
// Un solo stato, non uno per vista: se imposti "solo Revolut, ultimi 3 mesi"
// nel riepilogo e poi passi ai movimenti, ti aspetti di vedere gli stessi
// movimenti che compongono quei totali. Averne due separati è il modo più
// veloce per far perdere fiducia nei numeri.
//
// Persistito in localStorage: riaprendo l'app ritrovi la tua vista.

import { api } from "./api.js";
import { el, todayISO } from "./ui.js";

const STORAGE_KEY = "spese.filters.v1";

const DEFAULTS = {
  period: "month",
  date_from: null,
  date_to: null,
  account_ids: [],
  category_ids: [],
  kind: "all",
};

export const PERIODS = [
  { key: "month", label: "This month" },
  { key: "3m", label: "3 months" },
  { key: "year", label: "Year" },
  { key: "all", label: "All time" },
  { key: "custom", label: "Custom" },
];

// Copia profonda: gli array vanno duplicati, altrimenti lo stato condivide
// l'istanza con DEFAULTS e ogni toggle inquina i valori di partenza — con il
// risultato che "azzera i filtri" ripristina l'array già sporcato.
const freshDefaults = () => ({
  ...DEFAULTS,
  account_ids: [],
  category_ids: [],
});

export const state = load();

// anagrafiche, caricate una volta sola
let accounts = null;
let categories = null;

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return {
      ...freshDefaults(),
      ...saved,
      account_ids: Array.isArray(saved.account_ids) ? [...saved.account_ids] : [],
      category_ids: Array.isArray(saved.category_ids) ? [...saved.category_ids] : [],
    };
  } catch {
    return freshDefaults();
  }
}

function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* modalità privata o quota piena: i filtri restano validi per la sessione */
  }
}

export async function ensureLoaded() {
  if (!accounts) accounts = await api.accounts();
  if (!categories) categories = await api.categories();
  return { accounts, categories };
}

export const getAccounts = () => accounts || [];
export const getCategories = () => categories || [];
export const invalidate = () => {
  accounts = null;
  categories = null;
};

export function categoryLabel(category) {
  const parent = category.parent_id
    ? (categories || []).find((c) => c.id === category.parent_id)
    : null;
  return parent ? `${parent.name} › ${category.name}` : category.name;
}

/** Intervallo di date corrispondente al periodo scelto. */
export function dateRange() {
  const now = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  switch (state.period) {
    case "month":
      return { date_from: iso(new Date(now.getFullYear(), now.getMonth(), 1)),
               date_to: iso(new Date(now.getFullYear(), now.getMonth() + 1, 0)) };
    case "3m":
      return { date_from: iso(new Date(now.getFullYear(), now.getMonth() - 2, 1)), date_to: null };
    case "year":
      return { date_from: iso(new Date(now.getFullYear(), 0, 1)), date_to: null };
    case "custom":
      return { date_from: state.date_from, date_to: state.date_to };
    default:
      return { date_from: null, date_to: null };
  }
}

/** Parametri pronti per l'API. */
export function toQuery(extra = {}, { withDates = true } = {}) {
  const query = { ...extra };
  if (withDates) {
    const { date_from, date_to } = dateRange();
    if (date_from) query.date_from = date_from;
    if (date_to) query.date_to = date_to;
  }
  if (state.kind !== "all") query.kind = state.kind;
  return query;
}

/**
 * account_ids e category_ids vanno ripetuti, non uniti da virgole.
 *
 * `withDates: false` serve all'andamento mensile: filtrare la storia con il
 * periodo selezionato la ridurrebbe a una colonna sola quando scegli "questo
 * mese", che è l'opposto di ciò che un grafico storico deve mostrare. Conti,
 * categorie e tipo restano applicati.
 */
export function toSearchParams(extra = {}, options = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(toQuery(extra, options))) {
    if (value !== undefined && value !== null && value !== "") params.set(key, value);
  }
  for (const id of state.account_ids) params.append("account_ids", id);
  for (const id of state.category_ids) params.append("category_ids", id);
  return params;
}

/** Riassunto testuale dei filtri attivi, per la barra chiusa. */
export function describe() {
  const parts = [PERIODS.find((p) => p.key === state.period)?.label || "Period"];

  if (state.period === "custom" && state.date_from) {
    parts[0] = `${state.date_from} → ${state.date_to || "today"}`;
  }
  if (state.account_ids.length) {
    const names = state.account_ids
      .map((id) => (accounts || []).find((a) => a.id === id)?.name)
      .filter(Boolean);
    parts.push(names.length <= 2 ? names.join(" + ") : `${names.length} accounts`);
  }
  if (state.category_ids.length) {
    const names = state.category_ids
      .map((id) => (categories || []).find((c) => c.id === id)?.name)
      .filter(Boolean);
    parts.push(names.length <= 2 ? names.join(" + ") : `${names.length} categories`);
  }
  if (state.kind === "income") parts.push("income only");
  if (state.kind === "expense") parts.push("expenses only");
  return parts.join(" · ");
}

/** Aggiunge una categoria ai filtri (usata cliccando la ciambella). */
export function addCategory(id) {
  if (id === null || id === undefined) return false;
  if (state.category_ids.includes(id)) return false;
  state.category_ids.push(id);
  save();
  return true;
}

export const hasExtraFilters = () =>
  state.account_ids.length > 0 || state.category_ids.length > 0 || state.kind !== "all";

function toggle(list, id) {
  const index = list.indexOf(id);
  if (index === -1) list.push(id);
  else list.splice(index, 1);
}

/**
 * Barra dei filtri: chiusa mostra il riassunto, aperta il pannello completo.
 * Su un telefono un pannello sempre aperto mangerebbe mezzo schermo.
 */
export function filterBar(onChange) {
  const wrapper = el("div", { class: "filterbar" });
  let open = false;

  const summaryLine = el("button", { class: "filter-toggle" });
  const panel = el("div", { class: "filter-panel", hidden: true });

  const refreshSummary = () => {
    summaryLine.replaceChildren(
      el("span", { class: "truncate", text: describe() }),
      el("span", { class: "filter-caret", text: open ? "▲" : "▼" })
    );
    summaryLine.classList.toggle("filtered", hasExtraFilters());
  };

  const apply = () => {
    save();
    refreshSummary();
    buildPanel();
    onChange();
  };

  function buildPanel() {
    const customRow = el("div", { class: "field-row", hidden: state.period !== "custom" }, [
      el("div", {}, [
        el("label", { text: "From" }),
        el("input", {
          type: "date",
          value: state.date_from || "",
          onchange: (e) => {
            state.date_from = e.target.value || null;
            apply();
          },
        }),
      ]),
      el("div", {}, [
        el("label", { text: "To" }),
        el("input", {
          type: "date",
          value: state.date_to || todayISO(),
          onchange: (e) => {
            state.date_to = e.target.value || null;
            apply();
          },
        }),
      ]),
    ]);

    panel.replaceChildren(
      el("label", { text: "Period" }),
      el(
        "div",
        { class: "chips" },
        PERIODS.map((p) =>
          el("button", {
            class: `chip${state.period === p.key ? " active" : ""}`,
            text: p.label,
            onclick: () => {
              state.period = p.key;
              if (p.key === "custom" && !state.date_from) {
                state.date_from = new Date(new Date().getFullYear(), 0, 1)
                  .toISOString()
                  .slice(0, 10);
                state.date_to = todayISO();
              }
              apply();
            },
          })
        )
      ),
      customRow,

      el("label", { text: "Accounts" }),
      el("div", { class: "chips" }, [
        el("button", {
          class: `chip${state.account_ids.length === 0 ? " active" : ""}`,
          text: "All",
          onclick: () => {
            state.account_ids = [];
            apply();
          },
        }),
        ...getAccounts().map((a) =>
          el("button", {
            class: `chip${state.account_ids.includes(a.id) ? " active" : ""}`,
            text: a.name,
            onclick: () => {
              toggle(state.account_ids, a.id);
              apply();
            },
          })
        ),
      ]),

      el("label", { text: "Type" }),
      el(
        "div",
        { class: "chips" },
        [
          ["all", "All"],
          ["expense", "Expenses only"],
          ["income", "Income only"],
        ].map(([key, label]) =>
          el("button", {
            class: `chip${state.kind === key ? " active" : ""}`,
            text: label,
            onclick: () => {
              state.kind = key;
              apply();
            },
          })
        )
      ),

      el("label", { text: "Categories" }),
      el("div", { class: "chips" }, [
        el("button", {
          class: `chip${state.category_ids.length === 0 ? " active" : ""}`,
          text: "All",
          onclick: () => {
            state.category_ids = [];
            apply();
          },
        }),
        ...getCategories().map((c) =>
          el("button", {
            class: `chip${state.category_ids.includes(c.id) ? " active" : ""}`,
            style: state.category_ids.includes(c.id) ? `background:${c.color};border-color:${c.color}` : "",
            text: categoryLabel(c),
            onclick: () => {
              toggle(state.category_ids, c.id);
              apply();
            },
          })
        ),
      ]),

      hasExtraFilters()
        ? el("button", {
            class: "small",
            style: "margin-top:12px;width:100%",
            text: "Clear filters",
            onclick: () => {
              Object.assign(state, freshDefaults());
              apply();
            },
          })
        : null
    );
  }

  summaryLine.addEventListener("click", () => {
    open = !open;
    panel.hidden = !open;
    refreshSummary();
  });

  buildPanel();
  refreshSummary();
  wrapper.append(summaryLine, panel);
  return wrapper;
}
