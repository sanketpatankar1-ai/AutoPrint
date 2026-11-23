import hmac
import os
import secrets
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from .store import audit, db, digest, now, secret_hash

router=APIRouter()
DUMMY=secret_hash(secrets.token_hex(24))

def origin_ok(origin,host):
    expected=os.getenv("PUBLIC_ORIGIN")
    if expected:
        return origin==expected.rstrip("/")
    parsed=urlsplit(origin or "")
    return parsed.scheme in ("http","https") and parsed.netloc==host

def browser_write(req: Request):
    if req.headers.get("x-autoprint")!="1" or (req.headers.get("origin") and not origin_ok(req.headers["origin"],req.headers.get("host"))):
        raise HTTPException(403,"Invalid request origin")

def is_local_host(hostname):
    if not hostname or hostname in ("127.0.0.1","localhost","testserver","::1"):
        return True
    try:
        import ipaddress
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback
    except Exception:
        return False

def cookie(resp,name,token,req,seconds):
    local=is_local_host(req.url.hostname)
    secure=req.url.scheme=="https" or (not local and os.getenv("PUBLIC_ORIGIN","").startswith("https://"))
    resp.set_cookie(name,token,max_age=seconds,httponly=True,secure=secure,samesite="strict",path="/")

def lookup(c,token):
    return c.execute("""SELECT u.id,u.email,u.role FROM logins l JOIN users u ON u.id=l.uid
                       WHERE token=? AND expires>? AND seen>? AND role IN ('owner','admin')""",
                     (digest(token or ""),now(),now()-3600)).fetchone()

def staff(req:Request):
    with db(True) as c:
        token=req.cookies.get("ap_staff")
        user=lookup(c,token)
        if not user:
            raise HTTPException(401,"Sign in to your workspace")
        c.execute("UPDATE logins SET seen=? WHERE token=? AND seen<?",(now(),digest(token),now()-60))
        return dict(user)

def owner(user=Depends(staff)):
    if user["role"]!="owner":
        raise HTTPException(403,"Owner access required")
    return user

class Login(BaseModel):
    email: str=Field(max_length=254)
    password: str=Field(max_length=512)

@router.post("/api/login",dependencies=[Depends(browser_write)])
def login(data:Login,req:Request,resp:Response):
    email=data.email.strip().lower()
    ip=req.client.host if req.client else "local"
    with db(True) as c:
        for k,limit in (("ip:"+ip,24),("account:"+email,8)):
            if c.execute("SELECT COUNT(*) FROM attempts WHERE k=? AND at>?",(k,now()-900)).fetchone()[0]>=limit:
                raise HTTPException(429,"Too many attempts. Try again in 15 minutes.")
        c.executemany("INSERT INTO attempts VALUES(?,?)",[(k,now()) for k in ("ip:"+ip,"account:"+email)])
        u=c.execute("SELECT * FROM users WHERE email=?",(email,)).fetchone()
    saved=u["hash"] if u else DUMMY
    matches=hmac.compare_digest(saved,secret_hash(data.password,saved.split(":")[0]))
    with db(True) as c:
        if u and matches and c.execute("SELECT 1 FROM users WHERE id=? AND hash=?",(u["id"],saved)).fetchone():
            token=secrets.token_urlsafe(32)
            c.execute("INSERT INTO logins VALUES(?,?,?,?)",(digest(token),u["id"],now()+28800,now()))
            c.execute("DELETE FROM attempts WHERE k=?",("account:"+email,))
            audit(c,email,"Signed in")
            cookie(resp,"ap_staff",token,req,28800)
            return {"role":u["role"]}
        audit(c,email,"Sign-in failed")
    raise HTTPException(401,"Email or password is incorrect")

@router.post("/api/logout",dependencies=[Depends(browser_write)])
def logout(req:Request,resp:Response,user=Depends(staff)):
    with db(True) as c:
        c.execute("DELETE FROM logins WHERE token=?",(digest(req.cookies["ap_staff"]),))
        audit(c,user["email"],"Signed out")
    resp.delete_cookie("ap_staff",path="/")
    return {"ok":True}

def customer(req:Request):
    with db() as c:
        s=c.execute("SELECT id FROM sessions WHERE token=? AND created>? AND seen>?",(digest(req.cookies.get("ap_customer","")),now()-86400,now()-3600)).fetchone()
        if not s:
            raise HTTPException(401,"Refresh the page to start a new session")
        return s[0]
