// Accesso all'API. Un solo posto che parla col backend.

const BASE = "/api";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(BASE + path, options);
  } catch {
    // fetch fallisce solo se la rete non c'è: il service worker serve già
    // le GET dalla cache, quindi qui ci si arriva soprattutto in scrittura.
    throw new ApiError("Sei offline: questa operazione richiede connessione", 0);
  }

  if (response.status === 401 && !path.startsWith("/auth/")) {
    // sessione scaduta o cookie assente: lo segnala una volta sola a chi sa
    // cosa farne, invece di far fallire ogni vista con un errore criptico
    window.dispatchEvent(new CustomEvent("spese:unauthorized"));
    throw new ApiError("Sessione scaduta: rifai l'accesso", 401);
  }

  if (!response.ok) {
    let detail = `Errore ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* risposta non JSON: si tiene il messaggio generico */
    }
    throw new ApiError(detail, response.status);
  }

  return response.status === 204 ? null : response.json();
}

const json = (method, path, body) =>
  request(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

// Accetta sia un oggetto semplice sia URLSearchParams: i filtri con conti e
// categorie multipli ripetono la stessa chiave, cosa che un oggetto non può
// rappresentare.
const qs = (params) => {
  if (params instanceof URLSearchParams) {
    const string = params.toString();
    return string ? `?${string}` : "";
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const string = search.toString();
  return string ? `?${string}` : "";
};

export const api = {
  // accesso
  me: () => request("/auth/me"),
  login: (password) => json("POST", "/auth/login", { password }),
  logout: () => json("POST", "/auth/logout", {}),
  logoutEverywhere: () => json("POST", "/auth/logout-everywhere", {}),

  // riepilogo
  summary: (p) => request(`/stats/summary${qs(p)}`),
  months: (p) => request(`/stats/months${qs(p)}`),
  top: (p) => request(`/stats/top${qs(p)}`),
  balances: () => request("/stats/balances"),

  // anagrafiche
  accounts: () => request("/accounts"),
  createAccount: (body) => json("POST", "/accounts", body),
  updateAccount: (id, body) => json("PATCH", `/accounts/${id}`, body),
  deleteAccount: (id) => request(`/accounts/${id}`, { method: "DELETE" }),
  categories: () => request("/categories"),
  createCategory: (body) => json("POST", "/categories", body),
  updateCategory: (id, body) => json("PATCH", `/categories/${id}`, body),
  deleteCategory: (id) => request(`/categories/${id}`, { method: "DELETE" }),

  // movimenti
  transactions: (p) => request(`/transactions${qs(p)}`),
  createTransaction: (body) => json("POST", "/transactions", body),
  setCategory: (id, categoryId) =>
    json("PUT", `/transactions/${id}/category`, { category_id: categoryId }),
  deleteTransaction: (id) => request(`/transactions/${id}`, { method: "DELETE" }),

  // regole
  rules: () => request("/rules"),
  createRule: (body) => json("POST", "/rules", body),
  deleteRule: (id) => request(`/rules/${id}`, { method: "DELETE" }),
  applyRules: (body) => json("POST", "/rules/apply", body),
  suggestions: (p) => request(`/rules/suggestions${qs(p)}`),
  similar: (id) => request(`/transactions/${id}/similar`),
  ruleFromTransaction: (body) => json("POST", "/rules/from-transaction", body),

  // rilevamenti
  subscriptions: (p) => request(`/detect/subscriptions${qs(p)}`),
  detectTransfers: (p) => request(`/detect/transfers${qs(p)}`),
  applyTransfers: (body) => json("POST", "/detect/transfers/apply", body),

  // import
  profiles: () => request("/import-profiles"),
  createProfile: (body) => json("POST", "/import-profiles", body),
  deleteProfile: (id) => request(`/import-profiles/${id}`, { method: "DELETE" }),
  runs: () => request("/imports"),
  revertRun: (id) => request(`/imports/${id}/revert`, { method: "POST" }),

  inspect: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/imports/inspect", { method: "POST", body: form });
  },
  preview: (file, profileId, accountId, limit = 60) => {
    const form = new FormData();
    form.append("file", file);
    form.append("profile_id", profileId);
    form.append("account_id", accountId);
    form.append("limit", limit);
    return request("/imports/preview", { method: "POST", body: form });
  },
  commit: (file, profileId, accountId) => {
    const form = new FormData();
    form.append("file", file);
    form.append("profile_id", profileId);
    form.append("account_id", accountId);
    return request("/imports/commit", { method: "POST", body: form });
  },
};
