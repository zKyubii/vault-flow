import { api } from "../api.js";
import {
  categoryLabel,
  ensureLoaded,
  filterBar,
  getAccounts,
  getCategories,
  toSearchParams,
} from "../filters.js";
import { clear, el, empty, formatDayHeading, money, signClass, spinner, toast } from "../ui.js";

let search = "";

/**
 * Dopo aver messo una categoria a mano, propone di estenderla ai movimenti
 * simili. È così che nascono le regole: dal gesto che stai già facendo, non
 * da una schermata separata dove scrivere pattern a mano.
 */
async function offerRule(transaction, categoryId, container, onDone) {
  let info;
  try {
    info = await api.similar(transaction.id);
  } catch {
    return; // se il conteggio fallisce si resta con la sola categoria salvata
  }
  if (!info.others) return;

  const category = getCategories().find((c) => c.id === categoryId);
  container.replaceChildren(
    el("div", { class: "rule-offer" }, [
      el("div", { class: "muted", style: "margin-bottom:8px" }, [
        `Ci sono altri ${info.others} movimenti che contengono «${info.pattern}».`,
      ]),
      el("div", { class: "row", style: "gap:8px" }, [
        el("button", {
          class: "primary small",
          style: "flex:1",
          text: `Metti tutti in ${category ? category.name : "questa categoria"}`,
          onclick: async () => {
            try {
              const result = await api.ruleFromTransaction({
                transaction_id: transaction.id,
                category_id: categoryId,
              });
              toast(`Regola creata · ${result.updated} movimenti categorizzati`);
              onDone();
            } catch (error) {
              toast(error.message, true);
            }
          },
        }),
        el("button", {
          class: "small",
          text: "No",
          onclick: () => container.replaceChildren(),
        }),
      ]),
    ])
  );
}

function categorySelect(transaction, offerBox, onDone) {
  const select = el("select", { class: "inline-select" });
  select.append(el("option", { value: "", text: "— nessuna —" }));
  for (const category of getCategories()) {
    select.append(
      el("option", {
        value: category.id,
        text: categoryLabel(category),
        selected: category.id === transaction.category_id,
      })
    );
  }

  select.addEventListener("change", async () => {
    const value = select.value ? Number(select.value) : null;
    try {
      await api.setCategory(transaction.id, value);
      transaction.category_id = value;
      transaction.category_source = value ? "manual" : null;
      toast("Salvata — le regole non la sovrascriveranno più");
      if (value) await offerRule(transaction, value, offerBox, onDone);
      else offerBox.replaceChildren();
    } catch (error) {
      toast(error.message, true);
    }
  });
  return select;
}

function transactionRow(transaction, onChange) {
  const category = getCategories().find((c) => c.id === transaction.category_id);
  const account = getAccounts().find((a) => a.id === transaction.account_id);

  const row = el("div", { class: "tx" }, [
    el("span", {
      class: "tx-dot",
      style: `background:${category ? category.color : "var(--border)"}`,
    }),
    el("div", { class: "tx-main" }, [
      el("div", { class: "tx-desc truncate", text: transaction.description }),
      el("div", { class: "muted truncate" }, [
        [account ? account.name : "?", category ? category.name : "senza categoria"].join(" · "),
        transaction.category_source === "manual" ? " ✎" : "",
      ]),
    ]),
    el("span", {
      class: `amount ${signClass(transaction.amount)}`,
      text: money(transaction.amount),
    }),
  ]);

  let expanded = null;
  row.addEventListener("click", (event) => {
    if (event.target.tagName === "SELECT") return;
    if (expanded) {
      expanded.remove();
      expanded = null;
      row.classList.remove("open");
      return;
    }
    row.classList.add("open");
    const offerBox = el("div");
    expanded = el("div", { class: "tx-detail" }, [
      el("div", { class: "row", style: "gap:8px" }, [
        categorySelect(transaction, offerBox, onChange),
        el("button", {
          class: "small danger",
          text: "Elimina",
          onclick: async () => {
            if (!confirm(`Eliminare "${transaction.description}"?`)) return;
            try {
              await api.deleteTransaction(transaction.id);
              toast("Eliminata");
              onChange();
            } catch (error) {
              toast(error.message, true);
            }
          },
        }),
      ]),
      offerBox,
    ]);
    row.after(expanded);
  });

  return row;
}

export async function render(root) {
  root.append(spinner());
  try {
    await ensureLoaded();
  } catch (error) {
    root.replaceChildren(empty(error.message));
    return;
  }

  const searchInput = el("input", {
    type: "search",
    placeholder: "Cerca descrizione o controparte…",
    value: search,
  });
  let searchTimer;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      search = searchInput.value.trim();
      load();
    }, 350);
  });

  const listBox = el("div");
  const bar = filterBar(() => load());

  async function load() {
    clear(listBox).append(spinner());
    try {
      const params = toSearchParams({ limit: 200 });
      if (search) params.set("search", search);
      const page = await api.transactions(params);
      clear(listBox);

      if (!page.items.length) {
        listBox.append(empty("Nessun movimento con questi filtri"));
        return;
      }

      // La somma è quella delle righe effettivamente caricate. Se l'elenco è
      // troncato va detto: presentarla come "il totale" sarebbe un numero
      // sbagliato messo accanto a uno giusto.
      const truncated = page.total > page.items.length;
      const shown = page.items.reduce((sum, t) => sum + Number(t.amount), 0);
      listBox.append(
        el("div", { class: "row muted", style: "margin:4px 2px 8px" }, [
          el("span", {
            text: truncated
              ? `${page.items.length} di ${page.total} movimenti`
              : `${page.total} movimenti`,
          }),
          el("span", { class: "row", style: "gap:5px" }, [
            truncated ? el("span", { class: "muted", text: "somma mostrate" }) : null,
            el("span", { class: `amount ${signClass(shown)}`, text: money(shown) }),
          ]),
        ])
      );

      let currentDay = null;
      let card = null;
      for (const transaction of page.items) {
        if (transaction.booked_at !== currentDay) {
          currentDay = transaction.booked_at;
          listBox.append(el("div", { class: "date-head", text: formatDayHeading(currentDay) }));
          card = el("div", { class: "card" });
          listBox.append(card);
        }
        card.append(transactionRow(transaction, load));
      }
    } catch (error) {
      clear(listBox).append(empty(error.message));
    }
  }

  root.replaceChildren(el("div", { class: "card" }, [searchInput]), bar, listBox);
  load();
}
