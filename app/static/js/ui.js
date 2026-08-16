// Shared helpers: formatting and DOM building.

// Numbers and dates follow the DEVICE locale, not the interface language:
// an Italian user sees 1.234,56 € even though the UI is in English.
const eurFormatter = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "EUR",
});

export const money = (value) => eurFormatter.format(Number(value || 0));

export const signClass = (value) => (Number(value) < 0 ? "neg" : "pos");

export function formatDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${d}/${m}/${y}`;
}

export function formatDayHeading(iso) {
  const date = new Date(iso + "T00:00:00");
  return date.toLocaleDateString(undefined, {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export const monthLabel = (ym) => {
  const [y, m] = ym.split("-");
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${names[Number(m) - 1]} ${y.slice(2)}`;
};

// Element builder. `text` is always treated as text, never as HTML:
// descriptions come from bank statements and must not be able to inject
// markup.
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

let toastTimer;
export function toast(message, isError = false) {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.className = isError ? "toast error" : "toast";
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), isError ? 5000 : 2800);
}

export const todayISO = () => new Date().toISOString().slice(0, 10);

export function firstOfMonthISO() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
}

export function spinner(message = "Loading…") {
  return el("div", { class: "spinner", text: message });
}

export function empty(message) {
  return el("div", { class: "empty", text: message });
}
