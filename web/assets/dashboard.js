const pages = {
  overview: ["Overview", "grid", "Your print shop at a glance."],
  monitor: [
    "Live Printing Monitor",
    "activity",
    "Follow each order from upload to printed pages.",
  ],
  orders: [
    "Orders",
    "orders",
    "Find an order and review its payment and printing history.",
  ],
  sessions: [
    "Customers / Sessions",
    "users",
    "Anonymous customer visits and their order activity.",
  ],
  payments: [
    "Payments",
    "card",
    "Track payment attempts, captures and refunds.",
  ],
  analytics: [
    "Revenue Analytics",
    "chart",
    "Explore verified revenue and print demand.",
  ],
  printers: [
    "Printer Management",
    "printer",
    "Manage printer availability, routing and queued work.",
  ],
  reports: [
    "Reports",
    "reports",
    "Export business activity and verified financial records.",
  ],
  notifications: [
    "Notifications",
    "bell",
    "Follow payment, print job and device alerts.",
  ],
  settings: [
    "Settings",
    "settings",
    "Manage your business details, prices and print policies.",
  ],
};
const groups = [
  ["Workspace", ["overview", "monitor", "orders", "sessions"]],
  ["Business", ["payments", "analytics", "printers", "reports"]],
  ["Preferences", ["notifications", "settings"]],
];
const el = (id) => document.getElementById(id);
const option = (v, t = v) =>
  '<option value="' + esc(v) + '">' + esc(t) + "</option>";
const displayMode = (v) => (v === "BW" ? "B&W" : v);
const stateClass = (s) =>
  ({
    Paid: "green",
    Completed: "green",
    Online: "green",
    Failed: "red",
    Refunded: "violet",
    Printing: "violet",
    Queued: "blue",
    Pending: "amber",
    "Awaiting payment": "amber",
    Offline: "neutral",
    "Refund hold": "amber",
  })[s] || "neutral";
const badge = (s) =>
  '<span class="badge ' + stateClass(s) + '"><b></b>' + esc(s) + "</span>";
let view = "",
  ov,
  filters = {},
  page = 1,
  generation = 0,
  requestId = 0,
  lastEvent = null,
  ws,
  sound = localStorage.getItem("ap-sound") === "true",
  audio;
document.documentElement.dataset.theme =
  localStorage.getItem("ap-theme") || "light";
el("nav").innerHTML = groups
  .map(
    ([name, ids]) =>
      '<div class="nav-section"><small>' +
      name +
      "</small>" +
      ids
        .map(
          (id) =>
            '<a href="#' +
            id +
            '" data-view="' +
            id +
            '">' +
            icon(pages[id][1]) +
            pages[id][0] +
            "</a>",
        )
        .join("") +
      "</div>",
  )
  .join("");
el("theme").onclick = () => {
  const d =
    document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = d;
  localStorage.setItem("ap-theme", d);
};
el("menu").onclick = () => document.body.classList.toggle("open-menu");
el("scrim").onclick = () => document.body.classList.remove("open-menu");
el("logout").onclick = async () => {
  try {
    await send("/api/logout", "POST", {});
    location.replace("/owner");
  } catch (e) {
    toast(e.message);
  }
};
el("close-detail").onclick = () => el("detail").close();
let toastTimer;
function toast(s) {
  el("toast").textContent = s;
  el("toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el("toast").classList.remove("show"), 4500);
}
function stat(label, value, ico, tone, foot = "") {
  return (
    '<article class="stat"><div class="stat-label"><span>' +
    esc(label) +
    '</span><span class="stat-icon ' +
    tone +
    '">' +
    icon(ico) +
    "</span></div><strong>" +
    esc(value) +
    "</strong>" +
    (foot ? "<small>" + esc(foot) + "</small>" : "") +
    "</article>"
  );
}
function panel(title, desc, body, action = "") {
  return (
    '<section class="panel"><div class="panel-head"><div><h2>' +
    esc(title) +
    "</h2>" +
    (desc ? "<p>" + esc(desc) + "</p>" : "") +
    "</div>" +
    action +
    "</div>" +
    body +
    "</section>"
  );
}
function link(text, target) {
  return (
    '<a class="text-link" href="#' +
    target +
    '">' +
    esc(text) +
    " " +
    icon("arrowRight") +
    "</a>"
  );
}
function table(
  headers,
  body,
  zero = "No records yet",
  detail = "Activity will appear here automatically.",
) {
  return (
    '<div class="table-wrap"><table><thead><tr>' +
    headers.map((x) => "<th>" + esc(x) + "</th>").join("") +
    "</tr></thead><tbody>" +
    body.join("") +
    "</tbody></table></div>" +
    (!body.length ? empty(zero, detail) : "")
  );
}
function orderTable(items, compact = false) {
  const headers = compact
    ? ["Order / Document", "Print type", "Amount", "Payment", "Printing"]
    : [
        "Order ID",
        "Customer session",
        "Document",
        "Pages",
        "Print type",
        "Copies",
        "Amount",
        "Payment",
        "Printing",
        "Created (IST)",
        "",
      ];
  return table(
    headers,
    items.map(
      (o) =>
        '<tr><td><button class="row-link" data-order="' +
        esc(o.id) +
        '">' +
        esc(o.id) +
        "</button>" +
        (compact ? "<small>" + esc(o.document) + "</small>" : "") +
        "</td>" +
        (compact
          ? ""
          : "<td>" +
            esc(o.session) +
            "</td><td>" +
            esc(o.document) +
            "</td><td>" +
            o.pages +
            "</td>") +
        "<td>" +
        esc(displayMode(o.mode)) +
        "</td>" +
        (compact ? "" : "<td>" + o.copies + "</td>") +
        "<td>" +
        money(o.amount) +
        "</td><td>" +
        badge(o.payment) +
        "</td><td>" +
        badge(o.state) +
        "</td>" +
        (compact
          ? ""
          : "<td>" +
            stamp(o.created) +
            "</td><td>" +
            (o.state === "Failed" && o.payment === "Paid"
              ? '<button class="secondary" data-retry="' +
                esc(o.id) +
                '">Retry</button>'
              : "") +
            "</td>") +
        "</tr>",
    ),
    "No orders to display",
    "New customer orders appear here as they arrive.",
  );
}
function dateLabel(d, monthly = false) {
  return new Date(
    d + (monthly ? "-01" : "") + "T12:00:00+05:30",
  ).toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    ...(monthly
      ? { month: "short", year: "2-digit" }
      : { month: "short", day: "numeric" }),
  });
}
let chartSeq = 0;
function chart(data, key, label, currency = true, monthly = false) {
  if (!data.length) return empty("No activity for this period");
  const uid = "plot" + ++chartSeq,
    w = 650,
    h = 160,
    left = 52,
    right = 628,
    top = 14,
    base = 130;
  const vals = data.map((x) => x[key]),
    max = Math.max(...vals, 1),
    xs = vals.map(
      (_, i) =>
        left +
        (right - left) * (vals.length === 1 ? 0.5 : i / (vals.length - 1)),
    ),
    ys = vals.map((v) => base - ((base - top) * v) / max);
  const line = xs
    .map((x, i) => (i ? "L" : "M") + x.toFixed(1) + " " + ys[i].toFixed(1))
    .join(" ");
  const fmt = (v) => (currency ? money(v) : String(v));
  const grid = [0, 0.5, 1]
    .map((t) => {
      const y = base - (base - top) * t;
      return (
        '<line x1="' +
        left +
        '" x2="' +
        right +
        '" y1="' +
        y +
        '" y2="' +
        y +
        '" class="gridline"/><text x="43" y="' +
        (y + 4) +
        '" text-anchor="end">' +
        esc(t && max === 1 && currency ? "" : fmt(Math.round(max * t))) +
        "</text>"
      );
    })
    .join("");
  const labels = xs
    .map((x, i) =>
      i === 0 ||
      i === xs.length - 1 ||
      i % Math.max(1, Math.ceil(xs.length / 6)) === 0
        ? '<text x="' +
          x +
          '" y="155" text-anchor="middle">' +
          esc(dateLabel(data[i].day, monthly)) +
          "</text>"
        : "",
    )
    .join("");
  return (
    '<div class="chart" data-points="' +
    esc(
      JSON.stringify(
        data.map((d, i) => dateLabel(d.day, monthly) + " · " + fmt(vals[i])),
      ),
    ) +
    '"><div class="plot-value" aria-live="polite">' +
    esc(label) +
    '</div><svg tabindex="0" role="img" aria-label="' +
    esc(label) +
    '. Use left and right arrows to inspect values." viewBox="0 0 ' +
    w +
    " " +
    h +
    '"><defs><linearGradient id="' +
    uid +
    '" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#8b79f2" stop-opacity=".26"/><stop offset="1" stop-color="#8b79f2" stop-opacity=".02"/></linearGradient></defs>' +
    grid +
    '<path d="' +
    line +
    " L" +
    xs.at(-1) +
    " " +
    base +
    " L" +
    xs[0] +
    " " +
    base +
    ' Z" fill="url(#' +
    uid +
    ')"/><path d="' +
    line +
    '" class="trend-line"/>' +
    xs
      .filter(() => xs.length <= 35)
      .map(
        (x, i) =>
          '<circle cx="' + x + '" cy="' + ys[i] + '" r="3" class="plot-dot"/>',
      )
      .join("") +
    labels +
    "</svg></div>"
  );
}
function bars(entries, currency = false) {
  let max = Math.max(1, ...entries.map((x) => x[1]));
  return (
    '<div class="bars">' +
    entries
      .map(
        ([name, value, tone]) =>
          '<div class="bar-item" tabindex="0" title="' +
          esc(name) +
          " · " +
          esc(currency ? money(value) : value) +
          '"><div><span><b class="dot ' +
          tone +
          '"></b>' +
          esc(name) +
          "</span><strong>" +
          esc(currency ? money(value) : value) +
          '</strong></div><div class="track"><i class="' +
          tone +
          '" style="width:' +
          (value / max) * 100 +
          '%"></i></div></div>',
      )
      .join("") +
    "</div>"
  );
}
function listLine(label, value, ico, tone) {
  return (
    '<div class="summary-line"><span><i class="mini ' +
    tone +
    '">' +
    icon(ico) +
    "</i>" +
    esc(label) +
    "</span><strong>" +
    esc(value) +
    "</strong></div>"
  );
}
function overview() {
  const s = ov.today,
    online = ov.printers.filter((p) => p.online).length;
  return (
    '<div class="stats">' +
    stat(
      "Revenue today",
      money(s.gross),
      "rupee",
      "lavender",
      "Verified payments",
    ) +
    stat("Orders today", s.orders, "orders", "sky", "Customer orders") +
    stat("Pages printed", s.pages, "layers", "peach", "Completed today") +
    stat(
      "Active sessions",
      ov.sessions,
      "users",
      "mint",
      "In the last 5 minutes",
    ) +
    "</div>" +
    '<div class="dashboard-grid">' +
    panel(
      "Revenue trend",
      "Verified payments over the last 7 days",
      '<div class="chart-total">' +
        money(ov.trend.reduce((a, b) => a + b.gross, 0)) +
        "</div>" +
        chart(ov.trend, "gross", "Daily revenue"),
      link("Analytics", "analytics"),
    ) +
    panel(
      "Print floor",
      "Current device and queue status",
      listLine(
        "Printers online",
        online + " / " + ov.printers.length,
        "printer",
        "lavender",
      ) +
        listLine("Waiting to print", ov.queue, "clock", "sky") +
        listLine("Completed today", s.documents, "check", "mint") +
        listLine("Failed today", s.failed_jobs, "alert", "peach"),
      link("Manage", "printers"),
    ) +
    "</div>" +
    panel(
      "Recent orders",
      "Latest customer printing activity",
      orderTable(ov.latest, true),
      link("All orders", "orders"),
    ) +
    '<div class="equal-grid">' +
    panel(
      "Payment activity",
      "Today’s payment records",
      bars([
        ["Successful", s.paid, "green"],
        ["Pending", s.pending, "amber"],
        ["Failed", s.failed, "red"],
      ]),
      link("Payments", "payments"),
    ) +
    panel(
      "Business totals",
      "Financial and printing summary",
      listLine("Revenue this month", money(ov.month), "rupee", "lavender") +
        listLine(
          "Total documents printed",
          ov.lifetime.documents,
          "file",
          "sky",
        ) +
        listLine("Total pages printed", ov.lifetime.pages, "layers", "mint") +
        listLine("Refunds today", money(s.refunds), "card", "peach"),
    ) +
    "</div>"
  );
}
const dates = [
  ["today", "Today"],
  ["yesterday", "Yesterday"],
  ["week", "Last 7 days"],
  ["month", "Last 30 days"],
  ["thismonth", "This month"],
  ["custom", "Custom range"],
];
function rangeBar(all = false) {
  return (
    '<label class="inline-control">' +
    icon("calendar") +
    '<select id="preset" aria-label="Date range">' +
    (all ? option("all", "All dates") : "") +
    dates.map((x) => option(...x)).join("") +
    '</select></label><span id="customrange"><input type="date" aria-label="Start date" id="startdate"> – <input type="date" aria-label="End date" id="enddate"><button id="applydates" class="secondary">Apply</button></span>'
  );
}
function controls() {
  if (["overview", "printers", "settings", "notifications"].includes(view))
    return "";
  const all = ["orders", "sessions", "payments", "monitor"].includes(view);
  let str = '<div class="toolbar">';
  if (all)
    str +=
      '<label class="search">' +
      icon("search") +
      '<input id="search" aria-label="Search records" placeholder="' +
      {
        orders: "Search orders…",
        monitor: "Search live orders…",
        payments: "Search transactions…",
        sessions: "Search sessions…",
      }[view] +
      '"></label>';
  str += rangeBar(all);
  if (["orders", "monitor", "payments"].includes(view))
    str +=
      '<select id="paymentfilter" aria-label="Payment status">' +
      option("", "All payments") +
      ["Paid", "Pending", "Failed", "Refunded"].map((x) => option(x)).join("") +
      "</select>";
  if (["orders", "monitor"].includes(view))
    str +=
      '<select id="statefilter" aria-label="Printing status">' +
      option("", "All print states") +
      [
        "Awaiting payment",
        "Queued",
        "Printing",
        "Completed",
        "Failed",
        "Refund hold",
        "Upload expired",
      ]
        .map((x) => option(x))
        .join("") +
      "</select>";
  return str + "</div>";
}
function analytics(data) {
  const s = data.summary,
    p = data.periods;
  return (
    '<div class="stats">' +
    stat("Captured revenue", money(s.gross), "rupee", "lavender") +
    stat("Processed refunds", money(s.refunds), "card", "peach") +
    stat("Net revenue", money(s.net), "chart", "mint") +
    stat("Average order value", money(s.average), "orders", "sky") +
    "</div>" +
    '<div class="equal-grid">' +
    panel(
      "Daily revenue",
      "Captured payments in the selected period",
      chart(data.days, "gross", "Daily revenue"),
    ) +
    panel(
      "Monthly revenue",
      "Captured payments grouped by month",
      chart(data.months, "gross", "Monthly revenue", true, true),
    ) +
    panel(
      "Printing orders",
      "Orders by creation date",
      chart(data.days, "orders", "Order volume", false),
    ) +
    panel(
      "Revenue by print type",
      "Verified revenue in the selected period",
      bars(
        [
          ["Black & white", s.BW, "violet"],
          ["Colour", s.Colour, "green"],
        ],
        true,
      ),
    ) +
    panel(
      "Payment outcomes",
      s.transactions + " recorded transactions",
      bars([
        ["Successful", s.paid, "green"],
        ["Failed", s.failed, "red"],
        ["Pending", s.pending, "amber"],
      ]),
    ) +
    panel(
      "Revenue periods",
      "Gross captured payments",
      Object.entries({
        today: "Today",
        yesterday: "Yesterday",
        week: "Last 7 days",
        thismonth: "This month",
        year: "Year to date",
      })
        .map(([k, v]) => listLine(v, money(p[k]), "rupee", "lavender"))
        .join(""),
    ) +
    "</div>"
  );
}
function paymentTable(items) {
  return table(
    [
      "Razorpay transaction",
      "Order ID",
      "Amount",
      "Method",
      "Payment",
      "Time (IST)",
      "Refund",
    ],
    items.map(
      (x) =>
        "<tr><td>" +
        esc(x.id || "Awaiting transaction") +
        '</td><td><button class="row-link" data-order="' +
        esc(x.oid) +
        '">' +
        esc(x.oid) +
        "</button></td><td>" +
        money(x.amount) +
        "</td><td>" +
        esc(x.method || "—") +
        "</td><td>" +
        badge(x.status) +
        "</td><td>" +
        stamp(x.at) +
        "</td><td>" +
        esc(x.refund_status || "—") +
        (x.refunded ? " · " + money(x.refunded) : "") +
        "</td></tr>",
    ),
    "No matching payments",
    "Payment activity will appear when customers check out.",
  );
}
function sessionTable(items) {
  return table(
    [
      "Anonymous session",
      "Started (IST)",
      "Last active (IST)",
      "Orders",
      "Order value",
    ],
    items.map(
      (x) =>
        '<tr><td><button class="row-link" data-session="' +
        esc(x.id) +
        '">' +
        esc(x.id) +
        "</button></td><td>" +
        stamp(x.created) +
        "</td><td>" +
        stamp(x.seen) +
        "</td><td>" +
        x.orders +
        "</td><td>" +
        money(x.amount) +
        "</td></tr>",
    ),
    "No matching sessions",
    "Customer visits will appear here.",
  );
}
function notices(items) {
  return items.length
    ? items
        .map(
          (x) =>
            '<div class="notice"><i class="stat-icon ' +
            (["warning", "error", "verification"].includes(x.kind)
              ? "peach"
              : "lavender") +
            '">' +
            icon(
              ["warning", "error", "verification"].includes(x.kind)
                ? "alert"
                : "activity",
            ) +
            "</i><div><strong>" +
            esc(x.message) +
            "</strong><small>" +
            stamp(x.at) +
            " IST</small></div></div>",
        )
        .join("")
    : empty(
        "All caught up",
        "New print and payment alerts will appear here.",
        "bell",
      );
}
function pager(d) {
  return (
    '<div class="pager"><span>' +
    d.total +
    " record" +
    (d.total === 1 ? "" : "s") +
    '</span><button class="secondary" data-page="-1" ' +
    (page <= 1 ? "disabled" : "") +
    ">Previous</button><b>" +
    page +
    " / " +
    Math.max(1, Math.ceil(d.total / 30)) +
    '</b><button class="secondary" data-page="1" ' +
    (page * 30 >= d.total ? "disabled" : "") +
    ">Next</button></div>"
  );
}
function printers() {
  const cb = (id, a, t) =>
    '<button class="secondary" data-printer="' +
    esc(id) +
    '" data-action="' +
    a +
    '">' +
    t +
    "</button>";
  return (
    panel(
      "Local print agent",
      "Heartbeat and connection status",
      ov.agents.length
        ? ov.agents
            .map((a) =>
              listLine(
                a.id,
                (a.online ? "Online" : "Offline") + " · " + stamp(a.seen),
                "server",
                a.online ? "mint" : "peach",
              ),
            )
            .join("")
        : empty(
            "No agent connected",
            "Start the local print agent on the shop’s printer computer.",
            "server",
          ),
    ) +
    (ov.printers.length
      ? '<div class="equal-grid">' +
        ov.printers
          .map((p) =>
            panel(
              p.name,
              p.model || "Printer model unavailable",
              '<div class="chips">' +
                badge(p.online ? "Online" : "Offline") +
                badge(
                  !p.enabled
                    ? "Disabled"
                    : p.paused
                      ? "Paused"
                      : p.available
                        ? "Available"
                        : "Unavailable",
                ) +
                "</div>" +
                listLine(
                  "Active for",
                  (p.modes || "Unassigned").replace("BW", "B&W"),
                  "layers",
                  "lavender",
                ) +
                listLine(
                  "Current job",
                  p.current_job || "Idle",
                  "file",
                  "sky",
                ) +
                listLine(
                  "Pending / Completed / Failed",
                  [p.pending, p.completed, p.failed].join(" / "),
                  "activity",
                  "mint",
                ) +
                listLine("Total pages", p.pages, "file", "peach") +
                (p.error
                  ? '<p class="form-error">' + esc(p.error) + "</p>"
                  : "") +
                '<div class="button-row">' +
                cb(
                  p.id,
                  p.paused ? "resume" : "pause",
                  p.paused ? "Resume queue" : "Pause queue",
                ) +
                cb(
                  p.id,
                  p.enabled ? "disable" : "enable",
                  p.enabled ? "Disable" : "Enable",
                ) +
                cb(p.id, "BW", "Use for B&W") +
                cb(p.id, "Colour", "Use for colour") +
                "</div>",
            ),
          )
          .join("") +
        "</div>"
      : panel(
          "Printers",
          "Registered devices",
          empty(
            "No printers registered",
            "Printers appear when the agent discovers them.",
            "printer",
          ),
        )) +
    panel(
      "Print configuration",
      "Per-page prices and supported paper sizes",
      '<div class="button-row">' +
        link("Printing settings", "settings") +
        link("Review failed jobs", "orders/failed") +
        "</div>",
    )
  );
}
const reportTypes = [
  [
    "daily",
    "Daily business",
    "Orders, printing and revenue for a business day.",
    "calendar",
  ],
  [
    "weekly",
    "Weekly business",
    "Review seven days of business activity.",
    "chart",
  ],
  [
    "monthly",
    "Monthly business",
    "Summarize the current calendar month.",
    "reports",
  ],
  [
    "printing",
    "Printing activity",
    "Order details, pages, copies and job status.",
    "printer",
  ],
  [
    "payments",
    "Payment transactions",
    "Captures, failed attempts and refund records.",
    "card",
  ],
  [
    "printers",
    "Printer performance",
    "Completed jobs, failures and output by device.",
    "activity",
  ],
];
function exportsFor(kind, preset = filters.preset) {
  const query = new URLSearchParams({ ...filters, preset });
  return ["csv", "xlsx"]
    .map(
      (fmt) =>
        '<a class="secondary" download href="/api/owner/reports/' +
        kind +
        "." +
        fmt +
        "?" +
        esc(query) +
        '">' +
        icon("download") +
        (fmt === "csv" ? "CSV" : "Excel") +
        "</a>",
    )
    .join("");
}
function reports() {
  return (
    '<div class="report-grid">' +
    reportTypes
      .map(([k, n, d, i]) => {
        const p =
          filters.preset === "default"
            ? { daily: "today", weekly: "week", monthly: "thismonth" }[k] ||
              "week"
            : filters.preset;
        return (
          '<section class="panel report"><span class="stat-icon lavender">' +
          icon(i) +
          "</span><h2>" +
          n +
          "</h2><p>" +
          d +
          "</p><small>" +
          esc(
            p === "custom"
              ? filters.start + " – " + filters.end
              : dates.find((x) => x[0] === p)?.[1] || p,
          ) +
          '</small><div class="button-row">' +
          exportsFor(k, p) +
          "</div></section>"
        );
      })
      .join("") +
    "</div>"
  );
}
let savedSettings;
function settingsForm(s) {
  savedSettings = s;
  const input = (k, name, type = "text", attrs = "") =>
    "<label>" +
    name +
    '<input name="' +
    k +
    '" type="' +
    type +
    '" value="' +
    esc(s[k]) +
    '" ' +
    attrs +
    "></label>";
  const price = (k, name) =>
    "<label>" +
    name +
    ' (₹)<input type="number" step="any" min="' +
    (k === "minimum" ? "1" : "0.01") +
    '" max="' +
    (k === "minimum" ? "10000" : "1000") +
    '" name="' +
    k +
    '" value="' +
    s[k] / 100 +
    '" required></label>';
  const toggle = (k, title, desc) =>
    '<label class="toggle"><span><strong>' +
    title +
    "</strong><small>" +
    desc +
    '</small></span><input type="checkbox" name="' +
    k +
    '" ' +
    (s[k] ? "checked" : "") +
    "></label>";
  const editable = ov.user.role === "owner";
  return (
    '<form id="settingsform"><fieldset ' +
    (editable ? "" : "disabled") +
    ">" +
    panel(
      "Business profile",
      "Shown on the customer portal.",
      '<div class="form-grid">' +
        input("name", "Business name", "text", 'required maxlength="100"') +
        input("contact", "Contact information", "text", 'maxlength="200"') +
        input("address", "Business address", "text", 'maxlength="500"') +
        '<label>Business logo<input type="file" name="logoFile" accept="image/jpeg,image/png"><small>PNG or JPEG, up to 250 KB</small></label></div>' +
        (s.logo
          ? '<label class="check-row"><input type="checkbox" name="removeLogo"> Remove current logo</label>'
          : ""),
    ) +
    panel(
      "Printing prices",
      "Prices apply per printed page, per copy. Changes apply to new orders.",
      '<div class="form-grid">' +
        price("bw", "B&W · single-sided") +
        price("colour", "Colour · single-sided") +
        price("bw_duplex", "B&W · double-sided") +
        price("colour_duplex", "Colour · double-sided") +
        price("minimum", "Minimum order") +
        "</div>",
    ) +
    panel(
      "Paper and uploads",
      "Choose formats available to customers.",
      '<div class="checks">' +
        ["A3", "A4", "A5", "Letter", "Legal"]
          .map(
            (k) =>
              '<label class="check-row"><input type="checkbox" name="size" value="' +
              k +
              '" ' +
              (s.sizes.includes(k) ? "checked" : "") +
              "> " +
              k +
              "</label>",
          )
          .join("") +
        "</div>" +
        input(
          "max_mb",
          "Maximum upload size (MB)",
          "number",
          'min="1" max="32" required',
        ),
    ) +
    panel(
      "System controls",
      "Pausing printing holds new dispatches. Active spooler jobs finish normally.",
      toggle(
        "uploads",
        "Customer uploads",
        "Accept documents through the customer portal.",
      ) +
        toggle(
          "printing",
          "Printing",
          "Dispatch paid orders to enabled printers.",
        ) +
        toggle(
          "cleanup",
          "Automatic cleanup",
          "Delete expired documents while retaining business records.",
        ) +
        '<div class="form-grid">' +
        input(
          "retention",
          "Document retention (hours)",
          "number",
          'min="1" max="720" required',
        ) +
        "</div>",
    ) +
    panel(
      "Operating hours",
      "Shop time is India Standard Time. Matching times mean open all day.",
      '<div class="form-grid">' +
        input("opens", "Opens at", "time", "required") +
        input("closes", "Closes at", "time", "required") +
        "</div>",
    ) +
    "</fieldset>" +
    (editable
      ? '<div class="button-row"><button class="primary" type="submit">' +
        icon("check") +
        'Save changes</button><button type="button" id="showaudit" class="secondary">' +
        icon("book") +
        "Audit log</button></div>"
      : '<p class="sub">An owner can change business settings.</p>') +
    "</form>"
  );
}
function wireSettings() {
  const f = el("settingsform");
  if (!f) return;
  f.onsubmit = async (e) => {
    e.preventDefault();
    const btn = f.querySelector("[type=submit]");
    btn.disabled = true;
    try {
      const data = { ...savedSettings },
        fd = new FormData(f);
      for (const k of ["name", "contact", "address", "opens", "closes"])
        data[k] = fd.get(k).trim();
      for (const k of [
        "bw",
        "colour",
        "bw_duplex",
        "colour_duplex",
        "minimum",
      ]) {
        const paise = Number(fd.get(k)) * 100;
        if (Math.abs(paise - Math.round(paise)) > 0.000001)
          throw Error("Enter prices using up to two decimal places.");
        data[k] = Math.round(paise);
      }
      for (const k of ["max_mb", "retention"]) data[k] = Number(fd.get(k));
      for (const k of ["printing", "uploads", "cleanup"]) data[k] = fd.has(k);
      data.sizes = fd.getAll("size");
      if (!data.sizes.length) throw Error("Choose at least one paper size.");
      if (fd.has("removeLogo")) data.logo = "";
      const file = fd.get("logoFile");
      if (file?.size) {
        if (file.size > 250 * 1024) throw Error("Logo exceeds 250 KB.");
        data.logo = await new Promise((ok, bad) => {
          const rd = new FileReader();
          rd.onload = () => ok(rd.result);
          rd.onerror = () => bad(Error("Could not read the logo"));
          rd.readAsDataURL(file);
        });
      }
      await send("/api/owner/settings", "PUT", data);
      toast("Business settings saved");
      await render(true);
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  };
  if (el("showaudit")) el("showaudit").onclick = () => showAudit(1);
}
async function showAudit(page) {
  try {
    const d = await api("/api/owner/list/audit?page=" + page);
    el("detail-body").innerHTML =
      "<h2>Audit log</h2>" +
      table(
        ["Time (IST)", "Staff", "Action", "Details"],
        d.items.map(
          (x) =>
            "<tr><td>" +
            stamp(x.at) +
            "</td><td>" +
            esc(x.actor) +
            "</td><td>" +
            esc(x.action) +
            "</td><td>" +
            esc(x.subject) +
            "</td></tr>",
        ),
      ) +
      '<div class="button-row"><button class="secondary" id="auditprev" ' +
      (page === 1 ? "disabled" : "") +
      '>Previous</button><button class="secondary" id="auditnext" ' +
      (page * 30 >= d.total ? "disabled" : "") +
      ">Next</button></div>";
    el("auditprev").onclick = () => showAudit(page - 1);
    el("auditnext").onclick = () => showAudit(page + 1);
    if (!el("detail").open) el("detail").showModal();
  } catch (e) {
    toast(e.message);
  }
}
function notifyEvents(events) {
  if (!events.length) {
    lastEvent = 0;
    return;
  }
  const e = events[0];
  if (lastEvent !== null && e.id > lastEvent) {
    toast(e.message);
    if (sound && audio) {
      const osc = audio.createOscillator(),
        gain = audio.createGain();
      osc.connect(gain);
      gain.connect(audio.destination);
      gain.gain.value = 0.05;
      osc.frequency.value = 620;
      osc.start();
      gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.16);
      osc.stop(audio.currentTime + 0.18);
    }
  }
  lastEvent = e.id;
}
function chartControls() {
  document.querySelectorAll(".chart").forEach((c) => {
    const pts = JSON.parse(c.dataset.points),
      svg = c.querySelector("svg"),
      out = c.querySelector(".plot-value"),
      label = out.textContent;
    let i = 0;
    const select = (n) => {
      i = Math.max(0, Math.min(pts.length - 1, n));
      out.textContent = pts[i];
    };
    svg.onpointermove = (e) => {
      const rect = svg.getBoundingClientRect();
      select(
        Math.round(
          ((((e.clientX - rect.left) / rect.width) * 650 - 52) / 576) *
            (pts.length - 1),
        ),
      );
    };
    svg.onpointerleave = () => (out.textContent = label);
    svg.onfocus = () => select(i);
    svg.onkeydown = (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        select(i + (e.key === "ArrowRight" ? 1 : -1));
      }
    };
  });
}
async function render(force = false) {
  const idx = ++requestId,
    section = view;
  try {
    const query = new URLSearchParams({ ...filters, page }),
      listName = {
        orders: "orders",
        monitor: "orders",
        sessions: "sessions",
        payments: "payments",
        notifications: "events",
      }[section];
    const fetchSpecific = listName
      ? api("/api/owner/list/" + listName + "?" + query)
      : section === "analytics"
        ? api("/api/owner/analytics?" + query)
        : section === "settings" && (force || !el("settingsform"))
          ? api("/api/owner/settings")
          : Promise.resolve(null);
    const [overviewData, d] = await Promise.all([
      api("/api/owner/overview"),
      fetchSpecific,
    ]);
    if (idx !== requestId || section !== view) return;
    ov = overviewData;
    el("biz").textContent = ov.business;
    el("avatar").textContent = ov.user.email[0].toUpperCase();
    el("staff-email").textContent = ov.user.email;
    el("role").textContent =
      ov.user.role === "owner" ? "Workspace owner" : "Administrator";
    notifyEvents(ov.events);
    let html = "";
    if (section === "overview") html = overview();
    if (section === "analytics")
      html =
        analytics(d) +
        '<div class="button-row">' +
        exportsFor("payments") +
        "</div>";
    if (section === "printers") html = printers();
    if (section === "reports") html = reports();
    if (section === "settings") {
      if (!d) return;
      html = settingsForm(d);
    }
    if (listName) {
      let body =
        section === "payments"
          ? paymentTable(d.items)
          : section === "sessions"
            ? sessionTable(d.items)
            : section === "notifications"
              ? notices(d.items)
              : orderTable(d.items);
      html = panel(
        {
          monitor: "Printing activity",
          orders: "Order history",
          sessions: "Customer sessions",
          payments: "Transaction history",
          notifications: "Recent alerts",
        }[section],
        "",
        body + pager(d),
        section === "notifications"
          ? '<button id="sound" class="secondary">' +
              icon("volume") +
              "Sound " +
              (sound ? "on" : "off") +
              "</button>"
          : "",
      );
    }
    el("results").innerHTML = html;
    chartControls();
    if (section === "settings") wireSettings();
    if (el("sound"))
      el("sound").onclick = () => {
        sound = !sound;
        localStorage.setItem("ap-sound", sound);
        if (sound) {
          audio = audio || new AudioContext();
          audio.resume();
        }
        el("sound").innerHTML =
          icon("volume") + "Sound " + (sound ? "on" : "off");
      };
  } catch (e) {
    if (idx === requestId) {
      if (!el("results").children.length)
        el("results").innerHTML = empty(
          "Could not load this view",
          e.message,
          "alert",
        );
      else toast(e.message);
    }
  }
}
function filtersChanged() {
  page = 1;
  render();
}
function navigate() {
  generation++;
  const [next, arg] = location.hash.slice(1).split("/");
  view = Object.hasOwn(pages, next) ? next : "overview";
  page = 1;
  filters = {
    preset: [
      "orders",
      "monitor",
      "sessions",
      "payments",
      "notifications",
    ].includes(view)
      ? "all"
      : view === "reports"
        ? "default"
        : "week",
  };
  if (view === "orders" && arg) {
    if (arg === "failed") filters.state = "Failed";
    else filters.q = decodeURIComponent(arg);
  }
  document.body.classList.remove("open-menu");
  document.querySelectorAll("nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.view === view);
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  el("crumb").textContent = pages[view][0];
  document.title = pages[view][0] + " · AutoPrint";
  const date = new Date().toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
  el("main").innerHTML =
    '<header class="page-heading"><div><h1>' +
    pages[view][0] +
    "</h1><p>" +
    pages[view][2] +
    "</p></div>" +
    (view === "overview"
      ? '<span class="date-chip">' + icon("calendar") + esc(date) + "</span>"
      : "") +
    "</header>" +
    controls() +
    '<div id="results"></div>';
  if (el("preset")) {
    if (view === "reports")
      el("preset").insertAdjacentHTML(
        "afterbegin",
        option("default", "Report default dates"),
      );
    el("preset").value = filters.preset;
    el("preset").onchange = (e) => {
      const custom = e.target.value === "custom";
      el("customrange").classList.toggle("visible", custom);
      if (!custom) {
        filters.preset = e.target.value;
        delete filters.start;
        delete filters.end;
        filtersChanged();
      }
    };
    el("applydates").onclick = () => {
      if (
        !el("startdate").value ||
        !el("enddate").value ||
        el("enddate").value < el("startdate").value
      ) {
        toast("Choose a valid start and end date");
        return;
      }
      Object.assign(filters, {
        preset: "custom",
        start: el("startdate").value,
        end: el("enddate").value,
      });
      filtersChanged();
    };
  }
  for (const [id, k] of [
    ["statefilter", "state"],
    ["paymentfilter", "payment"],
    ["search", "q"],
  ]) {
    const f = el(id);
    if (f) {
      f.value = filters[k] || "";
      let timer;
      const n = generation;
      f.oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(
          () => {
            if (n === generation) {
              filters[k] = f.value;
              filtersChanged();
            }
          },
          id === "search" ? 200 : 0,
        );
      };
    }
  }
  render(true);
}
el("main").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  try {
    if (b.dataset.page) {
      page += Number(b.dataset.page);
      render();
    }
    if (b.dataset.session)
      location.hash = "orders/" + encodeURIComponent(b.dataset.session);
    if (b.dataset.order) {
      const d = await api(
        "/api/owner/orders/" + encodeURIComponent(b.dataset.order),
      );
      const o = d.order;
      el("detail-body").innerHTML =
        '<h2>Order details</h2><p class="sub">' +
        esc(o.id) +
        "</p><dl>" +
        Object.entries({
          Document: o.document,
          "Customer session": o.session,
          "Created (IST)": stamp(o.created),
          "Print type": displayMode(o.mode),
          Paper: o.paper,
          Sides: o.duplex ? "Double-sided" : "Single-sided",
          "Pages × copies": o.pages + " × " + o.copies,
          Amount: money(o.amount),
          Payment: o.payment,
          Printing: o.state,
          ...(d.collected ? {Pickup: "Collected"} : {}),
          Printer: o.printer || "Unassigned",
          "Latest error": o.error || "—",
        })
          .map(
            ([k, v]) =>
              "<div><dt>" + esc(k) + "</dt><dd>" + esc(v) + "</dd></div>",
          )
          .join("") +
        "</dl><h3>Payment history</h3>" +
        table(
          ["Transaction", "Status", "Amount", "Time (IST)"],
          d.payments.map(
            (p) =>
              "<tr><td>" +
              esc(p.id || "Awaiting transaction") +
              "</td><td>" +
              badge(p.status) +
              "</td><td>" +
              money(p.amount) +
              "</td><td>" +
              stamp(p.at) +
              "</td></tr>",
          ),
        ) +
        (d.attempts.length
          ? "<h3>Print attempts</h3>" +
            table(
              ["Printer", "Outcome", "Time (IST)"],
              d.attempts.map(
                (a) =>
                  "<tr><td>" +
                  esc(a.printer || "Unassigned") +
                  "</td><td>" +
                  badge(a.state) +
                  "</td><td>" +
                  stamp(a.finished) +
                  "</td></tr>",
              ),
            )
          : "");
      if (o.state === "Completed" && o.payment === "Paid" && !d.collected) {
        const handover = document.createElement("button");
        handover.className = "primary";
        handover.textContent = "Confirm customer collected print";
        handover.onclick = async () => {
          handover.disabled = true;
          try {
            await send("/api/owner/orders/" + encodeURIComponent(o.id) + "/collect", "POST", {});
            handover.textContent = "Collected";
          } catch (err) {
            handover.disabled = false;
            toast(err.message);
          }
        };
        el("detail-body").append(handover);
      }
      if (o.state === "Printing" && d.spool?.some(s=>["REQUIRES_REVIEW","Awaiting printer confirmation"].includes(s.phase))) {
        const resolve = document.createElement("button");
        resolve.textContent = "Confirm physical output";
        resolve.onclick = async () => {
          if (!confirm("Have you physically checked that this entire job printed correctly?")) return;
          resolve.disabled = true;
          try {
            await send("/api/owner/orders/"+encodeURIComponent(o.id)+"/confirm-output","POST",{claim:d.claim,confirmed:true});
            resolve.textContent = "Output confirmed";
          } catch (err) { resolve.disabled=false; toast(err.message); }
        };
        el("detail-body").append(resolve);
      }
      el("detail").showModal();
    }
    if (b.dataset.retry) {
      if (
        !confirm(
          "Retry this job? Check the printer output first to avoid printing a second copy.",
        )
      )
        return;
      b.disabled = true;
      await send("/api/owner/orders/" + b.dataset.retry + "/retry", "POST", {});
      toast("Job returned to the queue");
      await render();
    }
    if (b.dataset.printer) {
      b.disabled = true;
      await send("/api/owner/printers", "POST", {
        id: b.dataset.printer,
        action: b.dataset.action,
      });
      toast("Printer settings updated");
      await render();
    }
  } catch (err) {
    toast(err.message);
    b.disabled = false;
  }
});
function connect() {
  ws = new WebSocket(
    (location.protocol === "https:" ? "wss://" : "ws://") +
      location.host +
      "/api/owner/live",
  );
  ws.onopen = () => {
    el("live").innerHTML = "<b></b>Live updates";
    el("live").classList.add("online");
  };
  ws.onmessage = () => render();
  ws.onclose = async (e) => {
    el("live").innerHTML = "<b></b>Reconnecting";
    el("live").classList.remove("online");
    if (e.code === 1008) {
      try {
        await api("/api/owner/overview");
      } catch {
        return;
      }
    }
    setTimeout(connect, 3000);
  };
}
window.addEventListener("hashchange", navigate);
navigate();
connect();
