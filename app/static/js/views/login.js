import { api } from "../api.js";
import { el, toast } from "../ui.js";

/**
 * Sign-in screen.
 *
 * Not a view like the others: it replaces the whole interface until you are
 * in, and hides the title bar and navigation — showing the tabs to someone
 * who is not authenticated would only make all of them fail.
 */
export function showLogin(status = {}) {
  document.body.classList.add("locked");

  const passwordInput = el("input", {
    type: "password",
    placeholder: "Password",
    autocomplete: "current-password",
    // installed PWAs reopen here: a focused field saves one tap
    autofocus: true,
  });

  const button = el("button", { class: "primary", text: "Sign in", style: "width:100%" });
  const errorBox = el("div", { class: "login-error", hidden: true });

  const attempt = async () => {
    const password = passwordInput.value;
    if (!password) return;
    button.disabled = true;
    errorBox.hidden = true;
    try {
      await api.login(password);
      // clean reload: every view restarts with a valid cookie
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
    el("h2", { class: "login-title", text: "Vault Flow" }),
    notConfigured
      ? el("div", { class: "login-error", style: "margin-bottom:0" }, [
          "No password configured. Open the .env file, set APP_PASSWORD, " +
            "then restart with: docker compose restart app",
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
