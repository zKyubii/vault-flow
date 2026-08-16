// Minimal hash-based router. No framework: the app has five screens and a
// small amount of state, so a build toolchain would be dead weight.

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
      // Reload even if the call failed: the cookie may already be expired,
      // and being stuck on a dead screen is worse.
      location.reload();
    }
  });
  actions.replaceChildren(button);
}

/**
 * Before mounting any view we ask the server whether we are signed in.
 * If the server cannot be reached (offline) we start anyway: the service
 * worker can serve already-downloaded data, which is exactly the
 * "check my spending without a connection" use case.
 */
async function bootstrap() {
  let status = null;
  try {
    status = await api.me();
  } catch (error) {
    // A 401 should not happen here (/auth/me is public), but if the server
    // returns one anyway the right answer is the login screen, not an error
    // page. For anything else (offline) we try to start regardless.
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

// A session that expires while the app is open must not leave blank screens
// with no explanation: we go back to the login.
window.addEventListener("vaultflow:unauthorized", () => {
  if (document.body.classList.contains("locked")) return;
  document.getElementById("view").replaceChildren();
  showLogin({ password_configured: true });
});

// --- connection state ------------------------------------------------------

const offlineBar = document.getElementById("offline-bar");
const updateOnlineState = () => (offlineBar.hidden = navigator.onLine);
window.addEventListener("online", updateOnlineState);
window.addEventListener("offline", updateOnlineState);
updateOnlineState();

// --- service worker --------------------------------------------------------

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    // Served from the root: from /static it could not control "/".
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
      /* without a service worker the app still works, just not offline */
    });
  });

  // When a new version takes over, the open tab is still running the old
  // code: reload once. Without this you end up with stale JavaScript against
  // a changed API — which is how the login screen, right after it was added,
  // showed "Authentication required" instead of the sign-in form.
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
