import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { Window } from "happy-dom";

const source = (name) => readFileSync(new URL("../../web/" + name, import.meta.url), "utf8");

function setup(t, { waitDraft } = {}) {
  const win = new Window({
    url: "http://localhost/print",
    settings: { disableJavaScriptFileLoading: true, disableCSSFileLoading: true, disableJavaScriptEvaluation: true },
  });
  t.after(() => win.happyDOM.close());
  const doc = win.document;
  doc.write(source("customer.html"));
  const customerCss = source("assets/customer.css");
  assert.match(customerCss, /prefers-reduced-motion/);
  assert.match(customerCss, /max-width:\s*380px/);
  assert.match(customerCss, /translateY/);
  const form = doc.querySelector("#upload");
  const shop = { name: "Test", sizes: ["A4"], uploads: true, max_mb: 10, opens: "00:00", closes: "00:00" };
  const orders = [], modals = [], verifications = [];
  let verifyCount = 0;
  win.Razorpay = class {
    constructor(options) { this.options = options; }
    on(event, callback) { this[event] = callback; }
    open() { modals.push(this); }
  };
  win.fetch = async (url) => {
    let result;
    if (url.endsWith("/session")) {
      result = { settings: shop, orders, test_payments: true };
    } else if (url.startsWith("/api/customer/drafts?")) {
      if (waitDraft) await waitDraft;
      result = {draft:"draft1",pages:1};
    } else if (url === "/api/customer/drafts/draft1/quote") {
      result = {pages:1,copies:1,rate:200,amount:200,minimum_adjustment:0};
    } else if (url === "/api/customer/drafts/draft1/order") {
      const order = { id: "AP-1", document: "paper.pdf", pages: 1, copies: 1, amount: 200, mode: "BW", payment: "Pending", state: "Awaiting payment" };
      orders.push(order);
      result = order;
    } else if (url === "/api/customer/orders/AP-1/checkout") {
      result = { key: "rzp_test_stub", order_id: "order_1", currency: "INR", amount: orders[0].amount };
    } else if (url === "/api/customer/orders/AP-1/verify") {
      verifyCount++;
      const status = verifications.shift();
      assert.ok(status, "unexpected verification call");
      Object.assign(orders[0], status);
      result = status;
    } else throw Error("Unexpected endpoint " + url);
    return { ok: true, json: async () => structuredClone(result) };
  };
  win.eval(source("assets/icons.js") + "\n" + source("assets/customer.js"));
  return {
    win, doc, form, orders, modals, verifications, count: () => verifyCount,
    select: (name="paper.pdf",size=6) => Object.defineProperty(form.file, "files", {configurable:true,value:[new win.File([new Uint8Array(size)],name)]}),
    submit: () => form.onsubmit({ preventDefault() {} }),
    retry: () => doc.querySelector("#myorders").onclick({ target: doc.querySelector("[data-pay]") }),
    message: () => doc.querySelector("#checkout-feedback").textContent,
    choose: () => form.onchange({target: form.file}),
  };
}
const tick = () => new Promise((done) => setImmediate(done));

test("a missing document focuses the file input and never opens Checkout", async (t) => {
  const ui = setup(t);
  await tick();
  ui.form.requestSubmit();
  assert.equal(ui.doc.activeElement, ui.form.file);
  assert.equal(ui.doc.querySelector("#feedback").textContent, "Choose a document first");
  assert.equal(ui.orders.length, 0);
  assert.equal(ui.modals.length, 0);
});

test("double submit opens one checkout with the quoted amount; dismissal and failure allow the same order to resume", async (t) => {
  const ui = setup(t);
  await tick();
  ui.select();
  await Promise.all([ui.submit(), ui.submit()]);
  assert.equal(ui.orders.length, 1);
  assert.equal(ui.modals.length, 1);
  assert.equal(ui.modals[0].options.amount, 200);
  assert.equal(ui.modals[0].options.currency, "INR");
  assert.equal(ui.modals[0].options.order_id, "order_1");
  assert.equal(ui.doc.querySelector("#upload-btn").disabled, true);
  const required = "Payment first. Complete payment to start printing.";
  ui.modals[0].options.modal.ondismiss();
  assert.equal(ui.message(), required);
  await tick();
  await ui.retry();
  ui.verifications.push({payment:"Failed", state:"Awaiting payment"});
  ui.modals[1]["payment.failed"]();
  await tick();
  assert.equal(ui.message(), required);
  assert.equal(ui.doc.querySelector("#print-step").hidden, true);
  ui.modals[1].options.modal.ondismiss();
  await tick();
  await ui.retry();
  assert.equal(ui.modals.length, 3);
  assert.equal(ui.modals[2].options.order_id, "order_1");
  assert.equal(ui.orders.length, 1);
});

test("delayed capture displays Checking payment; duplicate callbacks leave one receipt", async (t) => {
  const ui = setup(t);
  await tick();
  ui.select();
  await ui.submit();
  ui.verifications.push({payment:"Pending",state:"Awaiting payment"}, {payment:"Paid",state:"Queued"});
  const modal = ui.modals[0].options;
  const success = modal.handler({});
  await modal.handler({});
  modal.modal.ondismiss(); // A late dismissal cannot overwrite payment verification.
  await tick();
  assert.equal(ui.message(), "Checking payment");
  assert.equal(ui.doc.querySelector("[data-pay]"), null);
  assert.equal(ui.doc.querySelector("#print-step").hidden, true);
  await success;
  assert.equal(ui.count(), 2); // One check before capture and one after capture.
  assert.equal(ui.doc.querySelector("#print-step").hidden, false);
  assert.equal(ui.doc.querySelector("#print-status").textContent, "Print status: Queued");
  assert.match(ui.doc.querySelector("#print-receipt").textContent, /paper.pdf.*AP-1/);
  assert.equal(ui.orders.length, 1);
});


test("invalid and oversized uploads are rejected before checkout", async (t) => {
  const ui = setup(t);
  await tick();
  ui.select("unsafe.svg", 8);
  await ui.choose();
  assert.match(ui.doc.querySelector("#feedback").textContent, /PDF, JPG, JPEG or PNG/);
  ui.select("too-large.pdf", 10 * 1024 * 1024 + 1);
  await ui.choose();
  assert.match(ui.doc.querySelector("#feedback").textContent, /upload limit/);
  assert.equal(ui.modals.length, 0);
  assert.equal(ui.orders.length, 0);
});

test("a slow document upload waits once and then opens checkout on the same submit", async (t) => {
  let release;
  const delayed = new Promise((resolve) => { release = resolve; });
  const ui = setup(t, { waitDraft: delayed });
  await tick();
  ui.select();
  const submission = ui.submit();
  await tick();
  assert.equal(ui.modals.length, 0);
  release();
  await submission;
  assert.equal(ui.modals.length, 1);
  assert.equal(ui.orders.length, 1);
});

test("orientation controls and live color filter update preview accurately", async (t) => {
  const ui = setup(t);
  await tick();
  ui.select("sample.pdf", 100);
  await ui.choose();
  await tick();

  const form = ui.doc.querySelector("#upload");
  const img = ui.doc.querySelector("#thumbnail");
  const orient = form.orientation;
  const mode = form.mode;

  // Orientation controls exist with Portrait and Landscape
  const options = Array.from(orient.options).map(o => o.text);
  assert.ok(options.includes("Portrait"), "Should include Portrait");
  assert.ok(options.includes("Landscape"), "Should include Landscape");
  assert.equal(orient.value, "3"); // Defaults to Portrait

  // Default mode is BW, filter is grayscale
  assert.equal(mode.value, "BW");
  assert.equal(img.style.filter, "grayscale(100%)");

  // Switching to Colour removes grayscale filter
  mode.value = "Colour";
  mode.dispatchEvent(new ui.win.Event("change"));
  assert.equal(img.style.filter, "none");

  // Switching back to BW restores grayscale filter
  mode.value = "BW";
  mode.dispatchEvent(new ui.win.Event("change"));
  assert.equal(img.style.filter, "grayscale(100%)");

  // Changing orientation to Landscape
  orient.value = "4";
  orient.dispatchEvent(new ui.win.Event("change"));
  assert.equal(orient.value, "4");
});

