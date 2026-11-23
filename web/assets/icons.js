const paths = {
  printer:
    "M6 9V3h12v6 M6 17H4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-2 M6 14h12v7H6z M17 12h2",
  grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  activity: "M2 12h5l3-8 4 16 3-8h5",
  orders: "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2 M10 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M17 4a4 4 0 0 1 0 7 M18 15a4 4 0 0 1 3 4v2",
  card: "M2 5h20v14H2z M2 10h20 M6 15h3",
  chart: "M3 3v18h18 M7 15l4-5 4 2 5-7",
  reports: "M5 2h10l4 4v16H5z M14 2v5h5 M8 12h8 M8 16h6",
  bell: "M18 8a6 6 0 0 0-12 0c0 7-3 8-3 10h18c0-2-3-3-3-10 M10 21h4",
  settings: "M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M9 15v6",
  store:
    "M3 9l2-6h14l2 6 M3 9c0 4 5 4 6 0 1 4 5 4 6 0 1 4 6 4 6 0 M5 12v9h14v-9 M9 21v-6h5v6",
  moon: "M20 15A9 9 0 0 1 9 3 9 9 0 1 0 20 15z",
  sun: "M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l2 2 M17 17l2 2 M19 5l-2 2 M7 17l-2 2 M16 12a4 4 0 1 1-8 0 4 4 0 1 1 8 0",
  arrowUpRight: "M6 18 18 6 M7 6h11v11",
  arrowRight: "M4 12h16 M14 6l6 6-6 6",
  logout: "M10 4H4v16h6 M9 12h12 M16 7l5 5-5 5",
  menu: "M3 6h18 M3 12h18 M3 18h18",
  panels: "M3 4h18v16H3z M9 4v16",
  layers: "m12 3 10 5-10 5L2 8z M2 12l10 5 10-5 M2 16l10 5 10-5",
  fingerprint:
    "M3 12a9 9 0 0 1 18 0 M6 16v-4a6 6 0 0 1 12 0v3 M9 20c2-3 0-6 1-9a2 2 0 0 1 4 1c0 4 1 6 3 8 M5 20l1-2 M13 20l-1 2 M20 18l-1-2",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z M15 12a3 3 0 1 1-6 0 3 3 0 1 1 6 0",
  shield: "M12 2 3 6v6c0 5 5 9 9 10 4-1 9-5 9-10V6z M8 12l3 3 5-6",
  download: "M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5",
  calendar: "M3 5h18v16H3z M3 10h18 M7 3v4 M17 3v4",
  rupee: "M6 5h13 M6 9h13 M8 5c8 0 8 9 0 9l8 7",
  file: "M5 2h9l5 5v15H5z M14 2v6h5 M8 13h8 M8 17h6",
  check: "m4 12 5 5L20 6",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 1 1 18 0 M12 7v5l3 2",
  alert: "m12 3 10 18H2z M12 9v5 M12 17v1",
  search: "M16 10a6 6 0 1 1-12 0 6 6 0 1 1 12 0 M15 15l6 6",
  inbox: "M3 14l3-10h12l3 10v6H3z M3 14h5l2 3h4l2-3h5",
  volume: "M3 9h4l5-5v16l-5-5H3z M16 8a6 6 0 0 1 0 8 M19 5a10 10 0 0 1 0 14",
  server: "M3 3h18v7H3z M3 14h18v7H3z M7 6v1 M7 17v1",
  chevron: "m9 5 7 7-7 7",
  book: "M4 3h16v18H4z M8 3v18 M11 8h6",
  filter: "M3 5h18l-7 8v6l-4 2v-8z",
};
function icon(name) {
  return (
    '<svg aria-hidden="true" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"><path d="' +
    (paths[name] || paths.file) +
    '"/></svg>'
  );
}
document
  .querySelectorAll("[data-icon]")
  .forEach((el) => (el.innerHTML = icon(el.dataset.icon)));
function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
function money(v) {
  return (Number(v || 0) / 100).toLocaleString("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: Number(v) % 100 ? 2 : 0,
    maximumFractionDigits: 2,
  });
}
function stamp(t) {
  return t
    ? new Date(t * 1000).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      })
    : "—";
}
async function api(url, options = {}) {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  options.signal?.addEventListener("abort", cancel, {once:true});
  if (options.signal?.aborted) cancel();
  const timer = setTimeout(cancel, options.body instanceof Blob ? 120000 : 30000);
  try {
  const res = await fetch(url, {
    ...options, signal: controller.signal,
    headers: { "X-AutoPrint": "1", ...options.headers },
  });
  if (!res.ok) {
    const e = await res.json().catch(() => ({}));
    if (res.status === 401 && url.startsWith("/api/owner/"))
      location.replace("/owner");
    const error = Error(
      typeof e.detail === "string"
        ? e.detail
        : e.detail?.[0]?.msg || "Request failed. Please try again.",
    );
    error.status = res.status;
    throw error;
  }
  return await res.json();
  } catch (error) {
    if (controller.signal.aborted && !options.signal?.aborted)
      throw Error("Connection timed out. Check order history before retrying payment.");
    throw error;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
  }
}
function send(url, method, data) {
  return api(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}
function empty(title, body = "", name = "inbox") {
  return (
    '<div class="empty"><span>' +
    icon(name) +
    "</span><strong>" +
    esc(title) +
    "</strong>" +
    (body ? "<small>" + esc(body) + "</small>" : "") +
    "</div>"
  );
}
