"""Run one instance on the shop's CUPS host. Install pycups on this machine."""
import json
import logging
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlencode

BASE=os.getenv("AUTOPRINT_URL","http://127.0.0.1:8000").rstrip("/")
TOKEN=os.getenv("PRINT_AGENT_TOKEN","")
# An agent on the same computer can reuse the shop's local pairing token.
local_env = Path(__file__).resolve().with_name(".env")
if not TOKEN and local_env.is_file():
    from dotenv import dotenv_values
    TOKEN = dotenv_values(local_env).get("PRINT_AGENT_TOKEN") or ""

def api(path,data=None,claim=None,binary=False):
    headers={"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json"}
    if claim:
        headers["X-Job-Claim"]=claim
    r=Request(BASE+"/api/agent/"+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
    with urlopen(r,timeout=15) as response:
        return response.read() if binary else json.load(response)

def devices(conn):
    result=[]
    for name,p in conn.getPrinters().items():
        details=conn.getPrinterAttributes(name,requested_attributes=["printer-state-reasons","printer-make-and-model","printer-state","printer-is-accepting-jobs","media-supported","sides-supported","number-up-supported","orientation-requested-supported","copies-supported"])
        p={**p,**details}
        reasons=p.get("printer-state-reasons",[])
        if isinstance(reasons,str):
            reasons=[reasons]
        online=not any("offline" in x or "connecting-to-device" in x or "timed-out" in x for x in reasons)
        media=p.get("media-supported",[])
        papers={"A3":"iso_a3_","A4":"iso_a4_","A5":"iso_a5_","Letter":"na_letter_","Legal":"na_legal_"}
        copies=p.get("copies-supported",[1,1])
        capabilities={"papers":[name for name,prefix in papers.items() if any(str(value).startswith(prefix) or str(value)==name for value in media)],
                      "duplex":"two-sided-long-edge" in p.get("sides-supported",[]),
                      "layouts":p.get("number-up-supported",[1]),
                      "orientations":p.get("orientation-requested-supported",[]),
                      "max_copies":max(copies) if isinstance(copies,(list,tuple)) else int(copies)}
        result.append(dict(name=name,model=p.get("printer-make-and-model",""),online=online,
            available=online and p.get("printer-state")==3 and bool(p.get("printer-is-accepting-jobs",True)),
            capabilities=capabilities,low_paper=any("media-low" in x or "media-empty" in x for x in reasons),
            error=", ".join(x for x in reasons if x!="none")[:400]))
    return result

def process(conn,journal,job):
    lease=job["claim"]
    found=journal.execute("SELECT spool FROM jobs WHERE lease=?",(lease,)).fetchone()
    oid=job["id"]
    if not found:
        try:
            content=api("jobs/"+oid+"/file",claim=lease,binary=True)
        except HTTPError as err:
            if err.code!=409:
                raise
            api("jobs/"+oid+"/finish",{"state":"Failed","error":"Document release blocked. Check payment and retention status."},lease)
            return
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(content)
            f.flush()
            # Commit intent before handing bytes to CUPS. A crash cannot silently reprint a job.
            journal.execute("INSERT INTO jobs VALUES(?,NULL)",(lease,))
            journal.commit()
            opts={"copies":str(job["copies"]),"media":job["paper"],"sides":"two-sided-long-edge" if job["duplex"] else "one-sided",
                  "print-color-mode":"monochrome" if job["mode"]=="BW" else "color"}
            if job.get("orientation"):
                opts["orientation-requested"]=str(job["orientation"])
            if job.get("layout"):
                opts["number-up"]=str(job["layout"])
            spool=conn.printFile(job["printer"].split("/",1)[1],f.name,oid+" "+lease,opts)
            journal.execute("UPDATE jobs SET spool=? WHERE lease=?",(spool,lease))
            journal.commit()
    else:
        spool=found[0]
    if spool is None:
        # Recover an accepted submission whose response was lost using its unique job title.
        matches=[i for i,j in conn.getJobs(which_jobs="all",requested_attributes=["job-name"]).items() if j.get("job-name")==oid+" "+lease]
        if matches:
            spool=matches[0]
            journal.execute("UPDATE jobs SET spool=? WHERE lease=?",(spool,lease))
            journal.commit()
        else:
            api("jobs/"+oid+"/finish",{"state":"Failed","error":"Submission uncertain. Check the printer before retrying."},lease)
            return
    import cups
    try:
        info=conn.getJobAttributes(spool)
    except cups.IPPError as err:
        if err.args[0]!=getattr(cups, 'IPP_NOT_FOUND', 0x0406):
            raise
        api("jobs/"+oid+"/finish",{"state":"Failed","error":"Spooler history unavailable. Check output before retrying."},lease)
        return
    state=info.get("job-state")
    reasons=info.get("job-state-reasons",[])
    if isinstance(reasons,str):
        reasons=[reasons]
    if state==9 and "queued-in-device" in reasons:
        phase="Awaiting printer confirmation"
    elif state in (7,8,9):
        status = {7:"Cancelled",8:"Failed",9:"Completed"}[state]
        if "job-completed-with-errors" in reasons:
            status="Failed"
        api("jobs/"+oid+"/finish",{"state":status,"error":"" if status=="Completed" else "Spooler reported "+status.lower()},lease)
        return
    else:
        phase={3:"Queued",4:"Queued",5:"Printing",6:"Printer stopped"}.get(state)
    if phase:
        # Report transitions once per lease; retry if the server did not acknowledge.
        journal.execute("CREATE TABLE IF NOT EXISTS reported(lease TEXT PRIMARY KEY,phase TEXT)")
        previous=journal.execute("SELECT phase FROM reported WHERE lease=?",(lease,)).fetchone()
        if not previous or previous[0]!=phase:
            api("jobs/"+oid+"/spool",{"phase":phase},lease)
            journal.execute("INSERT OR REPLACE INTO reported VALUES(?,?)",(lease,phase))
            journal.commit()

def main():
    import cups
    import fcntl
    if len(TOKEN)<32 or (urlsplit(BASE).scheme!="https" and urlsplit(BASE).hostname not in ("localhost","127.0.0.1","::1")):
        raise SystemExit("Configure a strong PRINT_AGENT_TOKEN and an HTTPS server URL (HTTP allowed on loopback).")
    directory=Path(os.getenv("AGENT_DATA","./agent-data"))
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=open(directory/"agent.lock","a")
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    journal=sqlite3.connect(directory/"jobs.db")
    journal.execute("CREATE TABLE IF NOT EXISTS jobs(lease TEXT PRIMARY KEY,spool INTEGER)")
    while True:
        try:
            conn=cups.Connection()
            found=devices(conn)
            api("heartbeat",{"printers":found})
            jobs=api("active")
            occupied={j["printer"].split("/",1)[1] for j in jobs}
            for printer in found:
                if printer["available"] and printer["name"] not in occupied:
                    job=api("claim?"+urlencode({"printer":printer["name"]}),{})
                    if job:
                        jobs.append(job)
            for job in jobs:
                try:
                    process(conn,journal,job)
                except Exception as err:
                    logging.warning("Job %s will be checked again (%s).",job["id"],type(err).__name__)
        except Exception as error:
            logging.warning("Agent cycle failed (%s). Retrying.",type(error).__name__)
        time.sleep(3)

if __name__=="__main__":
    main()
