// Service worker.
//
// Strategy: **network first, cache as a fallback**, for everything.
//
// The previous version served the app shell from cache and refreshed in the
// background. It looks faster, but after every update the first load runs the
// old code against a changed API: that is how, right after login was added,
// the app showed "Authentication required" instead of the sign-in screen —
// the cached JavaScript had no idea login existed.
//
// On a self-hosted app (home network or your own VPS) always asking the
// server costs a few milliseconds. Correctness is worth far more. If the
// network does not answer within NETWORK_TIMEOUT we fall back to the cache,
// so offline use is intact: that is the "read already-synced data without a
// connection" requirement.
//
// Writes (POST/PUT/DELETE) are never queued: failing immediately with a clear
// message is more honest than letting someone believe they saved something
// that may never arrive.

const VERSION = "v6";
const SHELL_CACHE = `vaultflow-shell-${VERSION}`;
const DATA_CACHE = `vaultflow-data-${VERSION}`;
const NETWORK_TIMEOUT = 2500;

const SHELL = [
  "/",
  "/static/index.html",
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/js/api.js",
  "/static/js/ui.js",
  "/static/js/filters.js",
  "/static/js/views/login.js",
  "/static/js/views/summary.js",
  "/static/js/views/transactions.js",
  "/static/js/views/add.js",
  "/static/js/views/importer.js",
  "/static/js/views/rules.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon.svg",
  "/static/icons/apple-touch-icon.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/icon-maskable-512.png",
  "/static/icons/favicon-32.png",
  // Without these the app falls back to the system font as soon as it is
  // offline — the one place where a self-hosted font would be most obvious.
  "/static/fonts/space-grotesk-latin.woff2",
  "/static/fonts/space-grotesk-latin-ext.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      // addAll fails as a whole if a single file is missing: we prefer to
      // install whatever is available
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key !== SHELL_CACHE && key !== DATA_CACHE)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
      .then(() => self.clients.matchAll({ type: "window" }))
      .then((clients) => {
        // Tabs that are already open are still running the old code: they
        // reload themselves rather than sit in an inconsistent state.
        for (const client of clients) client.postMessage({ type: "sw-updated" });
      })
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const isApi = url.pathname.startsWith("/api/");
  event.respondWith(networkFirst(request, isApi ? DATA_CACHE : SHELL_CACHE));
});

async function networkFirst(request, cacheName) {
  try {
    const response = await withTimeout(fetch(request), NETWORK_TIMEOUT);
    if (response && response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;

    // Navigating with no network and no cached copy of the page: fall back
    // to the app shell.
    if (request.mode === "navigate") {
      const shell = await caches.match("/static/index.html");
      if (shell) return shell;
    }
    if (request.url.includes("/api/")) {
      return new Response(
        JSON.stringify({ detail: "You are offline and this data has not been downloaded yet" }),
        { status: 503, headers: { "Content-Type": "application/json" } }
      );
    }
    return new Response("Offline", { status: 503 });
  }
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      }
    );
  });
}
