import { api } from "../api.js";
import { clear, el, empty, formatDate, money, spinner, toast } from "../ui.js";

// Flusso: scegli file → ispeziona → scegli profilo → ANTEPRIMA → salva.
// L'anteprima non è un vezzo: è ciò che rende l'import una cosa che fai
// volentieri invece che col fiato sospeso.

let file = null;

export async function render(root) {
  root.append(spinner());

  let accounts;
  let profiles;
  let runs;
  try {
    [accounts, profiles, runs] = await Promise.all([api.accounts(), api.profiles(), api.runs()]);
  } catch (error) {
    root.replaceChildren(empty(error.message));
    return;
  }

  const fileInput = el("input", { type: "file", accept: ".csv,.txt,text/csv" });
  const inspectBox = el("div");
  const previewBox = el("div");

  const accountSelect = el(
    "select",
    {},
    accounts.map((a) => el("option", { value: a.id, text: a.name }))
  );
  const profileSelect = el(
    "select",
    {},
    profiles.length
      ? profiles.map((p) => el("option", { value: p.id, text: p.name }))
      : [el("option", { value: "", text: "— nessun profilo salvato —" })]
  );

  // se il profilo ha un conto associato, si allinea la selezione
  const syncAccount = () => {
    const profile = profiles.find((p) => String(p.id) === profileSelect.value);
    if (profile && profile.account_id) accountSelect.value = profile.account_id;
  };
  profileSelect.addEventListener("change", syncAccount);

  const previewBtn = el("button", { text: "Anteprima", disabled: true });
  const commitBtn = el("button", { class: "primary", text: "Importa", disabled: true });

  fileInput.addEventListener("change", async () => {
    file = fileInput.files[0] || null;
    clear(previewBox);
    commitBtn.disabled = true;
    previewBtn.disabled = !file;
    if (!file) return clear(inspectBox);

    clear(inspectBox).append(spinner("Leggo il file…"));
    try {
      const info = await api.inspect(file);
      const lines = el("pre", { class: "filelines" });
      for (const line of info.lines) {
        const isHeader = line.number === info.header_line_guess;
        lines.append(
          isHeader
            ? el("b", { text: `${String(line.number).padStart(3)} │ ${line.text}\n` })
            : document.createTextNode(`${String(line.number).padStart(3)} │ ${line.text}\n`)
        );
      }
      clear(inspectBox).append(
        el("div", { class: "card" }, [
          el("h2", { text: "Com'è fatto il file" }),
          el("div", { class: "muted", style: "margin-bottom:8px" }, [
            `${info.total_lines} righe · encoding ${info.encoding_used} · delimitatore "${info.delimiter_guess}"`,
          ]),
          el("div", { class: "muted", style: "margin-bottom:8px" }, [
            info.header_line_guess
              ? `Intestazione ipotizzata alla riga ${info.header_line_guess} (in blu). È solo un suggerimento: se è sbagliata, conta le righe da saltare a mano.`
              : "Intestazione non individuata.",
          ]),
          lines,
        ])
      );
    } catch (error) {
      clear(inspectBox).append(empty(error.message));
    }
  });

  previewBtn.addEventListener("click", async () => {
    if (!file || !profileSelect.value) return toast("Serve un file e un profilo", true);
    clear(previewBox).append(spinner("Interpreto il file…"));
    commitBtn.disabled = true;
    try {
      const result = await api.preview(file, profileSelect.value, accountSelect.value);
      const table = el("table", { class: "preview" }, [
        el("tr", {}, [
          el("th", { text: "Riga" }),
          el("th", { text: "Data" }),
          el("th", { text: "Descrizione" }),
          el("th", { text: "Importo" }),
        ]),
        ...result.rows.map((r) =>
          el("tr", { class: r.is_duplicate ? "dup" : "" }, [
            el("td", { text: r.line_no }),
            el("td", { text: formatDate(r.booked_at) }),
            el("td", { text: r.description + (r.is_duplicate ? "  (già presente)" : "") }),
            el("td", {
              class: `amount ${Number(r.amount) < 0 ? "neg" : "pos"}`,
              text: money(r.amount),
            }),
          ])
        ),
      ]);

      clear(previewBox).append(
        el("div", { class: "card" }, [
          el("h2", { text: "Anteprima — non è stato scritto nulla" }),
          el("div", { class: "row" }, [
            el("span", { text: `${result.rows_new} nuove` }),
            el("span", { class: "muted", text: `${result.rows_duplicate} già presenti` }),
          ]),
          el("div", { class: "row", style: "margin-top:4px" }, [
            el("span", { class: "muted", text: `${result.rows_failed} righe illeggibili` }),
            el("span", { class: "muted", text: `totale ${money(result.total_amount)}` }),
          ]),
          result.date_from
            ? el("div", { class: "muted", style: "margin-top:4px" }, [
                `periodo ${formatDate(result.date_from)} → ${formatDate(result.date_to)}`,
              ])
            : null,
          result.errors.length
            ? el("div", { class: "muted", style: "margin-top:8px;color:var(--neg)" }, [
                `Prima riga illeggibile: ${result.errors[0].line_no} — ${result.errors[0].message}`,
              ])
            : null,
          el("div", { class: "scroll-x", style: "margin-top:10px" }, [table]),
        ])
      );
      commitBtn.disabled = result.rows_new === 0;
      if (result.rows_new === 0) toast("Niente di nuovo da importare");
    } catch (error) {
      clear(previewBox).append(empty(error.message));
    }
  });

  commitBtn.addEventListener("click", async () => {
    commitBtn.disabled = true;
    try {
      const run = await api.commit(file, profileSelect.value, accountSelect.value);
      toast(`Importate ${run.rows_imported}, saltate ${run.rows_skipped}`);
      render(clear(root));
    } catch (error) {
      toast(error.message, true);
      commitBtn.disabled = false;
    }
  });

  const runsCard = el("div", { class: "card" }, [
    el("h2", { text: "Import precedenti" }),
    ...(runs.length
      ? runs.slice(0, 10).map((run) =>
          el("div", { class: "row", style: "padding:7px 0" }, [
            el("div", { class: "stack" }, [
              el("span", { class: "truncate", text: run.filename }),
              el("span", {
                class: "muted",
                text: `${run.rows_imported} importate · ${run.rows_skipped} saltate · ${run.status}`,
              }),
            ]),
            run.status === "completed" && run.rows_imported > 0
              ? el("button", {
                  class: "small danger",
                  text: "Annulla",
                  onclick: async () => {
                    if (!confirm(`Annullare l'import di ${run.filename}? Verranno rimosse ${run.rows_imported} transazioni.`))
                      return;
                    try {
                      const result = await api.revertRun(run.id);
                      toast(result.detail);
                      render(clear(root));
                    } catch (error) {
                      toast(error.message, true);
                    }
                  },
                })
              : null,
          ])
        )
      : [empty("Nessun import ancora")]),
  ]);

  root.replaceChildren(
    el("div", { class: "card" }, [
      el("h2", { text: "1 · Scegli il file CSV" }),
      fileInput,
      el("div", { class: "field-row", style: "margin-top:8px" }, [
        el("div", {}, [el("label", { text: "Profilo" }), profileSelect]),
        el("div", {}, [el("label", { text: "Conto" }), accountSelect]),
      ]),
      el("div", { class: "row", style: "margin-top:14px;gap:8px" }, [previewBtn, commitBtn]),
    ]),
    inspectBox,
    previewBox,
    runsCard
  );

  syncAccount();
}
