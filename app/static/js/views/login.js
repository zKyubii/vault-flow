import { api } from "../api.js";
import { el, toast } from "../ui.js";

/**
 * Schermata di accesso.
 *
 * Non è una vista come le altre: sostituisce tutta l'interfaccia finché non
 * si è dentro, e nasconde barra dei titoli e navigazione — mostrare le
 * schede a chi non è autenticato significherebbe solo farle fallire tutte.
 */
export function showLogin(status = {}) {
  document.body.classList.add("locked");

  const passwordInput = el("input", {
    type: "password",
    placeholder: "Password",
    autocomplete: "current-password",
    // le PWA installate riaprono qui: il campo pronto evita un tocco
    autofocus: true,
  });

  const button = el("button", { class: "primary", text: "Entra", style: "width:100%" });
  const errorBox = el("div", { class: "login-error", hidden: true });

  const attempt = async () => {
    const password = passwordInput.value;
    if (!password) return;
    button.disabled = true;
    errorBox.hidden = true;
    try {
      await api.login(password);
      // ricarica pulita: ogni vista riparte con il cookie valido
      location.reload();
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
      passwordInput.value = "";
      passwordInput.focus();
      button.disabled = false;
    }
  };

  button.addEventListener("click", attempt);
  passwordInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") attempt();
  });

  const notConfigured = status.password_configured === false;

  const panel = el("div", { class: "login-card" }, [
    el("div", { class: "login-logo" }, [
      el("img", { src: "/static/icons/icon.svg", alt: "", width: 64, height: 64 }),
    ]),
    el("h2", { class: "login-title", text: "Dashboard Spese" }),
    notConfigured
      ? el("div", { class: "login-error", style: "margin-bottom:0" }, [
          "Nessuna password impostata. Apri il file .env, cambia APP_PASSWORD, " +
            "poi riavvia con: docker compose restart app",
        ])
      : el("div", {}, [
          el("label", { text: "Password" }),
          passwordInput,
          errorBox,
          el("div", { style: "height:14px" }),
          button,
        ]),
  ]);

  const screen = el("div", { class: "login-screen" }, [panel]);
  document.body.append(screen);
  setTimeout(() => passwordInput.focus(), 60);
}
