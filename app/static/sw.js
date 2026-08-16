// Service worker.
//
// Strategia: **rete per prima, cache come rete di scorta**, per tutto.
//
// La versione precedente serviva il guscio dell'app dalla cache e aggiornava
// in sottofondo. Sembra più veloce, ma dopo ogni aggiornamento il primo
// avvio esegue il codice vecchio contro un'API cambiata: è così che, appena
// introdotto il login, l'app ha mostrato "Autenticazione richiesta" invece
// della schermata di accesso — il JavaScript in cache non sapeva che il
// login esistesse.
//
// Su un'app self-hosted (rete locale o VPS propria) il costo di chiedere
// sempre al server è di pochi millisecondi. La correttezza vale molto di
// più. Se la rete non risponde entro NETWORK_TIMEOUT si usa la cache, quindi
// l'uso offline resta intatto: è il requisito "consulto i dati già
// sincronizzati senza connessione".
//
// Le scritture (POST/PUT/DELETE) non vengono mai messe in coda: fallire
// subito con un messaggio chiaro è più onesto che far credere all'utente di
// aver salvato qualcosa che potrebbe non arrivare mai.

const VERSION = "v4";
const SHELL_CACHE = `spese-shell-${VERSION}`;
const DATA_CACHE = `spese-data-${VERSION}`;
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
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      // addAll fallisce in blocco se manca un file: si preferisce installare
      // comunque ciò che c'è
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
        // le schede già aperte stanno eseguendo il codice vecchio: si
        // ricaricano da sole invece di restare in uno stato incoerente
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

    // navigazione senza rete e senza copia della pagina: si ripiega sul guscio
    if (request.mode === "navigate") {
      const shell = await caches.match("/static/index.html");
      if (shell) return shell;
    }
    if (request.url.includes("/api/")) {
      return new Response(
        JSON.stringify({ detail: "Sei offline e questi dati non sono ancora stati scaricati" }),
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
