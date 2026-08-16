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

  // --- new rule ---
  const patternInput = el("input", { type: "text", placeholder: "e.g. spotify" });
  const catSelect = el("select", {}, categoryOptions(null));
  const matchSelect = el("select", {}, [
    el("option", { value: "contains", text: "contains" }),
    el("option", { value: "starts_with", text: "starts with" }),
    el("option", { value: "exact", text: "is exactly" }),
    el("option", { value: "regex", text: "regular expression" }),
  ]);
  const priorityInput = el("input", { type: "number", value: 100, min: 0 });

  const createBtn = el("button", { class: "primary", text: "Create rule", style: "width:100%;margin-top:12px" });
  createBtn.addEventListener("click", async () => {
    if (!patternInput.value.trim()) return toast("Type what to look for", true);
    try {
      await api.createRule({
        pattern: patternInput.value.trim(),
        category_id: Number(catSelect.value),
        match_type: matchSelect.value,
        priority: Number(priorityInput.value),
      });
      toast("Rule created");
      reload();
    } catch (error) {
      toast(error.message, true);
    }
  });

  const newCard = el("div", { class: "card" }, [
    el("h2", { text: "New rule" }),
    el("label", { text: "If the description…" }),
    matchSelect,
    patternInput,
    el("div", { class: "field-row" }, [
      el("div", {}, [el("label", { text: "Category" }), catSelect]),
      el("div", {}, [el("label", { text: "Priority (lower wins)" }), priorityInput]),
    ]),
    createBtn,
  ]);

  // --- apply ---
  const applyBox = el("div");
  const dryBtn = el("button", { text: "Dry run" });
  const applyBtn = el("button", { class: "primary", text: "Apply" });

  const runApply = async (dryRun) => {
    clear(applyBox).append(spinner());
    try {
      const result = await api.applyRules({ only_uncategorized: true, dry_run: dryRun });
      const entries = Object.entries(result.by_category).sort((a, b) => b[1] - a[1]);
      clear(applyBox).append(
        el("div", { style: "margin-top:10px" }, [
          el("div", {
            text: dryRun
              ? `Dry run: ${result.updated} transactions would be categorised (nothing was written)`
              : `Categorised ${result.updated} transactions`,
          }),
          result.protected
            ? el("div", { class: "muted", style: "margin-top:4px" }, [
                `${result.protected} left alone because they were set by hand`,
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
      if (!dryRun) toast(`Categorised ${result.updated}`);
    } catch (error) {
      clear(applyBox).append(empty(error.message));
    }
  };

  dryBtn.addEventListener("click", () => runApply(true));
  applyBtn.addEventListener("click", () => runApply(false));

  const applyCard = el("div", { class: "card" }, [
    el("h2", { text: "Apply to existing transactions" }),
    el("div", { class: "muted" }, [
      "Categories set by hand are never overwritten.",
    ]),
    el("div", { class: "row", style: "margin-top:12px;gap:8px" }, [dryBtn, applyBtn]),
    applyBox,
  ]);

  // --- detected transfers ---
  const transfersBox = el("div");
  const transfersCard = el("div", { class: "card" }, [
    el("h2", { text: "Detected transfers" }),
    el("div", { class: "muted" }, [
      "Equal and opposite amounts on two different accounts a few days apart: the same money moving, not spending.",
    ]),
    el("button", {
      class: "small",
      style: "margin-top:10px",
      text: "Scan",
      onclick: async () => {
        clear(transfersBox).append(spinner("Scanning…"));
        try {
          const pairs = await api.detectTransfers({ window_days: 5 });
          const toMark = pairs.filter((p) => !p.already_marked);
          clear(transfersBox);

          if (!pairs.length) {
            transfersBox.append(empty("No pairs found"));
            return;
          }

          transfersBox.append(
            el("div", { class: "muted", style: "margin:10px 0 6px" }, [
              `${pairs.length} pairs · ${toMark.length} not yet marked`,
            ])
          );

          for (const pair of toMark.length ? toMark : pairs.slice(0, 8)) {
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
                    text: pair.already_marked ? "already marked" : `${pair.days_apart} days apart`,
                  }),
                ]),
              ])
            );
          }

          if (toMark.length) {
            transfersBox.append(
              el("div", { class: "muted", style: "margin-top:10px;color:var(--neg);font-size:.78rem" }, [
                "Check the descriptions before applying: two transactions of the same amount a few days apart can be a coincidence, not a transfer.",
              ]),
              el("button", {
                class: "primary",
                style: "width:100%;margin-top:10px",
                text: `Mark ${toMark.length} pairs as transfers`,
                onclick: async () => {
                  // Matched by **behaviour**, not by name: the category can be
                  // in any language or renamed by the user. What matters is
                  // that it is excluded from the totals.
                  const transferCat =
                    categories.find((c) => c.exclude_from_stats && /transfer|girocont/i.test(c.name)) ||
                    categories.find((c) => c.exclude_from_stats);
                  if (!transferCat) {
                    return toast("No category excluded from statistics found", true);
                  }
                  const ids = toMark.flatMap((p) => [p.out.id, p.in.id]);
                  try {
                    const result = await api.applyTransfers({
                      category_id: transferCat.id,
                      window_days: 5,
                      transaction_ids: ids,
                    });
                    toast(`Marked ${result.updated} transactions`);
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

  // --- suggestions ---
  const suggestionsCard = el("div", { class: "card" }, [
    el("h2", { text: "Recurring and uncategorised" }),
    el("div", { class: "muted", style: "margin-bottom:8px" }, [
      "Tap to prefill a rule.",
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
                el("span", { class: "muted", text: `${s.count} transactions` }),
              ]),
              el("span", {
                class: `amount ${Number(s.total) < 0 ? "neg" : "pos"}`,
                text: money(s.total),
              }),
            ]
          )
        )
      : [empty("Everything is categorised")]),
  ]);

  // --- rule list ---
  const rulesCard = el("div", { class: "card" }, [
    el("h2", { text: `Active rules (${rules.length})` }),
    ...(rules.length
      ? rules.map((rule) => {
          const category = categories.find((c) => c.id === rule.category_id);
          return el("div", { class: "row", style: "padding:7px 0" }, [
            el("div", { class: "stack" }, [
              el("span", { class: "truncate", text: rule.pattern }),
              el("span", {
                class: "muted",
                text: `${rule.match_type} → ${category ? category.name : "?"} · priority ${rule.priority}`,
              }),
            ]),
            el("button", {
              class: "small danger",
              text: "×",
              onclick: async () => {
                try {
                  await api.deleteRule(rule.id);
                  toast("Rule deleted");
                  reload();
                } catch (error) {
                  toast(error.message, true);
                }
              },
            }),
          ]);
        })
      : [empty("No rules yet")]),
  ]);

  root.replaceChildren(newCard, applyCard, transfersCard, suggestionsCard, rulesCard);
}
