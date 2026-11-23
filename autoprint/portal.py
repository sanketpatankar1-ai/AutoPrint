"""Private customer drafts, receipts, and pickup operations."""
import json
import io
import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .security import browser_write, customer, staff
from .store import TZ, audit, changed, config, db, event, now, rows

router = APIRouter()
write = [Depends(browser_write)]
render_slots = threading.BoundedSemaphore(2)


def customer_orders(c, sid):
    return rows(c, """SELECT v.id,v.document,v.pages,v.mode,v.copies,v.duplex,v.paper,v.amount,
        v.created,v.state,v.payment,o.gateway IS NOT NULL AS checkout_started,
        EXISTS(SELECT 1 FROM payments WHERE oid=o.id AND status='Pending') AS capture_pending,
        EXISTS(SELECT 1 FROM files WHERE oid=o.id) AS retained, k.at AS collected,
        CASE WHEN v.state='Printing' THEN COALESCE(sp.phase,'Queued') ELSE v.state END AS print_stage,
        CASE WHEN v.state IN ('Printing','Queued') THEN CASE
          WHEN p.id IS NULL THEN 'Waiting for printer assignment'
          WHEN NOT p.online OR NOT a.online OR a.seen<=? THEN 'Printer offline'
          WHEN p.paused OR NOT p.enabled OR NOT ? THEN 'Printing paused'
          WHEN v.state='Queued' AND NOT p.available THEN 'Printer busy' END END AS printer_notice
        FROM order_view v JOIN orders o ON o.id=v.id LEFT JOIN collections k ON k.oid=o.id
        LEFT JOIN spool_status sp ON sp.claim=o.claim LEFT JOIN routes r ON r.mode=o.mode
        LEFT JOIN printers p ON p.id=COALESCE(o.printer,r.printer) LEFT JOIN agents a ON a.id=p.agent
        WHERE o.session=? ORDER BY o.created DESC,o.rowid DESC""", (now()-45, config(c)["printing"], sid))


def validate_name(name, raw):
    ext = Path(name).suffix.lower()
    matches = {".pdf": b"%PDF-", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".png": b"\x89PNG\r\n\x1a\n"}
    if ext not in matches or not raw.startswith(matches[ext]):
        raise HTTPException(422, "Choose a PDF, JPG, JPEG or PNG with a matching file type")


def save_order(c, s, sid, name, pages, mode, copies, duplex, paper, content):
    rate = s[("bw" if mode == "BW" else "colour") + ("_duplex" if duplex else "")]
    amount = max(s["minimum"], rate * pages * copies)
    oid = "AP-" + secrets.token_hex(5).upper()
    c.execute("""INSERT INTO orders(id,session,document,pages,mode,copies,duplex,paper,amount,created)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", (oid,sid,name,pages,mode,copies,duplex,paper,amount,now()))
    c.execute("INSERT INTO files VALUES(?,?)", (oid,content))
    changed(c)
    return {"id":oid,"pages":pages,"amount":amount}


@router.post("/api/customer/drafts", dependencies=write)
async def prepare(req: Request, name: str = Query(min_length=1,max_length=160), sid=Depends(customer)):
    from .commerce import check_open, limited_body, parse_document
    with db() as c:
        s = config(c)
    check_open(s,s["sizes"][0])
    raw = await limited_body(req,s["max_mb"]*1024**2)
    validate_name(name, raw)
    content, pages = await run_in_threadpool(parse_document,raw)
    token = secrets.token_urlsafe(20)
    with db(True) as c:
        # Bound private temporary storage across abandoned selections and tabs.
        c.execute("""DELETE FROM drafts WHERE session=? AND oid IS NULL AND id NOT IN
            (SELECT id FROM drafts WHERE session=? AND oid IS NULL ORDER BY rowid DESC LIMIT 9)""",(sid,sid))
        c.execute("INSERT INTO drafts VALUES(?,?,?,?,?,?,?,NULL)",
                  (token,sid,Path(name).name,pages,len(raw),content,now()+1800))
    orientation = 3
    try:
        from pypdf import PdfReader
        p = PdfReader(io.BytesIO(content)).pages[0]
        box = p.mediabox
        rot = p.get("/Rotate", 0)
        pw, ph = float(box.width), float(box.height)
        if rot in (90, 270):
            pw, ph = ph, pw
        if pw > ph:
            orientation = 4
    except Exception:
        pass
    return {"draft":token,"pages":pages,"orientation":orientation}


class Options(BaseModel):
    mode: Literal["BW","Colour"]
    copies: int = Field(ge=1,le=100)
    duplex: bool
    paper: str
    page_range: str = Field(default="",max_length=500)
    orientation: Literal[0,3,4] = 0
    layout: Literal[1,2,4,6,9,16] = 1


@router.get("/api/customer/printers")
def customer_printers(sid=Depends(customer)):
    with db() as c:
        result=[]
        for row in c.execute("""SELECT r.mode,p.online,p.available,p.enabled,p.paused,a.seen,pc.data
            FROM routes r JOIN printers p ON p.id=r.printer JOIN agents a ON a.id=p.agent
            LEFT JOIN printer_capabilities pc ON pc.id=p.id"""):
            result.append({"mode":row["mode"],"online":bool(row["online"] and row["seen"]>now()-45),
                           "available":bool(config(c)["printing"] and row["online"] and row["available"] and row["enabled"] and not row["paused"] and row["seen"]>now()-45),
                           "capabilities":json.loads(row["data"]) if row["data"] else {}})
        return result


def validate_options(c, options):
    row=c.execute("""SELECT pc.data FROM routes r JOIN printer_capabilities pc ON pc.id=r.printer
        WHERE r.mode=?""",(options.mode,)).fetchone()
    caps=json.loads(row[0]) if row else {}
    if caps.get("orientations") and options.orientation and options.orientation not in caps["orientations"]:
        raise HTTPException(422,"Selected printer does not report support for this orientation")
    if caps.get("layouts") and options.layout not in caps.get("layouts",[1]):
        raise HTTPException(422,"Selected printer does not report support for this layout")
    if caps:
        if options.paper not in caps.get("papers",[]):
            raise HTTPException(422,"Selected printer does not report support for this paper size")
        if options.duplex and not caps.get("duplex"):
            raise HTTPException(422,"Selected printer does not support double-sided printing")
        if options.copies > caps.get("max_copies",1):
            raise HTTPException(422,"Copies exceed the printer's supported limit")


def selected_pages(expression, total):
    if not expression.strip():
        return list(range(total))
    selected = set()
    for part in expression.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise HTTPException(422,"Use page numbers or ranges, for example 1-3, 5")
        first = int(match[1])
        last = int(match[2] or first)
        if not 1 <= first <= last <= total:
            raise HTTPException(422,f"Choose pages between 1 and {total}")
        selected.update(range(first-1,last))
    return sorted(selected)


@router.post("/api/customer/drafts/{token}/quote",dependencies=write)
def quote_draft(token: str, options: Options, sid=Depends(customer)):
    from .commerce import check_open
    with db() as c:
        d=c.execute("SELECT pages FROM drafts WHERE id=? AND session=? AND expires>?",(token,sid,now())).fetchone()
        if not d:
            raise HTTPException(410,"Document selection expired. Choose the file again.")
        s=config(c)
        check_open(s,options.paper)
        validate_options(c,options)
        pages=len(selected_pages(options.page_range,d["pages"]))
        rate=s[("bw" if options.mode=="BW" else "colour")+("_duplex" if options.duplex else "")]
        subtotal=pages*options.copies*rate
        return {"pages":pages,"copies":options.copies,"rate":rate,"subtotal":subtotal,
                "minimum_adjustment":max(0,s["minimum"]-subtotal),"amount":max(s["minimum"],subtotal)}


@router.post("/api/customer/drafts/{token}/order", dependencies=write)
def submit_draft(token: str, options: Options, sid=Depends(customer)):
    from .commerce import check_open
    with db(True) as c:
        d = c.execute("SELECT * FROM drafts WHERE id=? AND session=? AND expires>?",(token,sid,now())).fetchone()
        if not d:
            raise HTTPException(410,"Document selection expired. Choose the file again.")
        if d["oid"]:
            saved=c.execute("SELECT options FROM draft_options WHERE token=?",(token,)).fetchone()
            if not saved or saved[0] != options.model_dump_json():
                raise HTTPException(409,"This document already has an order with different settings. Choose the file again to create a new order.")
            return dict(c.execute("SELECT id,pages,amount FROM orders WHERE id=?",(d["oid"],)).fetchone())
        s = config(c)
        check_open(s,options.paper)
        validate_options(c,options)
        if d["original_size"] > s["max_mb"]*1024**2:
            raise HTTPException(413,"File exceeds the current upload limit")
        indices=selected_pages(options.page_range,d["pages"])
        content=d["content"]
        if options.page_range.strip():
            from pypdf import PdfReader, PdfWriter
            reader=PdfReader(io.BytesIO(content))
            writer=PdfWriter()
            for index in indices:
                writer.add_page(reader.pages[index])
            output=io.BytesIO()
            writer.write(output)
            content=output.getvalue()
        order = save_order(c,s,sid,d["name"],len(indices),options.mode,options.copies,options.duplex,options.paper,content)
        c.execute("INSERT INTO print_options VALUES(?,?,?)",(order["id"],options.orientation,options.layout))
        c.execute("INSERT INTO draft_options VALUES(?,?)",(token,options.model_dump_json()))
        c.execute("UPDATE drafts SET oid=?,content=NULL WHERE id=?",(order["id"],token))
        return order


@router.get("/api/customer/drafts/{token}/preview")
def preview(token: str, page: int=Query(1,ge=1,le=1000), orientation: int=Query(0), layout: int=Query(1), paper: str=Query("A4"), mode: str=Query("Colour"), page_range: str=Query("",max_length=500), sid=Depends(customer)):
    with db() as c:
        d = c.execute("SELECT content,pages FROM drafts WHERE id=? AND session=? AND expires>? AND content IS NOT NULL",
                      (token,sid,now())).fetchone()
    if not d:
        raise HTTPException(404,"Preview expired")
    if orientation not in (0, 3, 4):
        raise HTTPException(422, "Invalid orientation")
    if layout not in (1, 2, 4, 6, 9, 16):
        raise HTTPException(422, "Invalid layout")
    if not paper:
        paper = "A4"
    elif paper not in ("A3", "A4", "A5", "Letter", "Legal"):
        raise HTTPException(422, "Invalid paper size")
    mode = "BW" if mode.upper() == "BW" else "Colour"
    indices=selected_pages(page_range,d["pages"])
    if page > d["pages"]:
        raise HTTPException(422,"Preview page exceeds the document page count")
    if not render_slots.acquire(timeout=4):
        raise HTTPException(429,"Preview busy. Try again.")
    try:
        result = subprocess.run([sys.executable,str(Path(__file__).with_name("thumbnail.py")),str(page-1),json.dumps({"indices":indices,"orientation":orientation,"layout":layout,"paper":paper,"mode":mode})],
                                input=d[0],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=6)
        if result.returncode or not result.stdout.startswith(b"\x89PNG"):
            raise HTTPException(422,"Preview unavailable for this document")
        return Response(result.stdout,media_type="image/png")
    except subprocess.TimeoutExpired:
        raise HTTPException(422,"Document is too complex to preview")
    finally:
        render_slots.release()


@router.get("/api/customer/orders/{oid}/receipt")
def receipt(oid: str,sid=Depends(customer)):
    with db() as c:
        r = c.execute("""SELECT o.*,p.id AS transaction_id,p.at FROM order_view o JOIN payments p
            ON p.oid=o.id AND p.status='Paid' AND p.amount=o.amount WHERE o.id=? AND o.session=?
            ORDER BY p.at LIMIT 1""",(oid,sid)).fetchone()
        if not r:
            raise HTTPException(404,"Receipt unavailable")
        business = config(c)["name"]
    date = datetime.fromtimestamp(r["at"],TZ).strftime("%d %b %Y, %H:%M IST")
    body = "\n".join([business,"Payment receipt",oid,date,r["document"],
        f"{r['pages']} pages × {r['copies']} copies · {r['mode']} · {r['paper']}",
        f"Captured amount: INR {r['amount']/100:.2f}",f"Payment status: {r['payment']}",r["transaction_id"]])
    return Response(body,media_type="text/plain",headers={"Content-Disposition":f'attachment; filename="{oid}-receipt.txt"'})


@router.get("/api/customer/orders/{oid}/pickup.png")
def pickup_qr(oid: str,req: Request,sid=Depends(customer)):
    import qrcode
    with db() as c:
        ok = c.execute("SELECT 1 FROM order_view WHERE id=? AND session=? AND payment='Paid' AND state='Completed'",(oid,sid)).fetchone()
    if not ok:
        raise HTTPException(404,"Pickup pass unavailable")
    # This reference only opens the authenticated owner's order search.
    out = io.BytesIO()
    origin = os.getenv("PUBLIC_ORIGIN",str(req.base_url)).rstrip("/")
    qrcode.make(origin+"/owner#orders/"+oid,box_size=4).save(out,format="PNG")
    return Response(out.getvalue(),media_type="image/png")


@router.post("/api/owner/orders/{oid}/collect", dependencies=write)
def collect(oid: str,user=Depends(staff)):
    with db(True) as c:
        if not c.execute("SELECT 1 FROM order_view WHERE id=? AND payment='Paid' AND state='Completed'",(oid,)).fetchone():
            raise HTTPException(409,"Only a completed, paid print can be collected")
        if c.execute("INSERT OR IGNORE INTO collections VALUES(?,?,?)",(oid,now(),user["id"])).rowcount:
            audit(c,user["email"],"Confirmed customer collection",oid)
    return {"collected":True}


class ReprintRequest(BaseModel):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,80}$")


@router.post("/api/customer/orders/{oid}/reprint",dependencies=write)
def reprint(oid: str,body: ReprintRequest,sid=Depends(customer)):
    from .commerce import check_open
    with db(True) as c:
        prior = c.execute("SELECT source,oid FROM reprints WHERE session=? AND request_id=?",(sid,body.request_id)).fetchone()
        if prior:
            if prior["source"] != oid:
                raise HTTPException(409,"Reprint request belongs to another order")
            return dict(c.execute("SELECT id,amount,pages FROM orders WHERE id=?",(prior["oid"],)).fetchone())
        o = c.execute("""SELECT v.*,f.content FROM order_view v JOIN files f ON f.oid=v.id
            WHERE id=? AND session=? AND payment='Paid' AND state='Completed'""",(oid,sid)).fetchone()
        if not o:
            raise HTTPException(409,"Original file unavailable for reprint")
        s = config(c)
        check_open(s,o["paper"])
        order = save_order(c,s,sid,o["document"],o["pages"],o["mode"],o["copies"],o["duplex"],o["paper"],o["content"])
        saved=c.execute("SELECT orientation,layout FROM print_options WHERE oid=?",(oid,)).fetchone()
        options=Options(mode=o["mode"],copies=o["copies"],duplex=bool(o["duplex"]),paper=o["paper"],
                        orientation=saved["orientation"] if saved else 0,layout=saved["layout"] if saved else 1)
        validate_options(c,options)
        c.execute("INSERT INTO print_options VALUES(?,?,?)",(order["id"],options.orientation,options.layout))
        c.execute("INSERT INTO reprints VALUES(?,?,?,?)",(sid,body.request_id,oid,order["id"]))
        return order


@router.post("/api/customer/orders/{oid}/cancel",dependencies=write)
def cancel(oid: str,sid=Depends(customer)):
    with db(True) as c:
        result = c.execute("""UPDATE orders SET state='Cancelled',finished=? WHERE id=? AND session=?
            AND state='Awaiting payment' AND NOT EXISTS(SELECT 1 FROM payments WHERE oid=? AND status IN ('Paid','Pending'))""",
                           (now(),oid,sid,oid))
        if not result.rowcount:
            raise HTTPException(409,"Order can no longer be cancelled")
        c.execute("DELETE FROM files WHERE oid=?",(oid,))
        event(c,"info","Customer cancelled order · "+oid)
    return {"state":"Cancelled"}
