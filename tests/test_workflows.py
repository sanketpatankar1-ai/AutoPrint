"""Exercise the actual ASGI routes and SQLite transactions in disposable directories."""
import hashlib
import hmac
import io
import json
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfWriter
from starlette.websockets import WebSocketDisconnect

from autoprint.app import app
from autoprint import commerce
from autoprint.store import db, housekeeping, now, secret_hash

WRITE={"X-AutoPrint":"1"}
AGENT={"Authorization":"Bearer "+"a"*48}
ADMIN="staff@example.test"

@pytest.fixture
def shop(tmp_path,monkeypatch):
    monkeypatch.setenv("AUTOPRINT_DATA",str(tmp_path))
    monkeypatch.setenv("PRINT_AGENT_TOKEN","a"*48)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET","isolated-test-secret")
    monkeypatch.setenv("RAZORPAY_KEY_ID","test-key")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET","isolated-checkout-secret")
    monkeypatch.setattr(commerce,"gateway_order",lambda oid,amount:"test_gateway_"+oid)
    password=secrets.token_urlsafe(24)
    with TestClient(app) as c:
        with db(True) as conn:
            conn.execute("INSERT INTO users(email,hash,role) VALUES(?,?,'owner')",(ADMIN,secret_hash(password)))
        yield c,password

def login(c,password,email=ADMIN):
    r=c.post("/api/login",headers=WRITE,json={"email":email,"password":password})
    assert r.status_code==200,r.text

def upload(c,mode="BW",name="print.pdf"):
    d=c.post("/api/customer/session",headers=WRITE,json={})
    assert d.status_code==200
    pdf=PdfWriter()
    for _ in range(2):
        pdf.add_blank_page(595,842)
    out=io.BytesIO()
    pdf.write(out)
    res=c.post("/api/customer/orders",params={"name":name,"mode":mode,"copies":2},headers=WRITE,content=out.getvalue())
    assert res.status_code==200,res.text
    o=res.json()
    checkout=c.post("/api/customer/orders/"+o["id"]+"/checkout",headers=WRITE)
    assert checkout.status_code==200
    return {**o,"gateway":checkout.json()["order_id"]}

def deliver(c,kind,payload,when=None,valid=True):
    body=json.dumps({"event":kind,"payload":payload,"created_at":when or now()}).encode()
    sig=hmac.new(b"isolated-test-secret",body,hashlib.sha256).hexdigest() if valid else "wrong"
    return c.post("/api/webhooks/razorpay",content=body,headers={"X-Razorpay-Signature":sig})

def pay(c,o,status="captured",pid=None,**kw):
    p=dict(id=pid or "pay_"+o["id"],amount=o["amount"],currency="INR",order_id=o["gateway"],method="upi",status=status,**({"captured":True} if status=="captured" else {}))
    return deliver(c,"payment."+status,{"payment":{"entity":p}},**kw)

def device(c,name="Shop printer"):
    p={"name":name,"model":"Shop device","online":True,"available":True}
    assert c.post("/api/agent/heartbeat",headers=AGENT,json={"printers":[p]}).status_code==200
    pid="shop-agent/"+name
    for mode in ("BW","Colour"):
        r=c.post("/api/owner/printers",headers=WRITE,json={"id":pid,"action":mode})
        assert r.status_code==200
    return pid

def owner_data(c):
    r=c.get("/api/owner/overview")
    assert r.status_code==200,r.text
    return r.json()

def test_paid_dispatch_retry_exports_and_privacy(shop):
    c,pw=shop
    assert c.get("/api/owner/overview").status_code==401
    o=upload(c,name="=SUM(1).pdf")
    assert c.get("/api/owner/reports/payments.csv").status_code==401
    login(c,pw)
    pid=device(c)
    claim=lambda:c.post("/api/agent/claim",headers=AGENT).json()
    assert claim() is None
    assert pay(c,o,valid=False).status_code==401
    assert pay(c,o,"failed",pid="failed_attempt").status_code==200
    assert claim() is None
    assert owner_data(c)["today"]["gross"]==0
    assert pay(c,o).status_code==200
    assert pay(c,o).status_code==200
    assert pay(c,o,"authorized").status_code==200
    pause=lambda action:c.post("/api/owner/printers",headers=WRITE,json={"id":pid,"action":action})
    pause("pause")
    assert claim() is None
    pause("resume")
    with ThreadPoolExecutor(2) as pool:
        first,second=list(pool.map(lambda _:claim(),range(2)))
    assert first==second and first["printer"]==pid
    oid=o["id"]
    path="/api/agent/jobs/"+oid
    hdr={**AGENT,"X-Job-Claim":first["claim"]}
    assert c.get(path+"/file",headers={"X-Job-Claim":first["claim"]}).status_code==401
    assert c.get(path+"/file",headers=hdr).content.startswith(b"%PDF-")
    assert c.post(path+"/finish",headers=hdr,json={"state":"Failed","error":"Paper jam"}).status_code==200
    assert c.post("/api/owner/orders/"+oid+"/retry",headers=WRITE).status_code==200
    second=claim()
    assert second["claim"]!=first["claim"]
    assert c.post(path+"/finish",headers=hdr,json={"state":"Completed"}).status_code==404
    hdr["X-Job-Claim"]=second["claim"]
    for _ in range(2):
        assert c.post(path+"/finish",headers=hdr,json={"state":"Completed"}).status_code==200
    totals=owner_data(c)["today"]
    assert (totals["gross"],totals["paid"],totals["failed"],totals["pages"],totals["documents"],totals["failed_jobs"])==(800,1,1,4,1,1)
    assert c.get(path+"/file",headers=hdr).status_code==409
    for kind in ("daily","weekly","monthly","printing","payments","printers"):
        for ext in ("csv","xlsx"):
            r=c.get(f"/api/owner/reports/{kind}.{ext}")
            assert r.status_code==200
            if ext=="xlsx":
                wb=load_workbook(io.BytesIO(r.content))
                assert wb["Summary"]["G2"].value==8
            elif kind=="printing":
                assert "'=SUM(1).pdf" in r.text
    listing=c.get("/api/owner/list/orders",params={"preset":"today","q":oid,"payment":"Paid","state":"Completed"}).json()
    assert listing["total"]==1
    assert not {"content","claim","gateway","token"} & listing["items"][0].keys()
    assert c.get("/api/owner/list/orders?payment=Pending").json()["total"]==0
    assert len(c.get("/api/owner/list/audit").json()["items"])>=12

def test_refund_out_of_order_and_revenue_period(shop):
    c,pw=shop
    login(c,pw)
    o=upload(c,"Colour")
    device(c)
    yesterday=now()-86400
    ref={"refund":{"entity":{"id":"refund_one","status":"processed","payment_id":"pay_"+o["id"],"amount":1200}}}
    assert deliver(c,"refund.processed",ref).status_code==200
    assert pay(c,o,when=yesterday).status_code==200
    assert c.post("/api/agent/claim",headers=AGENT).json() is None
    assert deliver(c,"refund.processed",ref).status_code==200
    s=c.get("/api/owner/analytics?preset=today").json()["summary"]
    assert (s["gross"],s["refunds"],s["net"])==(0,1200,-1200)
    y=c.get("/api/owner/analytics?preset=yesterday").json()["summary"]
    assert y["Colour"]==4000 and y["average"]==4000
    assert c.get("/api/owner/list/payments?payment=Refunded").json()["total"]==1
    assert c.get("/api/owner/list/orders").json()["items"][0]["state"]=="Refund hold"
    assert c.get("/api/owner/analytics?preset=custom&start=2026-11-02&end=2026-11-01").status_code==422

def test_settings_sessions_permissions_and_live_updates(shop):
    c,pw=shop
    login(c,pw)
    s=c.get("/api/owner/settings").json()
    s.update(bw=350,sizes=["A4","A3"])
    assert c.put("/api/owner/settings",json=s).status_code==403
    with c.websocket_connect("/api/owner/live",headers={"origin":"http://testserver"}) as live:
        rev=live.receive_json()["revision"]
        assert c.put("/api/owner/settings",json=s,headers=WRITE).status_code==200
        assert live.receive_json()["revision"]>rev
    with pytest.raises(WebSocketDisconnect), c.websocket_connect("/api/owner/live",headers={"origin":"https://other.test"}):
        pass
    o=upload(c)
    assert o["amount"]==1400
    d=c.post("/api/customer/session",headers=WRITE).json()
    assert d["settings"]["bw"]==350 and "cleanup" not in d["settings"]
    assert "test-secret" not in json.dumps(d)
    first=c.cookies.get("ap_customer")
    c.cookies.delete("ap_customer")
    assert not c.post("/api/customer/session",headers=WRITE).json()["orders"]
    assert c.post("/api/customer/orders/"+o["id"]+"/checkout",headers=WRITE).status_code==409
    assert c.cookies.get("ap_customer")!=first
    assert c.post("/api/logout",headers=WRITE).status_code==200
    assert c.get("/api/owner/list/orders").status_code==401
    with db(True) as conn:
        conn.execute("INSERT INTO users(email,hash,role) VALUES(?,?,'admin')",("operator@example.test",secret_hash(pw)))
    login(c,pw,"operator@example.test")
    assert c.get("/api/owner/overview").status_code==200
    assert c.put("/api/owner/settings",headers=WRITE,json=s).status_code==403
    assert c.get("/api/owner/list/audit").status_code==403

def test_cleanup_disconnect_and_rate_limits(shop):
    c,pw=shop
    login(c,pw)
    o=upload(c)
    pid=device(c)
    with db(True) as conn:
        conn.execute("UPDATE agents SET seen=?",(now()-60,))
        conn.execute("UPDATE orders SET created=? WHERE id=?",(now()-90000,o["id"]))
    housekeeping()
    with db() as conn:
        assert not conn.execute("SELECT online FROM printers WHERE id=?",(pid,)).fetchone()[0]
        assert not conn.execute("SELECT 1 FROM files").fetchone()
    assert pay(c,o).status_code==200
    assert owner_data(c)["today"]["failed_jobs"]==1
    assert c.post("/api/owner/orders/"+o["id"]+"/retry",headers=WRITE).status_code==409
    c.post("/api/logout",headers=WRITE)
    for _ in range(8):
        assert c.post("/api/login",headers=WRITE,json={"email":ADMIN,"password":"wrong"}).status_code==401
    assert c.post("/api/login",headers=WRITE,json={"email":ADMIN,"password":pw}).status_code==429

def test_two_printer_routing_and_database_guard(shop):
    import sqlite3
    c,pw=shop
    login(c,pw)
    mono,colour=(upload(c,mode) for mode in ("BW","Colour"))
    devices=[{"name":n,"model":"Test queue","online":True,"available":True} for n in ("Mono","Colour")]
    c.post("/api/agent/heartbeat",headers=AGENT,json={"printers":devices})
    for mode,name in (("BW","Mono"),("Colour","Colour")):
        r=c.post("/api/owner/printers",json={"id":"shop-agent/"+name,"action":mode},headers=WRITE)
        assert r.status_code==200
    with pytest.raises(sqlite3.IntegrityError),db(True) as conn:
        conn.execute("UPDATE orders SET state='Queued' WHERE id=?",(mono["id"],))
    assert pay(c,{**mono,"amount":1}).status_code==200
    assert owner_data(c)["today"]["gross"]==0
    assert pay(c,mono).status_code==200
    assert pay(c,colour).status_code==200
    for o,name in ((mono,"Mono"),(colour,"Colour")):
        j=c.post("/api/agent/claim",params={"printer":name},headers=AGENT).json()
        assert (j["id"],j["printer"])==(o["id"],"shop-agent/"+name)
    assert len(c.get("/api/agent/active",headers=AGENT).json())==2
    assert c.get("/api/agent/active").status_code==401

def test_agent_waits_for_spool_completion_and_recovers(monkeypatch):
    import sqlite3
    import sys
    import types
    import print_agent
    from unittest.mock import Mock
    monkeypatch.setitem(sys.modules,"cups",types.SimpleNamespace(IPPError=RuntimeError))
    job=dict(id="AP-TEST",claim="unique-test-lease",printer="shop-agent/Mono",mode="BW",copies=2,duplex=True,paper="A4")
    gateway=Mock(return_value=b"%PDF-1.4")
    monkeypatch.setattr(print_agent,"api",gateway)
    spooler=Mock()
    spooler.printFile.return_value=42
    spooler.getJobAttributes.return_value={"job-state":5}
    with sqlite3.connect(":memory:") as journal:
        journal.execute("CREATE TABLE jobs(lease TEXT PRIMARY KEY,spool INTEGER)")
        print_agent.process(spooler,journal,job)
        print_agent.process(spooler,journal,job)
        spooler.printFile.assert_called_once()
        assert gateway.call_count==2
        assert gateway.call_args.args==("jobs/AP-TEST/spool",{"phase":"Printing"},job["claim"])
        assert spooler.printFile.call_args.args[-1]["sides"]=="two-sided-long-edge"
        # Simulate losing the accepted spool ID at the handoff boundary.
        journal.execute("UPDATE jobs SET spool=NULL")
        spooler.getJobs.return_value={42:{"job-name":"AP-TEST unique-test-lease"}}
        spooler.getJobAttributes.return_value={"job-state":9}
        print_agent.process(spooler,journal,job)
        spooler.printFile.assert_called_once()
        assert gateway.call_args.args[1]["state"]=="Completed"

def test_local_checkout_verifies_capture_before_dispatch(shop, monkeypatch):
    from unittest.mock import Mock
    c, password = shop
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_isolated")
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET")
    login(c, password)
    device(c)
    o = upload(c)
    path = f"/api/customer/orders/{o['id']}/verify"
    payment = dict(id="pay_test123", order_id=o["gateway"], amount=o["amount"],
                   currency="INR", status="authorized", captured=False, amount_refunded=0)
    lookup = Mock(return_value={"items": [payment]})
    monkeypatch.setattr(commerce, "gateway_request", lookup)
    signature = hmac.new(b"isolated-checkout-secret", (o["gateway"]+"|pay_test123").encode(),
                         hashlib.sha256).hexdigest()
    proof = dict(razorpay_payment_id=payment["id"], razorpay_order_id=o["gateway"], razorpay_signature=signature)
    def verify(body):
        return c.post(path, json=body, headers=WRITE)
    assert verify({**proof, "razorpay_signature": "0"*64}).status_code == 400
    lookup.assert_not_called()
    assert verify(proof).json()["payment"] == "Pending"
    assert c.post("/api/agent/claim", headers=AGENT).json() is None
    payment.update(status="captured", captured=True)
    for override in ({"order_id":"unrelated"}, {"amount":1}, {"currency":"USD"},
                     {"captured":False}, {"amount_refunded":1}, {"refund_status":"partial"}):
        lookup.return_value = {"items":[{**payment, **override}]}
        assert verify(None).json()["state"] == "Awaiting payment"
    lookup.return_value = {"items":[payment]}
    assert verify(proof).json() == {"state":"Queued", "payment":"Paid"}
    assert verify(None).json()["state"] == "Queued"
    lookup.assert_called_with("orders/"+o["gateway"]+"/payments")
    assert owner_data(c)["today"]["gross"] == o["amount"]
    assert c.post("/api/agent/claim", headers=AGENT).json()["id"] == o["id"]
    c.cookies.delete("ap_customer")
    c.post("/api/customer/session",headers=WRITE)
    assert verify(None).status_code == 404

def test_local_checkout_failed_or_unavailable_keeps_job_unpaid(shop, monkeypatch):
    from fastapi import HTTPException
    c, _ = shop
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_isolated")
    o = upload(c)
    path = f"/api/customer/orders/{o['id']}/verify"
    def unavailable(path):
        raise HTTPException(502, "Gateway unavailable")
    monkeypatch.setattr(commerce, "gateway_request", unavailable)
    assert c.post(path,headers=WRITE).status_code == 502
    failed = dict(id="pay_failed123", order_id=o["gateway"], amount=o["amount"], currency="INR", status="failed")
    monkeypatch.setattr(commerce, "gateway_request", lambda path: {"items":[failed]})
    assert c.post(path,headers=WRITE).json() == {"payment":"Failed","state":"Awaiting payment"}
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_isolated")
    assert c.post(path,headers=WRITE).status_code == 409


def test_gateway_order_uses_verified_tls_when_python_has_no_roots(monkeypatch):
    import ssl
    from unittest.mock import Mock

    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_isolated")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "isolated-secret")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    monkeypatch.setattr(commerce.ssl, "create_default_context", lambda: empty)
    open_request = Mock(return_value=io.BytesIO(b'{"id":"order_test","amount":200,"currency":"INR"}'))
    monkeypatch.setattr(commerce, "urlopen", open_request)
    assert commerce.gateway_order("AP-TEST", 200) == "order_test"
    context = open_request.call_args.kwargs["context"]
    assert context.get_ca_certs() and context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_one_verified_capture_dispatches_exactly_one_job(shop, monkeypatch):
    c, password = shop
    login(c, password)
    printer = device(c)
    creates = []
    def create_order(oid, amount):
        creates.append((oid, amount))
        return "order_isolated"
    monkeypatch.setattr(commerce, "gateway_order", create_order)
    o = upload(c, "Colour")
    checkout_url = "/api/customer/orders/" + o["id"] + "/checkout"
    with ThreadPoolExecutor(2) as pool:
        retries = list(pool.map(lambda _: c.post(checkout_url, headers=WRITE).json(), range(2)))
    assert creates == [(o["id"], 4000)]
    for checkout in retries:
        assert checkout["order_id"] == o["gateway"] and checkout["amount"] == 4000
        assert set(checkout) == {"key","order_id","amount","currency","name"}
    claim = lambda: c.post("/api/agent/claim", headers=AGENT).json()
    assert claim() is None
    pay(c, o, "authorized")
    assert claim() is None
    assert c.post("/api/customer/session",headers=WRITE).json()["orders"][0]["capture_pending"]
    # Signed events with conflicting evidence must also leave the job on hold.
    entity = dict(id="pay_"+o["id"], amount=4000, currency="INR",
                  order_id=o["gateway"], status="captured", captured=False)
    deliver(c, "payment.captured", {"payment":{"entity":entity}})
    assert claim() is None
    entity.pop("captured")
    deliver(c, "payment.captured", {"payment":{"entity":entity}})
    assert claim() is None
    with ThreadPoolExecutor(2) as pool:
        assert all(r.status_code == 200 for r in pool.map(lambda _: pay(c, o), range(2)))
    job = claim()
    assert job["id"] == o["id"] and job["printer"] == printer
    assert c.post("/api/agent/jobs/"+o["id"]+"/finish",
                  headers={**AGENT,"X-Job-Claim":job["claim"]}, json={"state":"Completed"}).is_success
    assert pay(c, o).is_success and claim() is None
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM payments WHERE oid=? AND status='Paid'", (o["id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='paid'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM outcomes WHERE oid=?", (o["id"],)).fetchone()[0] == 1


def stage_document(c, name="draft.pdf", data=None):
    if data is None:
        pdf=PdfWriter()
        pdf.add_blank_page(595,842)
        out=io.BytesIO()
        pdf.write(out)
        data=out.getvalue()
    return c.post("/api/customer/drafts",params={"name":name},headers=WRITE,content=data)


def test_private_draft_validation_price_checkout_and_receipt(shop):
    c,password=shop
    c.post("/api/customer/session",headers=WRITE,json={})
    invalid=c.post("/api/customer/drafts",params={"name":"document.pdf"},headers=WRITE,content=b"<html>not a PDF</html>")
    assert invalid.status_code==422
    assert c.post("/api/customer/drafts",params={"name":"document.svg"},headers=WRITE,content=b"<svg/>").status_code==422
    draft=stage_document(c)
    assert draft.status_code==200,draft.text
    token=draft.json()["draft"]
    assert draft.json()["pages"]==1
    assert c.get(f"/api/customer/drafts/{token}/preview").status_code==200
    assert c.get(f"/api/customer/drafts/{token}/preview").headers["content-type"]=="image/png"
    outsider=TestClient(app)
    try:
        outsider.post("/api/customer/session",headers=WRITE,json={})
        assert outsider.post(f"/api/customer/drafts/{token}/draft",headers=WRITE).status_code in (404,405)
        assert outsider.get(f"/api/customer/drafts/{token}/preview").status_code==404
    finally:
        outsider.close()
    payload={"mode":"BW","copies":3,"duplex":False,"paper":"A4"}
    url=f"/api/customer/drafts/{token}/order"
    with ThreadPoolExecutor(2) as pool:
        a,b=list(pool.map(lambda _:c.post(url,headers=WRITE,json=payload),range(2)))
    assert a.status_code==b.status_code==200
    assert a.json()==b.json() and a.json()["amount"]==600
    session=c.post("/api/customer/session",headers=WRITE,json={}).json()
    assert len(session["orders"])==1
    row=session["orders"][0]
    assert row["id"]==a.json()["id"] and row["retained"]
    assert "content" not in row and "gateway" not in row and "claim" not in row
    assert c.get(f"/api/customer/orders/{row['id']}/receipt").status_code==404
    o={"id":row["id"],"amount":row["amount"],"gateway":"test_gateway_"+row["id"]}
    checkout=c.post(f"/api/customer/orders/{row['id']}/checkout",headers=WRITE).json()
    o["gateway"]=checkout["order_id"]
    assert pay(c,o).status_code==200
    assert c.get(f"/api/customer/orders/{row['id']}/receipt").status_code==200


def test_draft_upload_size_expiry_cancel_and_owner_collection(shop):
    c,password=shop
    c.post("/api/customer/session",headers=WRITE,json={})
    with db(True) as conn:
        current=json.loads(conn.execute("SELECT value FROM settings").fetchone()[0])
        current["max_mb"]=1
        conn.execute("UPDATE settings SET value=?",(json.dumps(current),))
    large=c.post("/api/customer/drafts",params={"name":"large.pdf"},headers=WRITE,content=b"%PDF-"+b"x"*(1024*1024))
    assert large.status_code==413
    pending=stage_document(c).json()
    order=c.post(f"/api/customer/drafts/{pending['draft']}/order",headers=WRITE,json={"mode":"BW","copies":1,"duplex":False,"paper":"A4"}).json()
    assert c.get(f"/api/customer/drafts/{pending['draft']}/preview").status_code==404
    assert c.post(f"/api/customer/orders/{order['id']}/cancel",headers=WRITE).json()["state"]=="Cancelled"
    assert c.post(f"/api/customer/orders/{order['id']}/checkout",headers=WRITE).status_code==409
    # A cancelled job stays out of the queue even when its gateway reports a late capture.
    late={"id":order["id"],"amount":order["amount"],"gateway":"late_gateway"}
    with db(True) as conn:
        conn.execute("UPDATE orders SET gateway=? WHERE id=?",(late["gateway"],late["id"]))
    assert pay(c,late).status_code==200
    assert c.post("/api/agent/claim",headers=AGENT).json() is None
    assert c.get("/api/owner/list/events?preset=all").status_code==401
    login(c,password)
    assert any("captured after customer cancellation" in event["message"].lower()
               for event in c.get("/api/owner/list/events?preset=all").json()["items"])
    pid=device(c)
    assert c.post("/api/owner/orders/"+order["id"]+"/retry",headers=WRITE).status_code==409

    paid={"id":"AP-COLLECT","amount":100,"gateway":"unused"}
    sid=c.post("/api/customer/session",headers=WRITE,json={}).json()["session"]
    with db(True) as conn:
        conn.execute("""INSERT INTO orders(id,session,document,pages,mode,copies,duplex,paper,amount,created,state,finished)
            VALUES(?,?,?,?,?,?,?,?,?,?,'Completed',?)""",(paid["id"],sid,"receipt.pdf",1,"BW",1,0,"A4",100,now(),now()))
        conn.execute("INSERT INTO files VALUES(?,?)",(paid["id"],b"private"))
        conn.execute("INSERT INTO payments VALUES(?,?,?,?,?,?)",("pay_collect",paid["id"],100,"Paid","upi",now()))
    assert c.get(f"/api/customer/orders/{paid['id']}/pickup.png").status_code==200
    assert c.post(f"/api/owner/orders/{paid['id']}/collect",headers=WRITE).json()["collected"]
    assert c.post(f"/api/owner/orders/{paid['id']}/collect",headers=WRITE).json()["collected"]
    assert c.post(f"/api/customer/orders/{paid['id']}/reprint",headers=WRITE,json={"request_id":"0123456789abcdef"}).status_code==200
    reprint=c.post(f"/api/customer/orders/{paid['id']}/reprint",headers=WRITE,json={"request_id":"0123456789abcdef"}).json()
    assert reprint["id"] != paid["id"] and reprint["amount"]==200


def test_customer_printer_status_is_agent_reported(shop):
    c,password=shop
    order=upload(c)
    login(c,password)
    printer=device(c)
    assert pay(c,order).status_code==200
    def heartbeat(online,available):
        return c.post("/api/agent/heartbeat",headers=AGENT,json={"printers":[
            {"name":"Shop printer","model":"Shop device","online":online,"available":available}
        ]})
    assert heartbeat(False,False).status_code==200
    session=c.post("/api/customer/session",headers=WRITE,json={}).json()
    visible=next(o for o in session["orders"] if o["id"]==order["id"])
    assert visible["print_stage"]=="Queued" and visible["printer_notice"]=="Printer offline"
    assert c.post("/api/agent/claim",headers=AGENT).json() is None
    assert heartbeat(True,True).status_code==200
    job=c.post("/api/agent/claim",headers=AGENT).json()
    assert job["id"]==order["id"] and job["printer"]==printer
    hdr={**AGENT,"X-Job-Claim":job["claim"]}
    assert c.post(f"/api/agent/jobs/{order['id']}/spool",headers=hdr,json={"phase":"Printing"}).status_code==200
    session=c.post("/api/customer/session",headers=WRITE,json={}).json()
    visible=next(o for o in session["orders"] if o["id"]==order["id"])
    assert visible["print_stage"]=="Printing"
    assert c.post(f"/api/agent/jobs/{order['id']}/finish",headers=hdr,json={"state":"Failed","error":"Paper jam"}).status_code==200
    session=c.post("/api/customer/session",headers=WRITE,json={}).json()
    visible=next(o for o in session["orders"] if o["id"]==order["id"])
    assert visible["state"]=="Failed" and visible["payment"]=="Paid" and visible["print_stage"]=="Failed"


def test_page_ranges_are_validated_and_written_to_paid_document(shop):
    from autoprint.portal import selected_pages
    from fastapi import HTTPException
    assert selected_pages("1-3,2,5",5)==[0,1,2,4]
    for value in ("0","2-1","6","1,,2","one"):
        with pytest.raises(HTTPException):
            selected_pages(value,5)
    c,_=shop
    c.post("/api/customer/session",headers=WRITE,json={})
    pdf=PdfWriter()
    for width in (100,200,300):
        pdf.add_blank_page(width,400)
    output=io.BytesIO()
    pdf.write(output)
    token=stage_document(c,data=output.getvalue()).json()["draft"]
    options={"mode":"BW","copies":2,"duplex":False,"paper":"A4","page_range":"1,3"}
    quote=c.post(f"/api/customer/drafts/{token}/quote",headers=WRITE,json=options).json()
    assert quote["pages"]==2 and quote["amount"]==800
    order=c.post(f"/api/customer/drafts/{token}/order",headers=WRITE,json=options).json()
    assert order["amount"]==800
    from pypdf import PdfReader
    with db() as conn:
        content=conn.execute("SELECT content FROM files WHERE oid=?",(order["id"],)).fetchone()[0]
    pages=PdfReader(io.BytesIO(content)).pages
    assert len(pages)==2 and float(pages[1].mediabox.width)==300


def test_only_page_three_of_sixteen_costs_two_rupees(shop):
    from pypdf import PdfReader
    c,_=shop
    c.post("/api/customer/session",headers=WRITE,json={})
    writer=PdfWriter()
    for number in range(1,17):
        writer.add_blank_page(100+number,400)
    output=io.BytesIO()
    writer.write(output)
    token=stage_document(c,data=output.getvalue()).json()["draft"]
    options={"mode":"BW","copies":1,"duplex":False,"paper":"A4","page_range":"3"}
    quote=c.post(f"/api/customer/drafts/{token}/quote",headers=WRITE,json=options).json()
    assert quote["amount"]==200 and quote["pages"]==1
    assert c.get(f"/api/customer/drafts/{token}/preview?page=3").status_code==200
    assert c.get(f"/api/customer/drafts/{token}/preview?page=17").status_code==422
    order=c.post(f"/api/customer/drafts/{token}/order",headers=WRITE,json=options).json()
    assert order["amount"]==200 and order["pages"]==1
    with db() as conn:
        content=conn.execute("SELECT content FROM files WHERE oid=?",(order["id"],)).fetchone()[0]
    pages=PdfReader(io.BytesIO(content)).pages
    assert len(pages)==1 and float(pages[0].mediabox.width)==103


def test_preview_orientation_color_and_printer_dispatch(shop):
    from PIL import Image
    c,password=shop
    c.post("/api/customer/session",headers=WRITE,json={})
    
    # 1. Multi-page portrait PDF
    writer=PdfWriter()
    writer.add_blank_page(595,842)
    writer.add_blank_page(595,842)
    output=io.BytesIO()
    writer.write(output)
    draft_res=stage_document(c,data=output.getvalue())
    assert draft_res.status_code==200
    draft=draft_res.json()
    assert draft["orientation"]==3
    token=draft["draft"]

    # 2. Browser-formatted query parameters with Portrait (3)
    p_portrait=c.get(f"/api/customer/drafts/{token}/preview?page=1&orientation=3&layout=1&paper=A4&mode=Colour&page_range=")
    assert p_portrait.status_code==200
    assert p_portrait.headers["content-type"]=="image/png"
    im_portrait=Image.open(io.BytesIO(p_portrait.content))
    assert im_portrait.height>im_portrait.width # (636, 900)

    # 3. Landscape (4) with Black & White (BW)
    p_landscape=c.get(f"/api/customer/drafts/{token}/preview?page=1&orientation=4&layout=1&paper=&mode=BW&page_range=")
    assert p_landscape.status_code==200
    im_landscape=Image.open(io.BytesIO(p_landscape.content))
    assert im_landscape.width>im_landscape.height # (900, 636)

    # 4. Multi-page preview
    p_p2=c.get(f"/api/customer/drafts/{token}/preview?page=2&orientation=3&layout=1&paper=A4&mode=BW&page_range=")
    assert p_p2.status_code==200

    # 5. Invalid parameters
    assert c.get(f"/api/customer/drafts/{token}/preview?orientation=99").status_code==422
    assert c.get(f"/api/customer/drafts/{token}/preview?layout=99").status_code==422

    # 6. Order with Landscape orientation and Colour mode
    options={"mode":"Colour","copies":1,"duplex":False,"paper":"A4","page_range":"","orientation":4,"layout":1}
    quote=c.post(f"/api/customer/drafts/{token}/quote",headers=WRITE,json=options).json()
    order=c.post(f"/api/customer/drafts/{token}/order",headers=WRITE,json=options).json()
    assert order["amount"]==quote["amount"]

    # 7. Verify print job received by printer agent preserves orientation and color mode
    login(c,password)
    dev=device(c,"Preview Printer")
    checkout=c.post(f"/api/customer/orders/{order['id']}/checkout",headers=WRITE).json()
    order["gateway"]=checkout["order_id"]
    assert pay(c,order).status_code==200
    claimed=c.post("/api/agent/claim",headers=AGENT,params={"printer":"Preview Printer"}).json()
    assert claimed["id"]==order["id"]
    assert claimed["orientation"]==4
    assert claimed["mode"]=="Colour"


def test_landscape_document_detection_and_image_preview(shop):
    from PIL import Image
    c,_=shop
    c.post("/api/customer/session",headers=WRITE,json={})

    # 1. Landscape PDF document detection
    writer=PdfWriter()
    writer.add_blank_page(842,595)
    buf=io.BytesIO()
    writer.write(buf)
    l_draft=stage_document(c,data=buf.getvalue()).json()
    assert l_draft["orientation"]==4 # Detected landscape

    # 2. Image upload (PNG) and preview
    img=Image.new("RGB",(300,400),color=(0,128,255))
    img_buf=io.BytesIO()
    img.save(img_buf,format="PNG")
    img_draft=c.post("/api/customer/drafts",params={"name":"sample.png"},headers=WRITE,content=img_buf.getvalue()).json()
    assert img_draft["pages"]==1
    img_prev=c.get(f"/api/customer/drafts/{img_draft['draft']}/preview?page=1&orientation=3&layout=1&paper=A4&mode=BW&page_range=")
    assert img_prev.status_code==200
    assert img_prev.headers["content-type"]=="image/png"



def test_consumed_draft_cannot_silently_change_paid_page_selection(shop):
    c,_=shop
    c.post('/api/customer/session',headers=WRITE,json={})
    writer=PdfWriter()
    for _ in range(3):
        writer.add_blank_page(100,200)
    output=io.BytesIO(); writer.write(output)
    token=stage_document(c,data=output.getvalue()).json()['draft']
    options=dict(mode='BW',copies=1,duplex=False,paper='A4',page_range='1')
    url=f'/api/customer/drafts/{token}/order'
    first=c.post(url,headers=WRITE,json=options)
    assert first.status_code==200
    assert c.post(url,headers=WRITE,json=options).json()['id']==first.json()['id']
    assert c.post(url,headers=WRITE,json={**options,'page_range':'3'}).status_code==409


def test_health_endpoints_are_minimal(shop):
    c,_=shop
    assert c.get('/health/live').json()=={'status':'ok'}
    response=c.get('/health/ready')
    assert response.json()=={'status':'ready'}
    assert len(response.headers['x-request-id'])==24


def test_customer_cannot_confirm_physical_output(shop):
    c,_=shop
    assert c.post('/api/owner/orders/AP-test/confirm-output',headers=WRITE,json={'claim':'lease','confirmed':True}).status_code==401


def test_review_and_resolution_require_owner(shop):
    c,password=shop
    assert c.get('/api/owner/print-review').status_code==401
    assert c.post('/api/owner/orders/missing/resolve',headers=WRITE,json={'key':'resolution-key-123','action':'cancel','confirmed':True}).status_code==401
    login(c,password)
    assert c.get('/api/owner/print-review').json()['items']==[]
    assert c.post('/api/owner/orders/missing/resolve',headers=WRITE,json={'key':'resolution-key-123','action':'cancel','confirmed':True}).status_code==404
