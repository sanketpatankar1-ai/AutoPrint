import hashlib
import hmac
import io
import json
import os
import secrets
import socket
import ssl

import certifi
import warnings
from base64 import b64encode
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrlRequest, urlopen
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from pydantic import BaseModel, Field
from PIL import Image, ImageOps
from pypdf import PdfReader
from . import portal
from .security import browser_write, cookie, customer
from .store import TZ, changed, config, db, digest, event, now, rows

router=APIRouter()

def test_payments():
    return os.getenv("RAZORPAY_KEY_ID", "").startswith("rzp_test_")

def public_settings(c):
    s=config(c)
    return {k:v for k,v in s.items() if k not in ("cleanup","retention")}

@router.post("/api/customer/session",dependencies=[Depends(browser_write)])
def session(req:Request,res:Response):
    with db(True) as c:
        token=req.cookies.get("ap_customer","")
        s=c.execute("SELECT id FROM sessions WHERE token=? AND created>? AND seen>?",(digest(token),now()-86400,now()-3600)).fetchone()
        if s:
            sid=s[0]
            c.execute("UPDATE sessions SET seen=? WHERE id=?",(now(),sid))
        else:
            sid="S-"+secrets.token_hex(6).upper()
            token=secrets.token_urlsafe(32)
            c.execute("INSERT INTO sessions VALUES(?,?,?,?)",(sid,digest(token),now(),now()))
            cookie(res,"ap_customer",token,req,86400)
            changed(c)
        return {"session":sid,"settings":public_settings(c),"test_payments":test_payments(),
                "orders":portal.customer_orders(c,sid)}

def parse_document(raw):
    try:
        if raw.startswith(b"%PDF-"):
            pdf=PdfReader(io.BytesIO(raw))
            if pdf.is_encrypted:
                raise ValueError()
            pages=len(pdf.pages)
            if not 1<=pages<=1000:
                raise ValueError()
            return raw,pages
        with warnings.catch_warnings():
            warnings.simplefilter("error",Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw),formats=("PNG","JPEG")) as img:
                if img.width*img.height>25_000_000:
                    raise ValueError()
                out=io.BytesIO()
                ImageOps.exif_transpose(img).convert("RGB").save(out,"PDF",resolution=150)
                return out.getvalue(),1
    except Exception:
        raise HTTPException(422,"Choose a readable PDF (1–1,000 pages), JPEG or PNG image")

async def limited_body(req,limit):
    data=bytearray()
    async for chunk in req.stream():
        if len(data)+len(chunk)>limit:
            raise HTTPException(413,"File exceeds the permitted upload size")
        data.extend(chunk)
    return bytes(data)

def check_open(s,paper):
    if not s["uploads"]:
        raise HTTPException(409,"Uploads are paused")
    h=datetime.now(TZ).strftime("%H:%M")
    a,b=s["opens"],s["closes"]
    if (a<b and not a<=h<b) or (a>b and b<=h<a):
        raise HTTPException(409,"Outside business operating hours")
    if paper not in s["sizes"]:
        raise HTTPException(422,"Choose a supported paper size")

@router.post("/api/customer/orders",dependencies=[Depends(browser_write)])
async def order(req:Request,name:str=Query(min_length=1,max_length=160),mode:Literal["BW","Colour"]="BW",
                copies:int=Query(1,ge=1,le=100),duplex:bool=False,paper:str="A4",sid=Depends(customer)):
    from starlette.concurrency import run_in_threadpool
    with db() as c:
        s=config(c)
    check_open(s,paper)
    raw=await limited_body(req,s["max_mb"]*1024**2)
    portal.validate_name(name,raw)
    content,pages=await run_in_threadpool(parse_document,raw)
    with db(True) as c:
        s=config(c)
        check_open(s,paper)
        if len(raw)>s["max_mb"]*1024**2:
            raise HTTPException(413,"Upload limit changed")
        portal.validate_options(c,portal.Options(mode=mode,copies=copies,duplex=duplex,paper=paper))
        return portal.save_order(c,s,sid,Path(name).name,pages,mode,copies,duplex,paper,content)

def gateway_request(path, payload=None):
    key=os.getenv("RAZORPAY_KEY_ID","")
    secret=os.getenv("RAZORPAY_KEY_SECRET","")
    if not key or not secret or (not test_payments() and not os.getenv("RAZORPAY_WEBHOOK_SECRET")):
        raise HTTPException(503,"The shop has not connected payments yet")
    req=UrlRequest("https://api.razorpay.com/v1/"+path,data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization":"Basic "+b64encode((key+":"+secret).encode()).decode(),"Content-Type":"application/json"})
    context = ssl.create_default_context()
    if not context.get_ca_certs() and not (os.getenv("SSL_CERT_FILE") or os.getenv("SSL_CERT_DIR")):
        context.load_verify_locations(cafile=certifi.where())
    try:
        with urlopen(req, timeout=8, context=context) as r:
            return json.load(r)
    except HTTPError as err:
        if err.code in (401, 403):
            raise HTTPException(502, "Razorpay denied API access. Ask the shop owner to check the configured key pair and account permissions.") from err
        if err.code == 429:
            raise HTTPException(503, "Razorpay is receiving too many requests. Please try again shortly.") from err
        raise HTTPException(502, f"Razorpay returned HTTP {err.code}. Checkout could not continue.") from err
    except URLError as err:
        if isinstance(err.reason, ssl.SSLCertVerificationError):
            raise HTTPException(502, "The shop server could not verify Razorpay’s HTTPS certificate.") from err
        if isinstance(err.reason, socket.gaierror):
            raise HTTPException(502, "The shop server could not resolve api.razorpay.com. Check the server’s internet connection and DNS.") from err
        raise HTTPException(502, "The shop server could not connect to Razorpay. Please try again.") from err
    except (TimeoutError, OSError, ValueError) as err:
        raise HTTPException(502, "Razorpay did not return a usable response. Please try again.") from err

def gateway_order(oid,amount):
    result = gateway_request("orders", dict(amount=amount, currency="INR", receipt=oid))
    if not result.get("id") or result.get("currency") != "INR" or result.get("amount") != amount:
        raise HTTPException(502, "Payment service returned an invalid order")
    return result["id"]

@router.post("/api/customer/orders/{oid}/checkout",dependencies=[Depends(browser_write)])
def checkout(oid:str,sid=Depends(customer)):
    with db(True) as c:
        o=c.execute("""SELECT o.* FROM orders o JOIN files f ON f.oid=o.id WHERE o.id=? AND session=?
                       AND state='Awaiting payment'""",(oid,sid)).fetchone()
        if not o:
            raise HTTPException(409,"This order is unavailable for checkout")
        gid=o["gateway"] or gateway_order(oid,o["amount"])
        c.execute("UPDATE orders SET gateway=? WHERE id=?",(gid,oid))
        return {"key":os.getenv("RAZORPAY_KEY_ID"),"order_id":gid,"amount":o["amount"],"currency":"INR","name":config(c)["name"]}

def verification_alert(c,reason):
    last=c.execute("SELECT at FROM events WHERE kind='verification' AND message=? ORDER BY id DESC LIMIT 1",(reason,)).fetchone()
    if not last or now()-last[0]>=60:
        event(c,"verification",reason)

class CheckoutProof(BaseModel):
    razorpay_payment_id: str = Field(pattern=r"^pay_[a-zA-Z0-9]+$", max_length=100)
    razorpay_order_id: str = Field(min_length=1, max_length=100)
    razorpay_signature: str = Field(pattern=r"^[0-9a-f]{64}$")

@router.post("/api/customer/orders/{oid}/verify", dependencies=[Depends(browser_write)])
def verify_test_payment(oid: str, proof: CheckoutProof | None = None, sid=Depends(customer)):
    if not test_payments() and not proof:
        raise HTTPException(409, "Live verification requires a checkout signature")
    with db() as c:
        order = c.execute("""SELECT o.gateway, v.amount, v.payment, v.state FROM order_view v
            JOIN orders o ON o.id=v.id WHERE o.id=? AND o.session=?""", (oid, sid)).fetchone()
    if not order or not order["gateway"]:
        raise HTTPException(404, "Checkout unavailable for this session")
    gid = order["gateway"]
    if proof:
        secret = os.getenv("RAZORPAY_KEY_SECRET")
        if not secret:
            raise HTTPException(503, "Payment verification unavailable")
        signature = hmac.new(secret.encode(), f"{gid}|{proof.razorpay_payment_id}".encode(), hashlib.sha256).hexdigest()
        if proof.razorpay_order_id != gid or not hmac.compare_digest(signature, proof.razorpay_signature):
            with db(True) as c:
                verification_alert(c, "Payment verification failed · "+oid)
            raise HTTPException(400, "Checkout signature did not match")
    if order["payment"] in ("Paid", "Refunded"):
        return {"payment": order["payment"], "state": order["state"]}

    # A fresh page can recover even if Checkout closed before its callback arrived.
    response = gateway_request("orders/"+quote(gid, safe="")+"/payments")
    items = response.get("items")
    if not isinstance(items, list):
        raise HTTPException(502, "Payment service returned an invalid response")
    with db(True) as c:
        for p in items:
            if proof and p.get("id") != proof.razorpay_payment_id:
                continue
            if p.get("order_id") != gid or p.get("amount") != order["amount"] or p.get("currency") != "INR":
                verification_alert(c, "Payment verification failed · "+oid)
                continue
            status = p.get("status")
            if status not in ("authorized","failed","captured") or not p.get("id"):
                continue
            if status == "captured" and (p.get("captured") is not True or p.get("amount_refunded") != 0
                                         or p.get("refund_status") is not None):
                continue
            ingest(c, "payment."+status, {"payment":{"entity":p}}, now())
        return dict(c.execute("SELECT payment,state FROM order_view WHERE id=?", (oid,)).fetchone())

def apply_refund(c,pid):
    o=c.execute("SELECT oid FROM payments WHERE id=?",(pid,)).fetchone()
    if o and c.execute("SELECT 1 FROM refunds WHERE pid=? AND status='processed'",(pid,)).fetchone():
        c.execute("UPDATE orders SET state='Refund hold' WHERE id=? AND state IN ('Queued','Awaiting payment','Failed')",o)

def ingest(c,kind,payload,at):
    if kind in ("payment.captured","payment.failed","payment.authorized"):
        p=payload["payment"]["entity"]
        o=c.execute("SELECT id,amount,state FROM orders WHERE gateway=?",(p["order_id"],)).fetchone()
        if not o:
            return
        if (p["currency"] != "INR" or p["amount"] != o["amount"] or p["status"] != kind.split(".")[1]
                or (kind == "payment.captured" and p.get("captured") is not True)):
            verification_alert(c,"Payment verification failed · "+o["id"])
            return
        status={"payment.captured":"Paid","payment.failed":"Failed","payment.authorized":"Pending"}[kind]
        old=c.execute("SELECT status,oid FROM payments WHERE id=?",(p["id"],)).fetchone()
        if old and (old["oid"]!=o["id"] or old["status"]=="Paid" or old["status"]==status or status=="Pending"):
            return
        c.execute("""INSERT INTO payments VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                    method=excluded.method,at=excluded.at""",(p["id"],o["id"],p["amount"],status,p.get("method") or "",at))
        if status=="Paid" and o["state"] == "Cancelled":
            event(c,"warning","Payment captured after customer cancellation · "+o["id"])
        if status=="Paid" and o["state"] in ("Awaiting payment","Upload expired"):
            exists=c.execute("SELECT 1 FROM files WHERE oid=?",(o["id"],)).fetchone()
            refunded=c.execute("SELECT 1 FROM refunds WHERE pid=? AND status='processed'",(p["id"],)).fetchone()
            state="Refund hold" if refunded else "Queued" if exists else "Failed"
            c.execute("UPDATE orders SET state=?,error=? WHERE id=?",(state,"" if exists else "Document retention expired",o["id"]))
            event(c,"paid","New paid order · "+o["id"])
            if state=="Failed":
                c.execute("UPDATE orders SET finished=? WHERE id=?",(now(),o["id"]))
                c.execute("INSERT INTO outcomes VALUES(?,?,NULL,'Failed',0,?)",("expired:"+o["id"],o["id"],now()))
                event(c,"error","Printing failed · document expired · "+o["id"])
        apply_refund(c,p["id"])
    elif kind in ("refund.created","refund.processed","refund.failed"):
        r=payload["refund"]["entity"]
        status=r["status"]
        if status not in ("pending","processed","failed") or int(r["amount"])<=0:
            raise ValueError()
        c.execute("""INSERT INTO refunds VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,at=excluded.at
                     WHERE refunds.status!='processed' AND excluded.status!='pending'""",(r["id"],r["payment_id"],r["amount"],status,at))
        apply_refund(c,r["payment_id"])
    changed(c)

@router.post("/api/webhooks/razorpay")
async def webhook(req:Request):
    secret=os.getenv("RAZORPAY_WEBHOOK_SECRET")
    raw=await limited_body(req,512*1024)
    if not secret:
        raise HTTPException(503,"Webhook is unavailable")
    sig=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig.encode(),req.headers.get("x-razorpay-signature","").encode()):
        with db(True) as c:
            verification_alert(c,"Payment verification failed · invalid webhook signature")
        raise HTTPException(401,"Invalid signature")
    try:
        msg=json.loads(raw)
        # Hash the signed body too. An unauthenticated delivery ID cannot suppress another event.
        key=digest(raw.hex())
        with db(True) as c:
            if c.execute("INSERT OR IGNORE INTO receipts VALUES(?)",(key,)).rowcount:
                ingest(c,msg["event"],msg["payload"],int(msg["created_at"]))
    except (KeyError,TypeError,ValueError):
        raise HTTPException(400,"Malformed event")
    return {"received":True}
