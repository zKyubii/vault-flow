import { api } from "../api.js";
import { clear, el, empty, formatDate, money, spinner, toast } from "../ui.js";

// Flow: pick file → inspect → pick profile → PREVIEW → save.
// The preview is not a flourish: it is what turns importing into something
// you do willingly instead of holding your breath.

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
      : [el("option", { value: "", text: "— no saved profile —" })]
  );

  // if the profile has an account attached, align the selection
  const syncAccount = () => {
    const profile = profiles.find((p) => String(p.id) === profileSelect.value);
    if (profile && profile.account_id) accountSelect.value = profile.account_id;
  };
  profileSelect.addEventListener("change", syncAccount);

  const previewBtn = el("button", { text: "Preview", disabled: true });
  const commitBtn = el("button", { class: "primary", text: "Import", disabled: true });

  fileInput.addEventListener("change", async () => {
    file = fileInput.files[0] || null;
    clear(previewBox);
    commitBtn.disabled = true;
    previewBtn.disabled = !file;
    if (!file) return clear(inspectBox);

    clear(inspectBox).append(spinner("Reading the file…"));
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
          el("h2", { text: "What the file looks like" }),
          el("div", { class: "muted", style: "margin-bottom:8px" }, [
            `${info.total_lines} lines · encoding ${info.encoding_used} · delimiter "${info.delimiter_guess}"`,
          ]),
          el("div", { class: "muted", style: "margin-bottom:8px" }, [
            info.header_line_guess
              ? `Header guessed at line ${info.header_line_guess} (in blue). It is only a guess: if it is wrong, count the rows to skip yourself.`
              : "Header not detected.",
          ]),
          lines,
        ])
      );
    } catch (error) {
      clear(inspectBox).append(empty(error.message));
    }
  });

  previewBtn.addEventListener("click", async () => {
    if (!file || !profileSelect.value) return toast("A file and a profile are required", true);
    clear(previewBox).append(spinner("Parsing the file…"));
    commitBtn.disabled = true;
    try {
      const result = await api.preview(file, profileSelect.value, accountSelect.value);
      const table = el("table", { class: "preview" }, [
        el("tr", {}, [
          el("th", { text: "Line" }),
          el("th", { text: "Date" }),
          el("th", { text: "Description" }),
          el("th", { text: "Amount" }),
        ]),
        ...result.rows.map((r) =>
          el("tr", { class: r.is_duplicate ? "dup" : "" }, [
            el("td", { text: r.line_no }),
            el("td", { text: formatDate(r.booked_at) }),
            el("td", { text: r.description + (r.is_duplicate ? "  (already imported)" : "") }),
            el("td", {
              class: `amount ${Number(r.amount) < 0 ? "neg" : "pos"}`,
              text: money(r.amount),
            }),
          ])
        ),
      ]);

      clear(previewBox).append(
        el("div", { class: "card" }, [
          el("h2", { text: "Preview — nothing has been written" }),
          el("div", { class: "row" }, [
            el("span", { text: `${result.rows_new} new` }),
            el("span", { class: "muted", text: `${result.rows_duplicate} already imported` }),
          ]),
          el("div", { class: "row", style: "margin-top:4px" }, [
            el("span", { class: "muted", text: `${result.rows_failed} unreadable rows` }),
            el("span", { class: "muted", text: `total ${money(result.total_amount)}` }),
          ]),
          result.date_from
            ? el("div", { class: "muted", style: "margin-top:4px" }, [
                `period ${formatDate(result.date_from)} → ${formatDate(result.date_to)}`,
              ])
            : null,
          result.errors.length
            ? el("div", { class: "muted", style: "margin-top:8px;color:var(--neg)" }, [
                `First unreadable row: ${result.errors[0].line_no} — ${result.errors[0].message}`,
              ])
            : null,
          el("div", { class: "scroll-x", style: "margin-top:10px" }, [table]),
        ])
      );
      commitBtn.disabled = result.rows_new === 0;
      if (result.rows_new === 0) toast("Nothing new to import");
    } catch (error) {
      clear(previewBox).append(empty(error.message));
    }
  });

  commitBtn.addEventListener("click", async () => {
    commitBtn.disabled = true;
    try {
      const run = await api.commit(file, profileSelect.value, accountSelect.value);
      toast(`Imported ${run.rows_imported}, skipped ${run.rows_skipped}`);
      render(clear(root));
    } catch (error) {
      toast(error.message, true);
      commitBtn.disabled = false;
    }
  });

  const runsCard = el("div", { class: "card" }, [
    el("h2", { text: "Previous imports" }),
    ...(runs.length
      ? runs.slice(0, 10).map((run) =>
          el("div", { class: "row", style: "padding:7px 0" }, [
            el("div", { class: "stack" }, [
              el("span", { class: "truncate", text: run.filename }),
              el("span", {
                class: "muted",
                text: `${run.rows_imported} imported · ${run.rows_skipped} skipped · ${run.status}`,
              }),
            ]),
            run.status === "completed" && run.rows_imported > 0
              ? el("button", {
                  class: "small danger",
                  text: "Undo",
                  onclick: async () => {
                    if (!confirm(`Undo the import of ${run.filename}? ${run.rows_imported} transactions will be removed.`))
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
      : [empty("No imports yet")]),
  ]);

  root.replaceChildren(
    el("div", { class: "card" }, [
      el("h2", { text: "1 · Choose the CSV file" }),
      fileInput,
      el("div", { class: "field-row", style: "margin-top:8px" }, [
        el("div", {}, [el("label", { text: "Profile" }), profileSelect]),
        el("div", {}, [el("label", { text: "Account" }), accountSelect]),
      ]),
      el("div", { class: "row equal", style: "margin-top:14px;gap:8px" }, [previewBtn, commitBtn]),
    ]),
    inspectBox,
    previewBox,
    runsCard
  );

  syncAccount();
}
