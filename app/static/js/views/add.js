import { api } from "../api.js";
import { el, empty, money, spinner, toast, todayISO } from "../ui.js";

// Serve per il contante, che non lascia traccia digitale da importare.
// Deve essere velocissimo: importo, due parole, fatto. Un form lungo
// garantisce che dopo tre giorni non lo usi più.

const QUICK = [5, 10, 20, 50];

export async function render(root) {
  root.append(spinner());

  let accounts;
  let categories;
  try {
    [accounts, categories] = await Promise.all([api.accounts(), api.categories()]);
  } catch (error) {
    root.replaceChildren(empty(error.message));
    return;
  }

  const amountInput = el("input", {
    type: "number",
    step: "0.01",
    inputmode: "decimal",
    placeholder: "0.00",
    style: "font-size:1.6rem;text-align:center;min-height:64px",
  });

  const descInput = el("input", { type: "text", placeholder: "e.g. coffee at the bar" });
  const dateInput = el("input", { type: "date", value: todayISO() });

  const accountSelect = el(
    "select",
    {},
    accounts.map((a) =>
      el("option", {
        value: a.id,
        text: a.name,
        // il contante è il caso d'uso principale qui
        selected: a.type === "cash",
      })
    )
  );

  const categorySelect = el("select", {}, [
    el("option", { value: "", text: "— none —" }),
    ...categories.map((c) => {
      const parent = c.parent_id ? categories.find((p) => p.id === c.parent_id) : null;
      return el("option", {
        value: c.id,
        text: parent ? `${parent.name} › ${c.name}` : c.name,
      });
    }),
  ]);

  // Uscita di default: quasi tutto quello che si scrive a mano è una spesa.
  let isExpense = true;
  const expenseBtn = el("button", { class: "chip active", text: "Expense" });
  const incomeBtn = el("button", { class: "chip", text: "Income" });
  const setDirection = (expense) => {
    isExpense = expense;
    expenseBtn.className = `chip${expense ? " active" : ""}`;
    incomeBtn.className = `chip${expense ? "" : " active"}`;
  };
  expenseBtn.addEventListener("click", () => setDirection(true));
  incomeBtn.addEventListener("click", () => setDirection(false));

  const quickButtons = el(
    "div",
    { class: "chips", style: "margin-top:10px;justify-content:center" },
    QUICK.map((value) =>
      el("button", {
        class: "chip",
        text: money(value),
        onclick: () => {
          amountInput.value = value.toFixed(2);
          descInput.focus();
        },
      })
    )
  );

  const submit = el("button", {
    class: "primary",
    text: "Save",
    style: "width:100%;margin-top:16px",
  });

  submit.addEventListener("click", async () => {
    const raw = Number(String(amountInput.value).replace(",", "."));
    if (!raw || Number.isNaN(raw)) return toast("Enter an amount", true);
    if (!descInput.value.trim()) return toast("Enter a description", true);

    submit.disabled = true;
    try {
      await api.createTransaction({
        account_id: Number(accountSelect.value),
        booked_at: dateInput.value,
        amount: isExpense ? -Math.abs(raw) : Math.abs(raw),
        description: descInput.value.trim(),
        category_id: categorySelect.value ? Number(categorySelect.value) : null,
      });
      toast("Saved");
      amountInput.value = "";
      descInput.value = "";
      amountInput.focus();
    } catch (error) {
      toast(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  root.replaceChildren(
    el("div", { class: "card" }, [
      amountInput,
      quickButtons,
      el("div", { class: "chips", style: "margin-top:12px;justify-content:center" }, [
        expenseBtn,
        incomeBtn,
      ]),
      el("label", { text: "Description" }),
      descInput,
      el("div", { class: "field-row" }, [
        el("div", {}, [el("label", { text: "Date" }), dateInput]),
        el("div", {}, [el("label", { text: "Account" }), accountSelect]),
      ]),
      el("label", { text: "Category (optional)" }),
      categorySelect,
      submit,
    ]),
    el("div", { class: "muted", style: "text-align:center" }, [
      "Manual entry is for cash. Anything paid by card comes in through CSV import.",
    ])
  );

  amountInput.focus();
}
