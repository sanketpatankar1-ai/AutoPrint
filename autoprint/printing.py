import json
import hmac
import os
import secrets
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from .store import changed, config, db, event, now

def trusted(authorization:str=Header("")):
    token=os.getenv("PRINT_AGENT_TOKEN","")
    if len(token)<32 or not hmac.compare_digest(authorization.encode(),("Bearer "+token).encode()):
        raise HTTPException(401,"Agent authentication required")
    return os.getenv("PRINT_AGENT_ID","shop-agent")

router=APIRouter(prefix="/api/agent")
class Device(BaseModel):
    name:str=Field(min_length=1,max_length=160)
    model:str=Field(default="",max_length=240)
    online:bool
    available:bool
    low_paper:bool=False
    error:str=Field(default="",max_length=400)
    capabilities:dict=Field(default_factory=dict)

class Heartbeat(BaseModel):
    printers:list[Device]=Field(max_length=100)

@router.post("/heartbeat")
def heartbeat(data:Heartbeat,agent=Depends(trusted)):
    with db(True) as c:
        old=c.execute("SELECT online FROM agents WHERE id=?",(agent,)).fetchone()
        c.execute("INSERT INTO agents VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET seen=excluded.seen,online=1",(agent,now()))
        if not old or not old[0]:
            event(c,"info","Local print agent connected · "+agent)
        existing={r["id"]:r for r in c.execute("SELECT * FROM printers WHERE agent=?",(agent,))}
        for p in data.printers:
            pid=agent+"/"+p.name
            was=existing.pop(pid,None)
            vals=(p.model,int(p.online),int(p.available and p.online),int(p.low_paper),p.error)
            capabilities=json.dumps(p.capabilities)
            if was:
                c.execute("INSERT OR REPLACE INTO printer_capabilities VALUES(?,?)",(pid,capabilities))
            if was and tuple(was[k] for k in ("model","online","available","low_paper","error"))==vals:
                continue
            c.execute("""INSERT INTO printers(id,agent,name,model,online,available,low_paper,error) VALUES(?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET model=excluded.model,online=excluded.online,available=excluded.available,
              low_paper=excluded.low_paper,error=excluded.error""",(pid,agent,p.name,*vals))
            c.execute("INSERT OR REPLACE INTO printer_capabilities VALUES(?,?)",(pid,capabilities))
            if was and was["online"] and not p.online:
                event(c,"warning","Printer disconnected · "+p.name)
            if p.low_paper and (not was or not was["low_paper"]):
                event(c,"warning","Low printer paper · "+p.name)
            changed(c)
        for pid,p in existing.items():
            if p["online"]:
                c.execute("UPDATE printers SET online=0,available=0 WHERE id=?",(pid,))
                event(c,"warning","Printer disconnected · "+p["name"])
    return {"ok":True}

def claim_result(o):
    result={k:o[k] for k in ("id","printer","claim","mode","copies","paper","duplex")}
    with db() as c:
        options=c.execute("SELECT orientation,layout FROM print_options WHERE oid=?",(o["id"],)).fetchone()
    if options:
        result.update(dict(options))
    return result

@router.get("/active")
def active(agent=Depends(trusted)):
    with db() as c:
        return [claim_result(o) for o in c.execute(
            "SELECT o.* FROM orders o JOIN printers p ON o.printer=p.id WHERE o.state='Printing' AND p.agent=?", (agent,)
        )]

@router.post("/claim")
def claim(printer:str="",agent=Depends(trusted)):
    with db(True) as c:
        # Re-deliver the same lease after a lost response; the agent journals submissions.
        old=c.execute("""SELECT o.* FROM orders o JOIN printers p ON o.printer=p.id
                        WHERE o.state='Printing' AND p.agent=? AND (?='' OR p.name=?) ORDER BY created LIMIT 1""",(agent,printer,printer)).fetchone()
        if old:
            return claim_result(old)
        if not config(c)["printing"]:
            return None
        o=c.execute("""SELECT o.id,r.printer FROM order_view o JOIN routes r ON r.mode=o.mode JOIN printers p ON p.id=r.printer
            JOIN agents a ON a.id=p.agent JOIN files f ON f.oid=o.id WHERE o.state='Queued' AND o.payment='Paid'
            AND p.enabled AND NOT p.paused AND p.online AND p.available AND a.seen>? AND p.agent=? AND (?='' OR p.name=?) ORDER BY o.created,o.id LIMIT 1""",(now()-45,agent,printer,printer)).fetchone()
        if not o:
            return None
        token=secrets.token_urlsafe(24)
        c.execute("UPDATE orders SET claim=?,printer=?,state='Printing',error='' WHERE id=?",(token,o["printer"],o["id"]))
        event(c,"printing","Printing started · "+o["id"])
        return claim_result(c.execute("SELECT * FROM orders WHERE id=?",(o["id"],)).fetchone())

def assigned(c,oid,agent,lease):
    o=c.execute("""SELECT o.* FROM orders o JOIN printers p ON p.id=o.printer WHERE o.id=? AND p.agent=? AND o.claim=?""",(oid,agent,lease)).fetchone()
    if not o:
        raise HTTPException(404,"Job assignment unavailable")
    return o

@router.get("/jobs/{oid}/file")
def download(oid:str,x_job_claim:str=Header(),agent=Depends(trusted)):
    with db() as c:
        o=assigned(c,oid,agent,x_job_claim)
        r=c.execute("SELECT f.content FROM files f JOIN order_view v ON v.id=f.oid WHERE f.oid=? AND v.payment='Paid'",(oid,)).fetchone()
        if o["state"]!="Printing" or not r:
            raise HTTPException(409,"Document unavailable")
        return Response(r[0],media_type="application/pdf")

class SpoolReport(BaseModel):
    phase: Literal["Printing","Queued","Printer stopped","Awaiting printer confirmation","SPOOLED","REQUIRES_REVIEW"]


@router.post("/jobs/{oid}/spool")
def report_spool(oid: str, data: SpoolReport, x_job_claim: str=Header(), agent=Depends(trusted)):
    with db(True) as c:
        o = assigned(c,oid,agent,x_job_claim)
        if o["state"] != "Printing":
            raise HTTPException(409,"Job already closed")
        updated=c.execute("""INSERT INTO spool_status VALUES(?,?) ON CONFLICT(claim)
            DO UPDATE SET phase=excluded.phase WHERE phase<>excluded.phase""",(x_job_claim,data.phase)).rowcount
        if updated:
            event(c,"printing","Printer reports "+data.phase.lower()+" · "+oid)
    return {"ok":True}


class Finish(BaseModel):
    state:Literal["Completed","Failed","Cancelled"]
    error:str=Field(default="",max_length=300)

@router.post("/jobs/{oid}/finish")
def finish(oid:str,result:Finish,x_job_claim:str=Header(),agent=Depends(trusted)):
    with db(True) as c:
        o=assigned(c,oid,agent,x_job_claim)
        if o["state"]==result.state:
            return {"ok":True}
        if o["state"]!="Printing":
            raise HTTPException(409,"Job already closed")
        c.execute("UPDATE orders SET state=?,error=?,finished=? WHERE id=?",(result.state,result.error,now(),oid))
        c.execute("INSERT INTO outcomes VALUES(?,?,?,?,?,?)",(x_job_claim,oid,o["printer"],result.state,o["pages"]*o["copies"],now()))
        event(c,"success" if result.state=="Completed" else "error","Printing "+result.state.lower()+" · "+oid)
    return {"ok":True}
