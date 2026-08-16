import { api } from "../api.js";
import {
  addCategory,
  ensureLoaded,
  filterBar,
  invalidate,
  state,
  toSearchParams,
} from "../filters.js";
import { el, empty, formatDate, money, monthLabel, signClass, spinner, toast } from "../ui.js";

function delta(current, previous) {
  const a = Math.abs(Number(current));
  const b = Math.abs(Number(previous));
  if (!b) return null;
  const percent = Math.round(((a - b) / b) * 100);
  if (percent === 0) return null;
  return { percent, worse: percent > 0 };
}

function totalsCard(data) {
  const previous = data.previous;
  const box = (label, value, previousValue, invert = false) => {
    const change = previous ? delta(value, previousValue) : null;
    return el("div", { class: "total-box" }, [
      el("div", { class: "label", text: label }),
      el("div", { class: `value amount ${signClass(value)}`, text: money(value) }),
      change
        ? el("div", {
            class: `total-delta ${change.worse !== invert ? "up" : "down"}`,
            text: `${change.percent > 0 ? "+" : ""}${change.percent}%`,
          })
        : null,
    ]);
  };

  return el("div", { class: "totals" }, [
    box("Entrate", data.income, previous?.income, true),
    box("Uscite", data.expense, previous?.expense),
    box("Saldo", data.net, previous?.net, true),
  ]);
}

const NS = "http://www.w3.org/2000/svg";
const svgEl = (tag, attrs) => {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
};

/**
 * Ciambella interattiva: passando sopra (o toccando) una fetta il centro
 * mostra quella categoria; cliccandola la si aggiunge ai filtri.
 * Un grafico che non si può interrogare è solo una decorazione.
 */
function donut(categories, onPick) {
  const spese = categories.filter((c) => Number(c.total) < 0);
  const total = spese.reduce((sum, c) => sum + Math.abs(Number(c.total)), 0);
  if (!total) return null;

  const size = 190;
  const center = size / 2;
  const radius = 72;
  const circumference = 2 * Math.PI * radius;

  const svg = svgEl("svg", { viewBox: `0 0 ${size} ${size}`, class: "donut" });
  const labelTop = svgEl("text", {
    x: center,
    y: center - 4,
    "text-anchor": "middle",
    class: "donut-label",
  });
  const labelBottom = svgEl("text", {
    x: center,
    y: center + 15,
    "text-anchor": "middle",
    class: "donut-sub",
  });

  const showTotal = () => {
    labelTop.textContent = money(-total);
    labelBottom.textContent = `${spese.length} categorie`;
  };
  const showCategory = (category) => {
    labelTop.textContent = money(category.total);
    const share = Math.round((Math.abs(Number(category.total)) / total) * 100);
    labelBottom.textContent = `${category.name} · ${share}%`;
  };

  let offset = 0;
  const slices = [];
  for (const category of spese) {
    const share = Math.abs(Number(category.total)) / total;
    const slice = svgEl("circle", {
      cx: center,
      cy: center,
      r: radius,
      fill: "none",
      stroke: category.color,
      "stroke-width": 24,
      "stroke-dasharray": `${share * circumference} ${circumference}`,
      "stroke-dashoffset": -offset,
      transform: `rotate(-90 ${center} ${center})`,
      class: "donut-slice",
    });

    // Le altre fette sbiadiscono: l'occhio segue quella piena senza bisogno
    // di aloni, e funziona identico con mouse e con dito.
    const highlight = () => {
      for (const other of slices) {
        other.setAttribute("stroke-width", 24);
        other.setAttribute("opacity", "0.32");
      }
      slice.setAttribute("stroke-width", 31);
      slice.setAttribute("opacity", "1");
      showCategory(category);
    };
    const reset = () => {
      for (const other of slices) {
        other.setAttribute("stroke-width", 24);
        other.setAttribute("opacity", "1");
      }
      showTotal();
    };

    slice.addEventListener("mouseenter", highlight);
    slice.addEventListener("mouseleave", reset);
    // sul telefono non esiste il passaggio del cursore: il primo tocco
    // evidenzia, il secondo filtra
    let armed = false;
    slice.addEventListener("click", () => {
      if (!armed) {
        highlight();
        armed = true;
        // se non arriva il secondo tocco si torna com'era: una fetta
        // evidenziata per sempre sembrerebbe un errore
        setTimeout(() => {
          if (!armed) return;
          armed = false;
          reset();
        }, 2500);
        return;
      }
      armed = false;
      if (category.category_id !== null) onPick(category.category_id);
    });

    svg.append(slice);
    slices.push(slice);
    offset += share * circumference;
  }

  showTotal();
  svg.append(labelTop, labelBottom);
  return svg;
}

function categoriesCard(data, onPick) {
  const spese = data.by_category.filter((c) => Number(c.total) < 0);
  if (!spese.length) return el("div", { class: "card" }, [empty("Nessuna spesa nel periodo")]);

  const max = Math.max(...spese.map((c) => Math.abs(Number(c.total))));
  const previous = data.previous?.by_category || {};

  return el("div", { class: "card" }, [
    el("h2", { text: "Spese per categoria" }),
    donut(spese, onPick),
    el("div", { class: "muted", style: "text-align:center;margin:-8px 0 12px;font-size:.72rem" }, [
      "Tocca una fetta per vederla, di nuovo per filtrarla",
    ]),
    ...spese.map((c) => {
      const value = Math.abs(Number(c.total));
      const before = previous[c.category_id ?? 0];
      const change = before !== undefined ? delta(c.total, before) : null;
      return el("div", {
        class: "bar-row clickable",
        onclick: () => c.category_id !== null && onPick(c.category_id),
      }, [
        el("div", { class: "row" }, [
          el("span", { class: "truncate", text: `${c.name} · ${c.count}` }),
          el("span", { class: "row", style: "gap:6px" }, [
            change
              ? el("span", {
                  class: `mini-delta ${change.worse ? "up" : "down"}`,
                  text: `${change.percent > 0 ? "+" : ""}${change.percent}%`,
                })
              : null,
            el("span", { class: "amount neg", text: money(c.total) }),
          ]),
        ]),
        el("div", { class: "bar-track" }, [
          el("div", {
            class: "bar-fill",
            style: `width:${(value / max) * 100}%;background:${c.color}`,
          }),
        ]),
      ]);
    }),
  ]);
}

function monthsCard(months) {
  if (months.length < 2) return null;
  const max = Math.max(
    ...months.map((m) => Math.max(Number(m.income), Math.abs(Number(m.expense))))
  );

  const tip = el("div", { class: "chart-tip", hidden: true });
  const chart = el("div", { class: "monthchart" });

  const showTip = (month, column) => {
    tip.replaceChildren(
      el("div", { class: "tip-month", text: monthLabel(month.month) }),
      el("div", { class: "row", style: "gap:14px" }, [
        el("span", { class: "muted", text: "Entrate" }),
        el("span", { class: "amount pos", text: money(month.income) }),
      ]),
      el("div", { class: "row", style: "gap:14px" }, [
        el("span", { class: "muted", text: "Uscite" }),
        el("span", { class: "amount neg", text: money(month.expense) }),
      ]),
      el("div", { class: "row tip-net", style: "gap:14px" }, [
        el("span", { class: "muted", text: "Saldo" }),
        el("span", { class: `amount ${signClass(month.net)}`, text: money(month.net) }),
      ])
    );
    tip.hidden = false;

    // ancorato alla colonna ma tenuto dentro la card: sui mesi ai bordi
    // altrimenti uscirebbe dallo schermo
    const area = chart.getBoundingClientRect();
    const box = column.getBoundingClientRect();
    const wanted = box.left - area.left + box.width / 2 - tip.offsetWidth / 2;
    const clamped = Math.max(0, Math.min(wanted, area.width - tip.offsetWidth));
    tip.style.left = `${clamped}px`;

    for (const other of chart.children) other.classList.remove("active");
    column.classList.add("active");
  };

  const hideTip = () => {
    tip.hidden = true;
    for (const other of chart.children) other.classList.remove("active");
  };

  for (const month of months) {
    const column = el("div", { class: "monthcol" }, [
      el("div", { class: "monthbars" }, [
        el("div", {
          class: "mb pos",
          style: `height:${(Number(month.income) / max) * 100}%`,
        }),
        el("div", {
          class: "mb neg",
          style: `height:${(Math.abs(Number(month.expense)) / max) * 100}%`,
        }),
      ]),
      el("div", { class: "monthlabel", text: monthLabel(month.month) }),
    ]);

    column.addEventListener("mouseenter", () => showTip(month, column));
    column.addEventListener("mouseleave", hideTip);
    // sul telefono non c'è il cursore: il tocco apre e richiude
    column.addEventListener("click", () => {
      if (column.classList.contains("active")) hideTip();
      else showTip(month, column);
    });

    chart.append(column);
  }

  return el("div", { class: "card" }, [
    el("h2", { text: "Andamento mensile" }),
    el("div", { class: "chart-wrap" }, [tip, chart]),
  ]);
}

function topCard(top) {
  if (!top.length) return null;
  return el("div", { class: "card" }, [
    el("h2", { text: "Spese più grandi" }),
    ...top.map((t) =>
      el("div", { class: "row", style: "padding:7px 0" }, [
        el("div", { class: "stack" }, [
          el("span", { class: "truncate", text: t.description }),
          el("span", {
            class: "muted",
            text: `${formatDate(t.booked_at)} · ${t.category || "senza categoria"}`,
          }),
        ]),
        el("span", { class: "amount neg", text: money(t.amount) }),
      ])
    ),
  ]);
}

const CADENCE_LABEL = {
  weekly: "a settimana",
  monthly: "al mese",
  quarterly: "ogni 3 mesi",
  yearly: "all'anno",
};

function subscriptionsCard(data) {
  const active = data.subscriptions.filter((s) => s.active);
  if (!active.length) return null;

  return el("div", { class: "card" }, [
    el("h2", { text: "Abbonamenti rilevati" }),
    el("div", { class: "row", style: "margin-bottom:10px" }, [
      el("div", { class: "stack" }, [
        el("span", { class: "amount neg", style: "font-size:1.2rem", text: `${money(data.monthly_total)}/mese` }),
        el("span", { class: "muted", text: `${money(data.yearly_total)} all'anno` }),
      ]),
      el("span", { class: "muted", text: `${data.active_count} attivi` }),
    ]),
    ...active.map((s) =>
      el("div", { class: "row", style: "padding:7px 0;border-top:1px solid var(--border)" }, [
        el("div", { class: "stack" }, [
          el("span", { class: "truncate", text: s.description }),
          el("span", {
            class: "muted",
            text: `${CADENCE_LABEL[s.cadence] || s.cadence} · prossimo ${formatDate(s.next_expected)}`,
          }),
        ]),
        el("span", { class: "amount neg", text: money(-s.amount) }),
      ])
    ),
    data.subscriptions.some((s) => !s.active)
      ? el("div", { class: "muted", style: "margin-top:10px;font-size:.76rem" }, [
          `Non più addebitati: ${data.subscriptions.filter((s) => !s.active).map((s) => s.pattern).join(", ")}`,
        ])
      : null,
  ]);
}

function balancesCard(balances, onChange) {
  const form = el("div", { hidden: true });
  const nameInput = el("input", { type: "text", placeholder: "Es. Conto principale" });
  const typeSelect = el("select", {}, [
    el("option", { value: "checking", text: "Conto corrente" }),
    el("option", { value: "card", text: "Carta" }),
    el("option", { value: "savings", text: "Risparmio" }),
    el("option", { value: "cash", text: "Contanti" }),
  ]);

  form.append(
    el("div", { style: "border-top:1px solid var(--border);margin-top:8px;padding-top:8px" }, [
      el("label", { text: "Nome del conto" }),
      nameInput,
      el("label", { text: "Tipo" }),
      typeSelect,
      el("button", {
        class: "primary small",
        style: "width:100%;margin-top:10px",
        text: "Crea conto",
        onclick: async () => {
          if (!nameInput.value.trim()) return toast("Serve un nome", true);
          try {
            await api.createAccount({
              name: nameInput.value.trim(),
              type: typeSelect.value,
              currency: "EUR",
            });
            toast("Conto creato");
            invalidate();
            onChange();
          } catch (error) {
            toast(error.message, true);
          }
        },
      }),
    ])
  );

  return el("div", { class: "card" }, [
    el("h2", { text: "Conti" }),
    ...balances.map((a) =>
      el("div", { class: "row", style: "padding:7px 0" }, [
        el("div", { class: "stack" }, [
          el("span", { text: a.name }),
          el("span", { class: "muted", text: `${a.transactions} movimenti` }),
        ]),
        el("div", { class: "row", style: "gap:8px" }, [
          el("span", { class: `amount ${signClass(a.balance)}`, text: money(a.balance) }),
          el("button", {
            class: "small danger",
            style: "min-height:28px;padding:2px 9px",
            text: "×",
            title: "Elimina il conto",
            onclick: async (event) => {
              event.stopPropagation();
              // il numero di movimenti va detto PRIMA: è irreversibile
              const message = a.transactions
                ? `Eliminare "${a.name}"?\n\nVerranno cancellati anche i suoi ${a.transactions} movimenti. Non si torna indietro.`
                : `Eliminare "${a.name}"?`;
              if (!confirm(message)) return;
              try {
                const result = await api.deleteAccount(a.id);
                toast(result.detail);
                invalidate();
                onChange();
              } catch (error) {
                toast(error.message, true);
              }
            },
          }),
        ]),
      ])
    ),
    el("button", {
      class: "small",
      style: "width:100%;margin-top:10px",
      text: "+ Aggiungi conto",
      onclick: () => {
        form.hidden = !form.hidden;
        if (!form.hidden) nameInput.focus();
      },
    }),
    form,
  ]);
}

export async function render(root) {
  root.append(spinner());
  try {
    await ensureLoaded();
  } catch (error) {
    root.replaceChildren(empty(error.message));
    return;
  }

  const content = el("div");
  let bar = filterBar(() => load());
  root.replaceChildren(bar, content);

  // cliccando una fetta o una barra si aggiunge quella categoria ai filtri:
  // la barra va ricostruita per mostrare il chip attivo
  const pickCategory = (categoryId) => {
    if (!addCategory(categoryId)) return;
    const rebuilt = filterBar(() => load());
    bar.replaceWith(rebuilt);
    bar = rebuilt;
    toast("Filtro categoria aggiunto");
    load();
  };

  async function load() {
    content.replaceChildren(spinner());
    try {
      const params = toSearchParams({ compare: true });
      const [summary, months, top, balances, subs] = await Promise.all([
        api.summary(params),
        // l'andamento ignora il filtro di periodo: un grafico storico
        // ridotto a una colonna sola non è un grafico storico
        api.months(toSearchParams({ months: 12 }, { withDates: false })),
        api.top(toSearchParams({ limit: 8 })),
        api.balances(),
        api.subscriptions(),
      ]);

      const children = [totalsCard(summary)];

      if (summary.previous) {
        children.push(
          el("div", { class: "muted", style: "text-align:center;margin:-4px 0 12px;font-size:.76rem" }, [
            `confronto con ${formatDate(summary.previous.date_from)} → ${formatDate(summary.previous.date_to)}`,
          ])
        );
      }

      if (Number(summary.transferred) > 0) {
        children.push(
          el("div", { class: "card" }, [
            el("div", { class: "row" }, [
              el("span", { class: "muted", text: "Giroconti e investimenti esclusi" }),
              el("span", { class: "muted", text: money(summary.transferred) }),
            ]),
          ])
        );
      }

      children.push(categoriesCard(summary, pickCategory));
      const chart = monthsCard(months);
      if (chart) children.push(chart);
      const subscriptions = subscriptionsCard(subs);
      if (subscriptions) children.push(subscriptions);
      const topExpenses = topCard(top);
      if (topExpenses) children.push(topExpenses);
      if (!state.account_ids.length) children.push(balancesCard(balances, load));

      content.replaceChildren(...children.filter(Boolean));
    } catch (error) {
      content.replaceChildren(empty(error.message));
    }
  }

  load();
}
