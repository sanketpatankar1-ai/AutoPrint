const r = (id) => document.getElementById(id);
const f = r("upload"), feedback = r("feedback"), checkoutFeedback = r("checkout-feedback");
const paymentFirst = "Payment first. Complete payment to start printing.";
const checkingPayment = "Checking payment";
const stages = ["Payment verified","Queued","Printing","Ready for pickup","Collected"];
const base = (id) => "/api/customer/orders/" + encodeURIComponent(id);
let settings = {}, available = false, busy = false, checkoutActive = false;
let documents = [], activeDocument = null;
let selection = null, pendingTestOrder = null, refreshVersion = 0;
let trackingOrder = sessionStorage.getItem("ap-tracking");
let lastShop = "", lastPaper = "", lastStatus = "";
function track(id) {
  trackingOrder = id;
  if (id) sessionStorage.setItem("ap-tracking", id);
  else sessionStorage.removeItem("ap-tracking");
}
function syncUploadButton() {
  f.file.required = !activeDocument;
  r("upload-btn").disabled = !available || busy || checkoutActive;
  f.setAttribute("aria-busy",String(busy || checkoutActive));
  f.file.disabled = busy || checkoutActive;
  f.querySelector("fieldset").disabled = busy || checkoutActive;
}
function requireDocument() {
  feedback.textContent = "Choose a document first";
  f.file.focus();
}
f.file.addEventListener("invalid", () => { if (!f.file.files.length) requireDocument(); });
function rate() {
  if (selection?.draft) { requestQuote(); return; }
  r("estimate").textContent = "—";
  r("rate").textContent = "Select a document to calculate its price.";
}

function clearSelection() {
  clearTimeout(quoteTimer);
  clearTimeout(previewTimer);
  quoteVersion++;
  previewRequestId++;
  lastPreviewUrl = "";
  selection?.controller.abort();
  selection = null;
  r("preview").hidden = true;
  r("preview").open = false;
  r("thumbnail").removeAttribute("src");
  r("thumbnail").style.display = "none";
  r("preview-message").textContent = "";
  r("file-name").textContent = "Choose a document";
  r("file-info").textContent = "PDF, JPG or PNG";
  rate();
}
function prepare(file) {
  if (selection?.file === file) return selection.promise;
  clearSelection();
  if (!/\.(pdf|jpe?g|png)$/i.test(file.name)) throw Error("Choose a PDF, JPG, JPEG or PNG.");
  if (!file.size || file.size > settings.max_mb * 1024 ** 2) throw Error(file.size ? "File exceeds the upload limit." : "The selected document is empty.");
  const item = {file, controller:new AbortController(), estimatedPages:/\.pdf$/i.test(file.name) ? null : 1};
  selection = item;
  const size = file.size < 1048576 ? Math.max(1,Math.round(file.size/1024)) + " KB" : (file.size/1048576).toFixed(1) + " MB";
  r("file-name").textContent = file.name;
  r("file-info").textContent = size + " · Checking document…";
  rate();
  item.promise = api("/api/customer/drafts?name=" + encodeURIComponent(file.name), {
    method:"POST", body:file, signal:item.controller.signal,
  }).then((draft) => {
    if (selection !== item) throw Error("Selection changed.");
    item.draft = draft;
    r("preview-page").max = String(draft.pages);
    r("preview-page").value = "1";
    if (draft.orientation && !item.userOrientationSet) {
      if ([...f.orientation.options].some(o => o.value === String(draft.orientation))) {
        f.orientation.value = String(draft.orientation);
      }
    }
    requestQuote();
    r("file-info").textContent = size + " · " + draft.pages + (draft.pages === 1 ? " page" : " pages");
    r("preview").hidden = false;
    updatePreview(true);
    rate();
    return draft;
  }).catch((err) => {
    if (selection === item) {
      selection = null;
      r("file-info").textContent = size + " · Document unavailable";
      rate();
    }
    throw err;
  });
  return item.promise;
}
function applyColorFilter() {
  const img = r("thumbnail");
  if (img) img.style.filter = f.mode.value === "BW" ? "grayscale(100%)" : "none";
}
let previewTimer = null, previewRequestId = 0, lastPreviewUrl = "";
function updatePreview(force = false) {
  applyColorFilter();
  const img = r("thumbnail"), msg = r("preview-message");
  if (!selection?.draft) {
    if (img) { img.removeAttribute("src"); img.style.display = "none"; }
    if (msg) msg.textContent = "";
    lastPreviewUrl = "";
    return;
  }
  let pageVal = parseInt(r("preview-page").value, 10);
  const maxPages = parseInt(r("preview-page").max, 10) || selection.draft.pages || 1;
  if (isNaN(pageVal) || pageVal < 1) pageVal = 1;
  else if (pageVal > maxPages) pageVal = maxPages;
  r("preview-page").value = String(pageVal);

  const params = new URLSearchParams({
    page: String(pageVal),
    orientation: f.orientation.value || "3",
    layout: f.layout.value || "1",
    paper: f.paper.value || "A4",
    mode: f.mode.value || "BW",
    page_range: f.page_range.value || ""
  });
  const url = "/api/customer/drafts/"+encodeURIComponent(selection.draft.draft)+"/preview?"+params.toString();
  if (!force && url === lastPreviewUrl && img.getAttribute("src") === url) return;

  clearTimeout(previewTimer);
  const thisReqId = ++previewRequestId;
  previewTimer = setTimeout(() => {
    if (thisReqId !== previewRequestId || !selection?.draft) return;
    if (msg) msg.textContent = "Loading preview…";
    img.onload = () => {
      if (thisReqId !== previewRequestId) return;
      lastPreviewUrl = url;
      img.style.display = "block";
      if (msg) msg.textContent = "";
    };
    img.onerror = () => {
      if (thisReqId !== previewRequestId) return;
      img.style.display = "none";
      if (msg) {
        msg.innerHTML = 'Preview unavailable. You can still print this file. <button type="button" class="secondary" id="preview-retry">Retry</button>';
        const retryBtn = r("preview-retry");
        if (retryBtn) retryBtn.onclick = (e) => { e.preventDefault(); updatePreview(true); };
      }
    };
    img.src = url;
  }, 100);
}
r("preview").ontoggle = () => updatePreview(true);
f.oninput = rate;
f.onchange = async (event) => {
  if (event.target !== f.file) { rate(); return; }
  feedback.textContent = "";
  documents = Array.from(f.file.files).slice(0,10).map(file => ({file, options:null, selection:null}));
  activeDocument = null;
  if (!documents.length) { clearSelection(); drawDocuments(); return; }
  await chooseDocument(documents[0]);
  if (f.file.files.length > 10) feedback.textContent = "Choose up to 10 documents at a time.";

};
function stage(order) {
  if (order.state === "Cancelled") return "Cancelled";
  if (order.payment === "Refunded") return "Refunded";
  if (order.state === "Upload expired") return "Upload expired";
  if (order.payment !== "Paid") return order.payment === "Failed" ? "Payment failed" : "Payment pending";
  if (order.collected) return "Collected";
  if (order.state === "Completed") return "Ready for pickup";
  if (order.state === "Failed") return "Printing failed";
  return order.print_stage || order.state;
}
function awaitingCapture(order) {
  return order.state === "Awaiting payment" && order.payment === "Pending" && (order.capture_pending || trackingOrder === order.id);
}
function updatePrintStep(orders) {
  const order = orders.find(o => o.id === trackingOrder && o.payment === "Paid");
  const first = order && r("print-step").hidden;
  r("print-step").hidden = !order;
  r("upload-step").hidden = Boolean(order);
  if (!order) { lastStatus = ""; return; }
  const current = stage(order), notice = order.printer_notice || "";
  const signature = [order.id,current,notice].join("|");
  if (lastStatus === signature) return;
  lastStatus = signature;
  const preparing = ["Queued","Printing","Payment verified"].includes(current);
  r("print-heading").textContent = preparing ? "Payment successful! Preparing your print." : current;
  const art = document.querySelector(".printer-drawing");
  if (!preparing) art.classList.remove("animate");
  if (first && preparing && sessionStorage.getItem("ap-animated") !== order.id) {
    art.classList.add("animate");
    sessionStorage.setItem("ap-animated",order.id);
  }
  r("print-receipt").textContent = [order.document, money(order.amount), order.id].join(" · ");
  r("print-status").textContent = "Print status: " + current + (notice ? " · " + notice : "");
  r("success-receipt").href = base(order.id)+"/receipt";
  const index = stages.indexOf(current);
  const visibleIndex = Math.max(0,index);
  r("timeline").innerHTML = stages.map((name,i) => '<li' + (i <= visibleIndex ? ' class="done"' : '') +
    (i === index ? ' aria-current="step"' : '') + '>' + name + '</li>').join("");
  if (first) {
    checkoutFeedback.textContent = "";
    r("print-heading").focus({preventScroll:true});
  }
}
r("another-print").onclick = () => {
  track(null);
  pendingTestOrder = null;
  document.querySelector(".printer-drawing").classList.remove("animate");
  updatePrintStep([]);
  feedback.textContent = checkoutFeedback.textContent = "";
  if (documents.length) chooseDocument(documents[0]);
  else f.file.focus({preventScroll:true});
};
const cardState = new Map();
function renderCards(orders, testMode) {
  const list = r("myorders");
  if (!orders.length) {
    if (!list.querySelector(".empty")) list.innerHTML = empty("No orders yet","Your prints and receipts will appear here.");
    cardState.clear(); return;
  }
  list.querySelector(".empty")?.remove();
  const remaining = new Set(orders.map(o=>o.id));
  for (const [id,card] of cardState) if (!remaining.has(id)) {card.el.remove(); cardState.delete(id);}
  let next = null;
  for (const o of [...orders].reverse()) {
    const check = awaitingCapture(o);
    const key = JSON.stringify([o,Boolean(check),testMode,available]);
    let card = cardState.get(o.id);
    if (!card) {
      card = {el:document.createElement("details")};
      card.el.className = "order-card";
      cardState.set(o.id,card);
    }
    if (card.el.parentNode !== list || card.el.nextElementSibling !== next) list.insertBefore(card.el,next);
    next = card.el;
    if (key === card.key) continue;
    card.key = key;
    const unpaid = o.state === "Awaiting payment";
    const ready = o.state === "Completed" && o.payment === "Paid";
    const id = esc(o.id), url = base(o.id);
    const button = (action,label) => '<button type="button" class="secondary" data-'+action+'="'+id+'">'+label+'</button>';
    const showQR = card.el.querySelector("[data-qr-image]");
    card.el.innerHTML = '<summary><strong class="order-heading">'+esc(o.document)+'</strong><b>'+money(o.amount)+'</b><span class="order-meta">'+esc(o.payment)+' · '+esc(stage(o))+'</span></summary>'+
      '<div class="order-body"><p><b>'+id+'</b></p><p>'+o.pages+' pages × '+o.copies+' · '+(o.mode === "BW" ? "B&W" : "Colour")+' · '+esc(o.paper)+' · '+(o.duplex ? "Double-sided" : "Single-sided")+'</p>'+
      (o.printer_notice ? '<p>'+esc(o.printer_notice)+'</p>' : '')+
      (unpaid ? '<p>'+(check ? checkingPayment : paymentFirst)+'</p>' : '')+
      (o.state === "Cancelled" && o.payment === "Paid" ? '<p>Payment was captured after cancellation. Contact the shop.</p>' : '')+
      '<div class="actions">'+(unpaid && !check ? button("pay","Pay now") : '')+
      (testMode && unpaid && o.checkout_started ? button("check","Check payment") : '')+
      (unpaid && !check ? button("cancel","Cancel order") : '')+
      (["Paid","Refunded"].includes(o.payment) ? '<a class="secondary" download href="'+url+'/receipt">Download receipt</a>' : '')+
      (ready && !o.collected ? button("qr","Pickup QR") : '')+
      (ready && o.retained && available ? button("reprint","Reprint &amp; pay") : '')+
      '</div></div>';
    if (showQR && ready && !o.collected) card.el.lastElementChild.append(showQR);
  }
}
async function refresh() {
  const version = ++refreshVersion;
  const data = await send("/api/customer/session", "POST", {});
  if (version !== refreshVersion) return;
  settings = data.settings;
  updatePrintStep(data.orders);
  pendingTestOrder = data.test_payments ? data.orders.find(o=>o.id===trackingOrder && awaitingCapture(o))?.id : null;
  const trackedOrder = data.orders.find(o=>o.id===trackingOrder);
  if (trackedOrder?.payment === "Failed") checkoutFeedback.textContent = "Payment failed. " + paymentFirst;
  const shop = [settings.name,settings.address,settings.contact,settings.logo];
  if (lastShop !== JSON.stringify(shop)) {
    lastShop = JSON.stringify(shop);
    const label = [settings.name === "AutoPrint" ? "" : settings.name,settings.address,settings.contact].filter(Boolean).join(" · ");
    r("shop").hidden = !label && !settings.logo;
    r("shop").innerHTML = (settings.logo ? '<img class="shop-logo" alt="" src="'+esc(settings.logo)+'">' : '') + esc(label);
  }
  const time = new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Kolkata",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date());
  const open = settings.opens === settings.closes || (settings.opens < settings.closes ? time >= settings.opens && time < settings.closes : time >= settings.opens || time < settings.closes);
  available = settings.uploads && open;
  r("instructions").textContent = !settings.uploads ? "Customer uploads are paused." : !open
    ? "Shop hours are "+settings.opens+"–"+settings.closes+" IST."
    : "Select a file to upload privately and check its pages. Up to "+settings.max_mb+" MB."+(data.test_payments ? " Razorpay Test Mode." : "");
  if (lastPaper !== JSON.stringify(settings.sizes)) {
    lastPaper = JSON.stringify(settings.sizes);
    const previous = f.paper.value;
    f.paper.innerHTML = settings.sizes.map(x=>"<option>"+esc(x)+"</option>").join("");
    if (settings.sizes.includes(previous)) f.paper.value = previous;
  }
  syncUploadButton();
  rate();
  renderCards(data.orders,data.test_payments);
  refreshPrinters();
}
async function payFor(order) {
  track(order.id);
  feedback.textContent = checkoutFeedback.textContent = "Total " + money(order.amount) + ". Opening Razorpay Checkout…";
  try {
    await openCheckout(order.id);
  } catch (err) {
    checkoutFeedback.textContent = err.message + " Retry this order using Pay now.";
  }
  await refresh();
}
f.onsubmit = async e => {
  e.preventDefault();
  if (busy || checkoutActive || !available) return;
  const file = activeDocument?.file || f.file.files[0];
  if (!file) { requireDocument(); return; }
  busy = true; syncUploadButton();
  r("pay-label").textContent = "Preparing payment…";
  feedback.textContent = "Checking your document and price…";
  try {
    const options = {mode:f.mode.value,copies:Number(f.copies.value),paper:f.paper.value,duplex:f.duplex.value==="true",page_range:f.page_range.value,orientation:Number(f.orientation.value),layout:Number(f.layout.value)};
    loadCheckout().catch(()=>{});
    const draft = await prepare(file);
    let quote;
    try {
      quote = await send("/api/customer/drafts/"+encodeURIComponent(draft.draft)+"/quote","POST",options);
    } catch (err) {
      if (err.status === 404) throw Error("The shop server needs to restart to enable page selection and pricing. Payment is on hold.");
      throw err;
    }
    const order = await send("/api/customer/drafts/"+encodeURIComponent(draft.draft)+"/order","POST",options);
    if (order.amount !== quote.amount || order.pages !== quote.pages) {
      throw Error("The price changed. Review the saved order before paying.");
    }
    documents = documents.filter(item => item !== activeDocument);
    activeDocument = null;
    f.file.value = "";
    clearSelection();
    drawDocuments();
    await payFor(order);
    feedback.textContent = "";
  } catch (err) {
    feedback.textContent = err.message;
    if (err.status === 410) {
      if (activeDocument) activeDocument.selection = null;
      clearSelection();
    }
  } finally {
    busy = false; r("pay-label").textContent = "Upload & Pay"; syncUploadButton();
  }
};
let checkoutLoader;
function loadCheckout() {
  if (window.Razorpay) return Promise.resolve();
  if (!checkoutLoader)
    checkoutLoader = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://checkout.razorpay.com/v1/checkout.js";
      const timeout = setTimeout(() => s.onerror(), 20000);
      s.onload = () => {
        clearTimeout(timeout);
        if (typeof window.Razorpay === "function") resolve();
        else s.onerror();
      };
      s.onerror = () => {
        clearTimeout(timeout);
        s.remove();
        checkoutLoader = null;
        reject(Error("Could not load checkout. Try again."));
      };
      document.head.append(s);
    });
  return checkoutLoader;
}
async function verifyPayment(oid, proof = null) {
  const status = await send("/api/customer/orders/" + encodeURIComponent(oid) + "/verify", "POST", proof);
  if (status.payment === "Paid") track(oid);
  if (status.payment === "Failed" && trackingOrder === oid) track(null);
  checkoutFeedback.textContent = status.payment === "Paid"
    ? "Payment verified. Print status: " + status.state + "."
    : status.payment === "Failed"
      ? "Payment failed. " + paymentFirst
      : checkingPayment;
  await refresh();
  return status;
}
async function verifyCheckout(oid, proof) {
  let status = await verifyPayment(oid, proof);
  for (const delay of [2000, 4000, 8000, 16000]) {
    if (status.payment !== "Pending") return;
    await new Promise((resolve) => setTimeout(resolve, delay));
    if (document.hidden) return;
    status = await verifyPayment(oid, proof);
  }
}
async function openCheckout(oid) {
  if (checkoutActive) return;
  checkoutActive = true;
  syncUploadButton();
  let settled = false;
  function releaseCheckout() {
    checkoutActive = false;
    syncUploadButton();
  }
  try {
    const [opts] = await Promise.all([
      send("/api/customer/orders/" + encodeURIComponent(oid) + "/checkout", "POST", {}),
      loadCheckout(),
    ]);
    const checkout = new Razorpay({
      ...opts,
      modal: { ondismiss: () => {
        if (settled) return;
        settled = true;
        releaseCheckout();
        if (trackingOrder === oid) track(null);
        checkoutFeedback.textContent = paymentFirst;
        refresh().catch((err) => { checkoutFeedback.textContent = err.message; });
      } },
      handler: async (proof) => {
        if (settled) return;
        settled = true;
        track(oid);
        checkoutFeedback.textContent = checkingPayment;
        try {
          await verifyCheckout(oid, proof);
        } catch (err) {
          checkoutFeedback.textContent = err.message;
        } finally {
          releaseCheckout();
        }
      },
    });
    checkout.on("payment.failed", () => {
      if (settled) return;
      checkoutFeedback.textContent = paymentFirst;
      if (opts.key.startsWith("rzp_test_")) {
        // Record the gateway's result without letting a late failed-attempt response
        // overwrite a later success or dismissal message.
        send("/api/customer/orders/" + encodeURIComponent(oid) + "/verify", "POST", null)
          .then(() => refresh())
          .catch(() => {});
      }
    });
    checkout.open();
    checkoutFeedback.textContent = "";
  } catch (err) {
    releaseCheckout();
    throw err;
  }
}
const reprintKeys = new Map();
r("myorders").onclick = async e => {
  const b = e.target.closest("button");
  if (!b || busy || checkoutActive || b.disabled) return;
  const d = b.dataset;
  if (d.qr) {
    const body = b.closest(".order-body");
    if (body.querySelector("[data-qr-image]")) return;
    const img = document.createElement("img");
    img.alt = "Pickup QR for order " + d.qr;
    img.width = img.height = 164;
    img.dataset.qrImage = "";
    img.src = base(d.qr)+"/pickup.png";
    body.append(img);
    return;
  }
  busy = true; b.disabled = true; syncUploadButton();
  try {
    if (d.check) await verifyPayment(d.check);
    if (d.pay) await openCheckout(d.pay);
    if (d.cancel && confirm("Cancel this unpaid print order?")) {
      await send(base(d.cancel)+"/cancel","POST",{});
      await refresh();
    }
    if (d.reprint && confirm("Create a new paid order using this document and the same print options? Review the new total in Checkout.")) {
      let key = reprintKeys.get(d.reprint);
      if (!key) {
        key = Array.from(crypto.getRandomValues(new Uint8Array(16)), x=>x.toString(16).padStart(2,"0")).join("");
        reprintKeys.set(d.reprint,key);
      }
      const order = await send(base(d.reprint)+"/reprint","POST",{request_id:key});
      reprintKeys.delete(d.reprint);
      await payFor(order);
    }
  } catch (err) { checkoutFeedback.textContent = err.message; }
  finally { busy = false; b.disabled = false; syncUploadButton(); }
};
let polling = false, timer;
async function poll() {
  if (document.hidden || polling) return;
  polling = true;
  let delay = 10000;
  try {
    if (pendingTestOrder && !checkoutActive) await verifyPayment(pendingTestOrder);
    else await refresh();
    if (!cardState.size) delay = 30000;
  } catch (err) {
    checkoutFeedback.textContent = err.message;
    delay = 20000;
  } finally {
    polling = false;
    clearTimeout(timer);
    if (!document.hidden) timer = setTimeout(poll,delay);
  }
}
document.addEventListener("visibilitychange",() => {
  document.documentElement.classList.toggle("tab-hidden",document.hidden);
  clearTimeout(timer);
  if (!document.hidden) poll();
});
document.documentElement.classList.toggle("tab-hidden",document.hidden);
poll();

let quoteTimer, quoteVersion = 0;
function requestQuote() {
  clearTimeout(quoteTimer);
  const version = ++quoteVersion;
  if (!selection?.draft) return;
  const draftId = selection.draft.draft;
  r("estimate").textContent = "Calculating…";
  quoteTimer = setTimeout(async () => {
    try {
      const quote = await send("/api/customer/drafts/"+encodeURIComponent(draftId)+"/quote","POST",{
        mode:f.mode.value,copies:Number(f.copies.value),paper:f.paper.value,
        duplex:f.duplex.value==="true",page_range:f.page_range.value,orientation:Number(f.orientation.value),layout:Number(f.layout.value)
      });
      if (version !== quoteVersion) return;
      r("estimate").textContent = money(quote.amount);
      const first = f.page_range.value.trim().match(/^\d+/);
      if (first && r("preview-page").value !== first[0]) {
        r("preview-page").value = first[0];
        updatePreview();
      }
      r("rate").textContent = quote.pages+" pages × "+quote.copies+" copies × "+money(quote.rate)+
        (quote.minimum_adjustment ? " + "+money(quote.minimum_adjustment)+" minimum adjustment" : "");
      feedback.textContent = "";
    } catch (err) {
      if (version === quoteVersion) {
        r("estimate").textContent = "—";
        feedback.textContent = err.status === 404 ? "The shop server needs to restart to enable page selection and pricing." : err.message;
      }
    }
  }, 250);
}
f.addEventListener("input", requestQuote);

const optionNames = ["mode","copies","paper","duplex","page_range","orientation","layout"];
function drawDocuments() {
  r("document-list").innerHTML = documents.length > 0
    ? documents.map((item,index)=>'<button class="secondary" type="button" data-document="'+index+'" aria-pressed="'+(item === activeDocument)+'">'+esc(item.file.name)+'</button>').join("")
    : "";
}
async function chooseDocument(item) {
  if (activeDocument) {
    activeDocument.options = Object.fromEntries(optionNames.map(name=>[name,f.elements[name].value]));
    activeDocument.selection = selection;
  }
  selection = null;
  clearSelection();
  activeDocument = item;
  syncUploadButton();
  if (item.options) for (const name of optionNames) f.elements[name].value = item.options[name];
  else {
    f.orientation.value = "0";
    f.layout.value = "1";
    f.paper.value = settings.sizes?.[0] || "A4";
    f.copies.value = "1";
    f.page_range.value = "";
    f.mode.value = "BW";
    f.duplex.value = "false";
    f.orientation.value = (item.draft?.orientation) ? String(item.draft.orientation) : "3";
    f.layout.value = "1";
  }
  drawDocuments();
  try {
    if (item.selection?.draft) {
      selection = item.selection;
      r("file-name").textContent = item.file.name;
      r("file-info").textContent = Math.ceil(item.file.size/1024)+" KB · "+selection.draft.pages+" pages";
      r("preview").hidden = false;
      r("preview-page").max = String(selection.draft.pages);
      r("preview-page").value = "1";
      updatePreview(true);
      rate();
      requestQuote();
    } else await prepare(item.file);
  } catch (err) {
    if (activeDocument === item) feedback.textContent = err.message;
  }
}
r("document-list").onclick = e => {
  const button = e.target.closest("[data-document]");
  if (button && !busy && !checkoutActive) chooseDocument(documents[Number(button.dataset.document)]);
};

async function refreshPrinters() {
  try {
    const printers=await api("/api/customer/printers");
    const printer=printers.find(p=>p.mode===f.mode.value);
    r("printer-availability").textContent = !printer ? "Printer assignment pending" : !printer.online ? "Printer offline" : printer.available ? "Printer available" : "Printer busy or paused";
    const caps=printer?.capabilities || {};
    if (caps.papers) for (const option of f.paper.options) option.disabled=!caps.papers.includes(option.value);
    f.duplex.options[1].disabled=Boolean(Object.keys(caps).length && !caps.duplex);
    f.copies.max=String(Math.min(100,caps.max_copies || 100));
    const orientation=f.orientation.value || "3", layout=f.layout.value;
    const availOrientations = (caps.orientations && caps.orientations.length)
      ? caps.orientations.filter(v=>[3,4].includes(v))
      : [3, 4];
    f.orientation.innerHTML=availOrientations.map(v=>'<option value="'+v+'">'+(v===3?"Portrait":"Landscape")+'</option>').join("");
    f.layout.innerHTML=(caps.layouts || [1]).map(v=>'<option value="'+v+'">'+v+'</option>').join("");
    if ([...f.orientation.options].some(o=>o.value===orientation)) f.orientation.value=orientation;
    else if (f.orientation.options.length) f.orientation.selectedIndex = 0;
    if ([...f.layout.options].some(o=>o.value===layout)) f.layout.value=layout;
  } catch (err) { r("printer-availability").textContent="Printer availability unavailable"; }
}
f.orientation.addEventListener("change", () => {
  if (activeDocument) activeDocument.userOrientationSet = true;
  updatePreview(true);
  rate();
});
f.orientation.addEventListener("input", () => {
  if (activeDocument) activeDocument.userOrientationSet = true;
  updatePreview(true);
  rate();
});
f.mode.addEventListener("change", () => {
  applyColorFilter();
  updatePreview(true);
  refreshPrinters();
  rate();
});
f.mode.addEventListener("input", () => {
  applyColorFilter();
  updatePreview(true);
  rate();
});
r("preview-page").addEventListener("input", () => updatePreview());
r("preview-page").addEventListener("change", () => updatePreview());
f.paper.addEventListener("change", () => { updatePreview(true); rate(); });
f.layout.addEventListener("change", () => { updatePreview(true); rate(); });
