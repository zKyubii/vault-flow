import { api } from "../api.js";
import { clear, el, empty, money, spinner, toast } from "../ui.js";

let categories = [];

function categoryOptions(selectedId) {
  return categories.map((c) => {
    const parent = c.parent_id ? categories.find((p) => p.id === c.parent_id) : null;
    return el("option", {
      value: c.id,
      text: parent ? `${parent.name} › ${c.name}` : c.name,
      selected: c.id === selectedId,
    });
  });
}

export async function render(root) {
  root.append(spinner());

  let rules;
  let suggestions;
  try {
    [categories, rules, suggestions] = await Promise.all([
      api.categories(),
      api.rules(),
      api.suggestions({ limit: 15 }),
    ]);
  } catch (error) {
    root.replaceChildren(empty(error.message));
    return;
  }

  const reload = () => render(clear(root));

  // --- nuova regola ---
  const patternInput = el("input", { type: "text", placeholder: "Es. spotify" });
  const catSelect = el("select", {}, categoryOptions(null));
  const matchSelect = el("select", {}, [
    el("option", { value: "contains", text: "contiene" }),
    el("option", { value: "starts_with", text: "inizia con" }),
    el("option", { value: "exact", text: "è esattamente" }),
    el("option", { value: "regex", text: "espressione regolare" }),
  ]);
  const priorityInput = el("input", { type: "number", value: 100, min: 0 });

  const createBtn = el("button", { class: "primary", text: "Crea regola", style: "width:100%;margin-top:12px" });
  createBtn.addEventListener("click", async () => {
    if (!patternInput.value.trim()) return toast("Scrivi cosa cercare", true);
    try {
      await api.createRule({
        pattern: patternInput.value.trim(),
        category_id: Number(catSelect.value),
        match_type: matchSelect.value,
        priority: Number(priorityInput.value),
      });
      toast("Regola creata");
      reload();
    } catch (error) {
      toast(error.message, true);
    }
  });

  const newCard = el("div", { class: "card" }, [
    el("h2", { text: "Nuova regola" }),
    el("label", { text: "Se la descrizione…" }),
    matchSelect,
    patternInput,
    el("div", { class: "field-row" }, [
      el("div", {}, [el("label", { text: "Categoria" }), catSelect]),
      el("div", {}, [el("label", { text: "Priorità (più basso vince)" }), priorityInput]),
    ]),
    createBtn,
  ]);

  // --- applica ---
  const applyBox = el("div");
  const dryBtn = el("button", { text: "Prova senza scrivere" });
  const applyBtn = el("button", { class: "primary", text: "Applica" });

  const runApply = async (dryRun) => {
    clear(applyBox).append(spinner());
    try {
      const result = await api.applyRules({ only_uncategorized: true, dry_run: dryRun });
      const entries = Object.entries(result.by_category).sort((a, b) => b[1] - a[1]);
      clear(applyBox).append(
        el("div", { style: "margin-top:10px" }, [
          el("div", {
            text: dryRun
              ? `Prova: ${result.updated} transazioni verrebbero categorizzate (niente è stato scritto)`
              : `Categorizzate ${result.updated} transazioni`,
          }),
          result.protected
            ? el("div", { class: "muted", style: "margin-top:4px" }, [
                `${result.protected} lasciate stare perché corrette a mano`,
              ])
            : null,
          ...entries.map(([name, count]) =>
            el("div", { class: "row muted", style: "margin-top:4px" }, [
              el("span", { text: name }),
              el("span", { text: String(count) }),
            ])
          ),
        ])
      );
      if (!dryRun) toast(`Categorizzate ${result.updated}`);
    } catch (error) {
      clear(applyBox).append(empty(error.message));
    }
  };

  dryBtn.addEventListener("click", () => runApply(true));
  applyBtn.addEventListener("click", () => runApply(false));

  const applyCard = el("div", { class: "card" }, [
    el("h2", { text: "Applica alle transazioni esistenti" }),
    el("div", { class: "muted" }, [
      "Le categorie scelte a mano non vengono mai sovrascritte.",
    ]),
    el("div", { class: "row", style: "margin-top:12px;gap:8px" }, [dryBtn, applyBtn]),
    applyBox,
  ]);

  // --- giroconti rilevati ---
  const transfersBox = el("div");
  const transfersCard = el("div", { class: "card" }, [
    el("h2", { text: "Giroconti rilevati" }),
    el("div", { class: "muted" }, [
      "Movimenti uguali e opposti su due conti diversi a pochi giorni di distanza: sono gli stessi soldi che si spostano, non spese.",
    ]),
    el("button", {
      class: "small",
      style: "margin-top:10px",
      text: "Cerca",
      onclick: async () => {
        clear(transfersBox).append(spinner("Cerco…"));
        try {
          const pairs = await api.detectTransfers({ window_days: 5 });
          const daMarcare = pairs.filter((p) => !p.already_marked);
          clear(transfersBox);

          if (!pairs.length) {
            transfersBox.append(empty("Nessuna coppia trovata"));
            return;
          }

          transfersBox.append(
            el("div", { class: "muted", style: "margin:10px 0 6px" }, [
              `${pairs.length} coppie · ${daMarcare.length} non ancora marcate`,
            ])
          );

          for (const pair of daMarcare.length ? daMarcare : pairs.slice(0, 8)) {
            transfersBox.append(
              el("div", { class: "row", style: "padding:8px 0;border-top:1px solid var(--border)" }, [
                el("div", { class: "stack" }, [
                  el("span", { class: "truncate", text: `${pair.out.account} → ${pair.in.account}` }),
                  el("span", { class: "muted truncate", text: pair.out.description }),
                  el("span", { class: "muted truncate", text: pair.in.description }),
                ]),
                el("div", { class: "stack", style: "align-items:flex-end" }, [
                  el("span", { class: "amount", text: money(pair.amount) }),
                  el("span", {
                    class: "muted",
                    text: pair.already_marked ? "già marcata" : `${pair.days_apart} gg`,
                  }),
                ]),
              ])
            );
          }

          if (daMarcare.length) {
            transfersBox.append(
              el("div", { class: "muted", style: "margin-top:10px;color:var(--neg);font-size:.78rem" }, [
                "Controlla le descrizioni prima di applicare: due movimenti dello stesso importo a pochi giorni di distanza possono essere una coincidenza, non un giroconto.",
              ]),
              el("button", {
                class: "primary",
                style: "width:100%;margin-top:10px",
                text: `Marca ${daMarcare.length} coppie come trasferimenti`,
                onclick: async () => {
                  const trasf = categories.find((c) => c.exclude_from_stats && c.name === "Trasferimenti");
                  if (!trasf) return toast("Manca la categoria Trasferimenti", true);
                  const ids = daMarcare.flatMap((p) => [p.out.id, p.in.id]);
                  try {
                    const result = await api.applyTransfers({
                      category_id: trasf.id,
                      window_days: 5,
                      transaction_ids: ids,
                    });
                    toast(`Marcati ${result.updated} movimenti`);
                    reload();
                  } catch (error) {
                    toast(error.message, true);
                  }
                },
              })
            );
          }
        } catch (error) {
          clear(transfersBox).append(empty(error.message));
        }
      },
    }),
    transfersBox,
  ]);

  // --- suggerimenti ---
  const suggestionsCard = el("div", { class: "card" }, [
    el("h2", { text: "Ricorrenti senza categoria" }),
    el("div", { class: "muted", style: "margin-bottom:8px" }, [
      "Tocca per precompilare una regola.",
    ]),
    ...(suggestions.length
      ? suggestions.map((s) =>
          el(
            "div",
            {
              class: "row",
              style: "padding:8px 0;cursor:pointer;border-bottom:1px solid var(--border)",
              onclick: () => {
                patternInput.value = s.pattern;
                patternInput.scrollIntoView({ behavior: "smooth", block: "center" });
                patternInput.focus();
              },
            },
            [
              el("div", { class: "stack" }, [
                el("span", { class: "truncate", text: s.pattern }),
                el("span", { class: "muted", text: `${s.count} movimenti` }),
              ]),
              el("span", {
                class: `amount ${Number(s.total) < 0 ? "neg" : "pos"}`,
                text: money(s.total),
              }),
            ]
          )
        )
      : [empty("Tutto categorizzato")]),
  ]);

  // --- elenco regole ---
  const rulesCard = el("div", { class: "card" }, [
    el("h2", { text: `Regole attive (${rules.length})` }),
    ...(rules.length
      ? rules.map((rule) => {
          const category = categories.find((c) => c.id === rule.category_id);
          return el("div", { class: "row", style: "padding:7px 0" }, [
            el("div", { class: "stack" }, [
              el("span", { class: "truncate", text: rule.pattern }),
              el("span", {
                class: "muted",
                text: `${rule.match_type} → ${category ? category.name : "?"} · priorità ${rule.priority}`,
              }),
            ]),
            el("button", {
              class: "small danger",
              text: "×",
              onclick: async () => {
                try {
                  await api.deleteRule(rule.id);
                  toast("Regola eliminata");
                  reload();
                } catch (error) {
                  toast(error.message, true);
                }
              },
            }),
          ]);
        })
      : [empty("Nessuna regola")]),
  ]);

  root.replaceChildren(newCard, applyCard, transfersCard, suggestionsCard, rulesCard);
}
