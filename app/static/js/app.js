// Router minimale basato su hash. Niente framework: l'app ha cinque
// schermate e uno stato piccolo, una dipendenza di build sarebbe peso morto.

import { api } from "./api.js";
import { clear } from "./ui.js";
import { showLogin } from "./views/login.js";
import * as summary from "./views/summary.js";
import * as transactions from "./views/transactions.js";
import * as add from "./views/add.js";
import * as importer from "./views/importer.js";
import * as rules from "./views/rules.js";

const ROUTES = {
  "/": { title: "Summary", view: summary },
  "/transactions": { title: "Transactions", view: transactions },
  "/add": { title: "Add", view: add },
  "/import": { title: "Import", view: importer },
  "/rules": { title: "Rules", view: rules },
};

const viewRoot = document.getElementById("view");
const pageTitle = document.getElementById("page-title");

function currentPath() {
  const hash = location.hash.replace(/^#/, "");
  return ROUTES[hash] ? hash : "/";
}

async function router() {
  const path = currentPath();
  const route = ROUTES[path];

  pageTitle.textContent = route.title;
  for (const link of document.querySelectorAll(".tabbar a")) {
    link.classList.toggle("active", link.dataset.tab === path);
  }

  clear(viewRoot);
  window.scrollTo(0, 0);
  try {
    await route.view.render(viewRoot);
  } catch (error) {
    clear(viewRoot).append(
      Object.assign(document.createElement("div"), {
        className: "empty",
        textContent: error.message || "Unexpected error",
      })
    );
  }
}

window.addEventListener("hashchange", router);

/**
 * Prima di montare qualsiasi vista si chiede al server se siamo dentro.
 * Se il server non risponde (offline) si prova comunque a partire: il
 * service worker può servire i dati già scaricati, ed è esattamente il caso
 * d'uso "consulto le spese senza connessione".
 */
function mountLogout(displayName) {
  const actions = document.getElementById("page-actions");
  const button = document.createElement("button");
  button.className = "small";
  button.title = displayName ? `Signed in as ${displayName} — sign out` : "Sign out";
  button.setAttribute("aria-label", "Sign out");
  button.textContent = "Sign out";
  button.addEventListener("click", async () => {
    if (!confirm("Sign out of the dashboard?")) return;
    try {
      await api.logout();
    } finally {
      // anche se la chiamata fallisce si ricarica: il cookie potrebbe già
      // essere scaduto, e restare su una schermata morta è peggio
      location.reload();
    }
  });
  actions.replaceChildren(button);
}

async function bootstrap() {
  let status = null;
  try {
    status = await api.me();
  } catch (error) {
    // Una 401 qui non dovrebbe capitare (/auth/me è pubblica), ma se il
    // server la restituisce comunque la risposta giusta è il login, non una
    // schermata di errore. Per qualsiasi altro problema (offline) si prova a
    // partire lo stesso: il service worker può servire i dati già scaricati.
    if (error && error.status === 401) {
      showLogin({ password_configured: true });
      return;
    }
    router();
    return;
  }

  if (!status.authenticated) {
    showLogin(status);
    return;
  }
  mountLogout(status.display_name);
  router();
}

window.addEventListener("DOMContentLoaded", bootstrap);

// Una sessione scaduta mentre l'app è aperta non deve lasciare schermate
// vuote senza spiegazione: si torna al login.
window.addEventListener("spese:unauthorized", () => {
  if (document.body.classList.contains("locked")) return;
  document.getElementById("view").replaceChildren();
  showLogin({ password_configured: true });
});

// --- stato della connessione -----------------------------------------------

const offlineBar = document.getElementById("offline-bar");
const updateOnlineState = () => (offlineBar.hidden = navigator.onLine);
window.addEventListener("online", updateOnlineState);
window.addEventListener("offline", updateOnlineState);
updateOnlineState();

// --- service worker --------------------------------------------------------

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    // servito dalla radice: da /static non potrebbe controllare "/"
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* senza service worker l'app funziona lo stesso, solo non offline */
    });
  });

  // Quando entra in servizio una versione nuova, la scheda aperta sta ancora
  // eseguendo il codice vecchio: si ricarica una volta sola. Senza questo,
  // dopo un aggiornamento si resta con JavaScript vecchio contro un'API
  // cambiata — ed è così che il login, appena introdotto, mostrava
  // "Autenticazione richiesta" invece della schermata di accesso.
  let reloading = false;
  const reloadOnce = () => {
    if (reloading) return;
    reloading = true;
    location.reload();
  };
  navigator.serviceWorker.addEventListener("message", (event) => {
    if (event.data && event.data.type === "sw-updated") reloadOnce();
  });
  navigator.serviceWorker.addEventListener("controllerchange", reloadOnce);
}
