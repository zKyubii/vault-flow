// API access. A single place that talks to the backend.

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
    // fetch only throws when the network is unreachable: the service worker
    // already serves GETs from cache, so we mostly land here on writes.
    throw new ApiError("You are offline: this action needs a connection", 0);
  }

  if (response.status === 401 && !path.startsWith("/auth/")) {
    // Session expired or cookie missing: announce it once to whoever knows
    // what to do with it, instead of failing every view with a cryptic error.
    window.dispatchEvent(new CustomEvent("vaultflow:unauthorized"));
    throw new ApiError("Session expired: please sign in again", 401);
  }

  if (!response.ok) {
    let detail = `Error ${response.status}`;
    try {
      const body = await response.json();
      if (body.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      /* non-JSON response: keep the generic message */
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

// Accepts both a plain object and URLSearchParams: filters with multiple
// accounts and categories repeat the same key, which an object cannot express.
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
  // authentication
  me: () => request("/auth/me"),
  login: (password) => json("POST", "/auth/login", { password }),
  logout: () => json("POST", "/auth/logout", {}),
  logoutEverywhere: () => json("POST", "/auth/logout-everywhere", {}),

  // summary
  summary: (p) => request(`/stats/summary${qs(p)}`),
  months: (p) => request(`/stats/months${qs(p)}`),
  top: (p) => request(`/stats/top${qs(p)}`),
  balances: () => request("/stats/balances"),

  // accounts and categories
  accounts: () => request("/accounts"),
  createAccount: (body) => json("POST", "/accounts", body),
  updateAccount: (id, body) => json("PATCH", `/accounts/${id}`, body),
  deleteAccount: (id) => request(`/accounts/${id}`, { method: "DELETE" }),
  categories: () => request("/categories"),
  createCategory: (body) => json("POST", "/categories", body),
  updateCategory: (id, body) => json("PATCH", `/categories/${id}`, body),
  deleteCategory: (id) => request(`/categories/${id}`, { method: "DELETE" }),

  // transactions
  transactions: (p) => request(`/transactions${qs(p)}`),
  createTransaction: (body) => json("POST", "/transactions", body),
  setCategory: (id, categoryId) =>
    json("PUT", `/transactions/${id}/category`, { category_id: categoryId }),
  deleteTransaction: (id) => request(`/transactions/${id}`, { method: "DELETE" }),

  // rules
  rules: () => request("/rules"),
  createRule: (body) => json("POST", "/rules", body),
  deleteRule: (id) => request(`/rules/${id}`, { method: "DELETE" }),
  applyRules: (body) => json("POST", "/rules/apply", body),
  suggestions: (p) => request(`/rules/suggestions${qs(p)}`),
  similar: (id) => request(`/transactions/${id}/similar`),
  ruleFromTransaction: (body) => json("POST", "/rules/from-transaction", body),

  // detection
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
