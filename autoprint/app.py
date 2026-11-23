import asyncio
import base64
import io
import json
import os
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from dotenv import load_dotenv
from PIL import Image
from . import commerce, metrics, printing, security, portal
from .security import browser_write, owner, staff
from .store import audit, changed, config, db, housekeeping, init, now, rows

WEB=Path(__file__).resolve().parent.parent/"web"
load_dotenv(WEB.parent / ".env", override=False)

@asynccontextmanager
async def lifespan(app):
    init()
    async def maintain():
        while True:
            try:
                await asyncio.to_thread(housekeeping)
            except Exception as error:
                logging.error("maintenance_failed type=%s",type(error).__name__)
            await asyncio.sleep(5)
    task=asyncio.create_task(maintain())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

app=FastAPI(title="AutoPrint",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
for router in (security.router,commerce.router,portal.router,printing.router):
    app.include_router(router)
app.mount("/assets",StaticFiles(directory=WEB/"assets"),name="assets")

@app.middleware("http")
async def headers(req,call_next):
    request_id=secrets.token_hex(12)
    try:
        result=await call_next(req)
    except Exception as error:
        logging.error("request_failed id=%s type=%s",request_id,type(error).__name__)
        result=JSONResponse({"detail":"Service temporarily unavailable. Please retry.","request_id":request_id},status_code=503 if isinstance(error,sqlite3.OperationalError) else 500)
    result.headers["X-Request-ID"]=request_id
    result.headers.update({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer",
        "X-Frame-Options":"DENY","Permissions-Policy":"camera=(), microphone=(), geolocation=()"})
    policy="default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
    result.headers["Content-Security-Policy"]=policy+("script-src 'self' https://checkout.razorpay.com; frame-src https://*.razorpay.com; connect-src 'self' https://*.razorpay.com" if req.url.path=="/print" else "script-src 'self'; connect-src 'self'")
    return result

@app.get("/")
def home():
    return RedirectResponse("/owner")

@app.get("/owner")
def dashboard(req:Request):
    with db() as c:
        user=security.lookup(c,req.cookies.get("ap_staff"))
    return FileResponse(WEB/("dashboard.html" if user else "login.html"))

@app.get("/print")
def customer_page():
    return FileResponse(WEB/"customer.html")

@app.get("/api/owner/overview")
def overview(user=Depends(staff)):
    with db() as c:
        return {"user":user,"business":config(c)["name"],"today":metrics.summary(c,metrics.period("today")),
            "lifetime":dict(c.execute("SELECT COUNT(*) documents,COALESCE(SUM(pages),0) pages FROM outcomes WHERE state='Completed'").fetchone()),"month":metrics.money(c,metrics.period("thismonth"))[0],"sessions":c.execute("SELECT COUNT(*) FROM sessions WHERE seen>? AND created>?",(now()-300,now()-86400)).fetchone()[0],
            "printers":metrics.printers(c),"agents":rows(c,"SELECT * FROM agents"),"latest":metrics.list_data(c,"orders",limit=6)["items"],
            "events":metrics.list_data(c,"events",limit=5)["items"],"queue":c.execute("SELECT COUNT(*) FROM orders WHERE state='Queued'").fetchone()[0],
            "trend":metrics.analytics(c,metrics.period("week"))["days"]}

@app.get("/api/owner/analytics")
def financial(preset:str="week",start:str="",end:str="",user=Depends(staff)):
    with db() as c:
        return metrics.analytics(c,metrics.period(preset,start,end))

@app.get("/api/owner/list/{section}")
def listing(section:str,q:str=Query("",max_length=200),payment:str="",state:str="",preset:str="all",start:str="",end:str="",page:int=Query(1,ge=1,le=10000000),user=Depends(staff)):
    if section=="audit" and user["role"]!="owner":
        raise HTTPException(403,"Owner access required")
    with db() as c:
        return metrics.list_data(c,section,q,payment,state,None if preset=="all" else metrics.period(preset,start,end),page)

@app.get("/api/owner/orders/{oid}")
def order_details(oid:str,user=Depends(staff)):
    with db() as c:
        order=c.execute("SELECT * FROM order_view WHERE id=?",(oid,)).fetchone()
        if not order:
            raise HTTPException(404,"Unknown order")
        return {"claim":c.execute("SELECT claim FROM orders WHERE id=?",(oid,)).fetchone()[0],"spool":rows(c,"SELECT phase FROM spool_status WHERE claim=(SELECT claim FROM orders WHERE id=?)",(oid,)),"order":dict(order),"collected":bool(c.execute("SELECT 1 FROM collections WHERE oid=?",(oid,)).fetchone()),"payments":rows(c,"SELECT * FROM transaction_view WHERE oid=? ORDER BY at DESC",(oid,)),
                "attempts":rows(c,"SELECT printer,state,pages,finished FROM outcomes WHERE oid=? ORDER BY finished DESC",(oid,))}

@app.get("/api/owner/reports/{kind}.{fmt}")
def report(kind:str,fmt:str,preset:str="",start:str="",end:str="",user=Depends(staff)):
    with db(True) as c:
        default={"daily":"today","monthly":"thismonth"}.get(kind,"week")
        result=metrics.export(c,kind,fmt,metrics.period(preset or default,start,end))
        audit(c,user["email"],"Downloaded report",kind+"."+fmt)
        return result

@app.get("/api/owner/settings")
def settings(user=Depends(staff)):
    with db() as c:
        return config(c)

class Settings(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=1,max_length=100)
    logo:str=Field(max_length=350000)
    contact:str=Field(max_length=200)
    address:str=Field(max_length=500)
    bw:int=Field(ge=1,le=100000)
    colour:int=Field(ge=1,le=100000)
    bw_duplex:int=Field(ge=1,le=100000)
    colour_duplex:int=Field(ge=1,le=100000)
    minimum:int=Field(ge=100,le=1000000)
    sizes:list[Literal["A3","A4","A5","Letter","Legal"]]=Field(min_length=1,max_length=5)
    max_mb:int=Field(ge=1,le=32)
    retention:int=Field(ge=1,le=720)
    opens:str=Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    closes:str=Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    cleanup:bool
    uploads:bool
    printing:bool

@app.put("/api/owner/settings",dependencies=[Depends(browser_write)])
def save_settings(settings:Settings,user=Depends(owner)):
    s=settings.model_dump()
    if s["logo"]:
        try:
            header,encoded=s["logo"].split(",",1)
            if header not in ("data:image/png;base64","data:image/jpeg;base64"):
                raise ValueError()
            with Image.open(io.BytesIO(base64.b64decode(encoded,validate=True))) as im:
                if im.format not in ("PNG","JPEG") or im.width*im.height>4_000_000:
                    raise ValueError()
                im.verify()
        except Exception:
            raise HTTPException(422,"Use a PNG or JPEG logo under 250 KB and four megapixels")
    with db(True) as c:
        before=config(c)
        c.execute("UPDATE settings SET value=? WHERE id=1",(json.dumps(s),))
        audit(c,user["email"],"Updated business settings",", ".join(k for k in s if s[k]!=before.get(k)))
    return {"ok":True}

class Control(BaseModel):
    id:str
    action:Literal["pause","resume","enable","disable","BW","Colour"]

@app.post("/api/owner/printers",dependencies=[Depends(browser_write)])
def control(data:Control,user=Depends(staff)):
    with db(True) as c:
        if not c.execute("SELECT 1 FROM printers WHERE id=?",(data.id,)).fetchone():
            raise HTTPException(404,"Unknown printer")
        if data.action in ("BW","Colour"):
            c.execute("INSERT INTO routes VALUES(?,?) ON CONFLICT(mode) DO UPDATE SET printer=excluded.printer",(data.action,data.id))
        elif data.action in ("pause","resume"):
            c.execute("UPDATE printers SET paused=? WHERE id=?",(data.action=="pause",data.id))
        else:
            c.execute("UPDATE printers SET enabled=? WHERE id=?",(data.action=="enable",data.id))
        audit(c,user["email"],"Printer control · "+data.action,data.id)
    return {"ok":True}

@app.post("/api/owner/orders/{oid}/retry",dependencies=[Depends(browser_write)])
def retry(oid:str,user=Depends(staff)):
    with db(True) as c:
        result=c.execute("""UPDATE orders SET state='Queued',claim=NULL,error='',finished=NULL,printer=NULL WHERE id IN
           (SELECT v.id FROM order_view v JOIN files f ON f.oid=v.id WHERE v.id=? AND v.state='Failed' AND v.payment='Paid')""",(oid,))
        if not result.rowcount:
            raise HTTPException(409,"Retry requires a failed, paid job with a retained document")
        audit(c,user["email"],"Retried print job",oid)
    return {"ok":True}

@app.websocket("/api/owner/live")
async def live(ws:WebSocket):
    if not security.origin_ok(ws.headers.get("origin"),ws.headers.get("host")):
        await ws.close(1008)
        return
    token=ws.cookies.get("ap_staff")
    def revision():
        with db() as c:
            if security.lookup(c,token):
                return c.execute("SELECT n FROM revision WHERE id=1").fetchone()[0]
    last=await asyncio.to_thread(revision)
    if last is None:
        await ws.close(1008)
        return
    await ws.accept()
    tick=0
    from starlette.websockets import WebSocketDisconnect
    try:
        await ws.send_json({"revision":last})
        while True:
            try:
                message=await asyncio.wait_for(ws.receive(),timeout=1)
                if message["type"]=="websocket.disconnect":
                    return
            except asyncio.TimeoutError:
                pass
            current=await asyncio.to_thread(revision)
            if current is None:
                await ws.close(1008)
                return
            tick+=1
            if current!=last or tick>=30:
                await ws.send_json({"revision":current})
                last=current
                tick=0
    except (RuntimeError,WebSocketDisconnect):
        return

@app.get("/health/live")
def health_live():
    return {"status":"ok"}

@app.get("/health/ready")
def health_ready():
    with db() as c:
        c.execute("SELECT 1 FROM settings LIMIT 1").fetchone()
    return {"status":"ready"}


class PhysicalConfirmation(BaseModel):
    claim: str = Field(min_length=1,max_length=200)
    confirmed: Literal[True]

@app.post("/api/owner/orders/{oid}/confirm-output",dependencies=[Depends(browser_write)])
def confirm_output(oid:str,body:PhysicalConfirmation,user=Depends(owner)):
    with db(True) as c:
        o=c.execute("SELECT * FROM order_view WHERE id=?",(oid,)).fetchone()
        claim=c.execute("SELECT claim FROM orders WHERE id=?",(oid,)).fetchone()
        if not o or not claim or claim[0]!=body.claim or o["payment"]!="Paid":
            raise HTTPException(409,"Paid job assignment changed")
        if o["state"]=="Completed":
            return {"state":"Completed"}
        phase=c.execute("SELECT phase FROM spool_status WHERE claim=?",(body.claim,)).fetchone()
        if o["state"]!="Printing" or not phase or phase[0] not in ("REQUIRES_REVIEW","Awaiting printer confirmation"):
            raise HTTPException(409,"Job is not awaiting physical confirmation")
        c.execute("UPDATE orders SET state='Completed',finished=? WHERE id=?",(now(),oid))
        c.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)",(body.claim,oid,o["printer"],"Completed",o["pages"]*o["copies"],now()))
        audit(c,user["email"],"Owner confirmed physical output",oid)
    return {"state":"Completed"}


class Resolution(BaseModel):
    key:str=Field(min_length=16,max_length=100)
    claim:str=Field(default="",max_length=200)
    action:Literal["failed","cancel"]
    confirmed:Literal[True]

@app.get("/api/owner/print-review")
def print_review(page:int=Query(1,ge=1),user=Depends(owner)):
    with db() as c:
        items=rows(c,"""SELECT o.id,o.claim,o.printer,o.mode,o.state,o.error,s.phase
            FROM orders o JOIN spool_status s ON s.claim=o.claim
            WHERE o.state='Printing' AND s.phase IN ('REQUIRES_REVIEW','Awaiting printer confirmation')
            ORDER BY o.created,o.id LIMIT 25 OFFSET ?""",((page-1)*25,))
        for item in items:
            item["history"]=rows(c,"SELECT action,actor,at FROM resolutions WHERE oid=? ORDER BY at",(item["id"],))
        return {"items":items,"page":page}

@app.post("/api/owner/orders/{oid}/resolve",dependencies=[Depends(browser_write)])
def resolve_print(oid:str,body:Resolution,user=Depends(owner)):
    with db(True) as c:
        prior=c.execute("SELECT * FROM resolutions WHERE key=?",(body.key,)).fetchone()
        if prior:
            if (prior["oid"],prior["claim"],prior["action"])!=(oid,body.claim,body.action):
                raise HTTPException(409,"Resolution key already used")
            return {"action":body.action}
        o=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if not o: raise HTTPException(404,"Order unavailable")
        if body.action=="failed":
            phase=c.execute("SELECT phase FROM spool_status WHERE claim=?",(body.claim,)).fetchone()
            if o["claim"]!=body.claim or o["state"]!="Printing" or not phase or phase[0] not in ('REQUIRES_REVIEW','Awaiting printer confirmation'):
                raise HTTPException(409,"Job is not awaiting review")
            # A local failure classification never cancels a Windows spooler job.
            c.execute("UPDATE orders SET error=? WHERE id=?",("Owner marked failed; stop/inspect spooler before any retry",oid))
        else:
            if o["state"]!="Queued" or o["claim"]:
                raise HTTPException(409,"Only unclaimed queued jobs can be cancelled")
            c.execute("UPDATE orders SET state='Cancelled',finished=? WHERE id=?",(now(),oid))
        c.execute("INSERT INTO resolutions VALUES(?,?,?,?,?,?)",(body.key,oid,body.claim,body.action,user["email"],now()))
        audit(c,user["email"],"Owner print resolution: "+body.action,oid)
        return {"action":body.action}
