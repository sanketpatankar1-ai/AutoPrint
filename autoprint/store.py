import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")
DEFAULTS = dict(name="AutoPrint", logo="", contact="", address="", bw=200, colour=1000,
                bw_duplex=200, colour_duplex=1000, minimum=100, sizes=["A4"], max_mb=10,
                uploads=True, printing=True, retention=24, cleanup=True, opens="00:00", closes="00:00")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE COLLATE NOCASE, hash TEXT NOT NULL,role TEXT CHECK(role IN ('owner','admin')));
CREATE TABLE IF NOT EXISTS logins(token TEXT PRIMARY KEY, uid INTEGER REFERENCES users, expires INTEGER, seen INTEGER);
CREATE TABLE IF NOT EXISTS attempts(k TEXT, at INTEGER);
CREATE INDEX IF NOT EXISTS attempts_key ON attempts(k,at);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,token TEXT UNIQUE,created INTEGER,seen INTEGER);
CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,seen INTEGER,online INTEGER);
CREATE TABLE IF NOT EXISTS printers(id TEXT PRIMARY KEY,agent TEXT REFERENCES agents,name TEXT,model TEXT,
 online INTEGER,available INTEGER,low_paper INTEGER,error TEXT,enabled INTEGER DEFAULT 1,paused INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS routes(mode TEXT PRIMARY KEY,printer TEXT REFERENCES printers);
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY,session TEXT REFERENCES sessions,document TEXT,pages INTEGER,mode TEXT,
 copies INTEGER,duplex INTEGER,paper TEXT,amount INTEGER,created INTEGER,gateway TEXT UNIQUE,state TEXT DEFAULT 'Awaiting payment',
 printer TEXT REFERENCES printers,claim TEXT,error TEXT DEFAULT '',finished INTEGER);
CREATE TABLE IF NOT EXISTS outcomes(claim TEXT PRIMARY KEY,oid TEXT REFERENCES orders,printer TEXT REFERENCES printers,state TEXT,pages INTEGER,finished INTEGER);
CREATE TABLE IF NOT EXISTS files(oid TEXT PRIMARY KEY REFERENCES orders,content BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS printer_capabilities(id TEXT PRIMARY KEY REFERENCES printers, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS print_options(oid TEXT PRIMARY KEY REFERENCES orders,orientation INTEGER,layout INTEGER);
CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,session TEXT REFERENCES sessions,name TEXT,pages INTEGER,
 original_size INTEGER,content BLOB,expires INTEGER,oid TEXT REFERENCES orders);
CREATE INDEX IF NOT EXISTS draft_session ON drafts(session,expires);
CREATE TABLE IF NOT EXISTS draft_options(token TEXT PRIMARY KEY, options TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collections(oid TEXT PRIMARY KEY REFERENCES orders,at INTEGER,staff_id INTEGER REFERENCES users);
CREATE TABLE IF NOT EXISTS reprints(session TEXT,request_id TEXT,source TEXT REFERENCES orders,oid TEXT REFERENCES orders,PRIMARY KEY(session,request_id));
CREATE TABLE IF NOT EXISTS resolutions(key TEXT PRIMARY KEY,oid TEXT NOT NULL,claim TEXT,action TEXT,actor TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS spool_status(claim TEXT PRIMARY KEY,phase TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS payments(id TEXT PRIMARY KEY,oid TEXT REFERENCES orders,amount INTEGER,status TEXT,method TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS refunds(id TEXT PRIMARY KEY,pid TEXT,amount INTEGER,status TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at INTEGER,kind TEXT,message TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at INTEGER,actor TEXT,action TEXT,subject TEXT);
CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY,n INTEGER);
INSERT OR IGNORE INTO revision VALUES(1,0);
CREATE INDEX IF NOT EXISTS order_dates ON orders(created);
CREATE INDEX IF NOT EXISTS order_queue ON orders(state,created);
CREATE INDEX IF NOT EXISTS payment_order ON payments(oid);
CREATE INDEX IF NOT EXISTS payment_dates ON payments(at);
CREATE INDEX IF NOT EXISTS refund_payment ON refunds(pid);
CREATE VIEW IF NOT EXISTS order_view AS
 SELECT o.id,o.session,o.document,o.pages,o.mode,o.copies,o.duplex,o.paper,o.amount,o.created,o.state,o.printer,o.error,o.finished,
 CASE WHEN EXISTS(SELECT 1 FROM payments p WHERE p.oid=o.id AND p.status='Paid') THEN
 CASE WHEN EXISTS(SELECT 1 FROM refunds r JOIN payments p ON p.id=r.pid WHERE p.oid=o.id AND r.status='processed') THEN 'Refunded' ELSE 'Paid' END
 WHEN EXISTS(SELECT 1 FROM payments p WHERE p.oid=o.id AND p.status='Pending') THEN 'Pending'
 WHEN EXISTS(SELECT 1 FROM payments p WHERE p.oid=o.id AND p.status='Failed') THEN 'Failed' ELSE 'Pending' END payment
 FROM orders o;
CREATE TRIGGER IF NOT EXISTS guard_new_queue BEFORE INSERT ON orders
 WHEN NEW.state IN ('Queued','Printing')
 BEGIN SELECT RAISE(ABORT,'New orders must await verified payment'); END;
CREATE TRIGGER IF NOT EXISTS guard_dispatch BEFORE UPDATE OF state ON orders
 WHEN NEW.state IN ('Queued','Printing') AND NOT EXISTS
 (SELECT 1 FROM payments p WHERE p.oid=NEW.id AND p.status='Paid' AND p.amount=NEW.amount
 AND NOT EXISTS(SELECT 1 FROM refunds r WHERE r.pid=p.id AND r.status='processed'))
 BEGIN SELECT RAISE(ABORT,'Verified payment required for dispatch'); END;
CREATE VIEW IF NOT EXISTS transaction_view AS
 SELECT p.id,p.oid,p.amount,p.status,p.method,p.at,
 COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.pid=p.id AND r.status='processed'),0) refunded,
 COALESCE((SELECT r.status FROM refunds r WHERE r.pid=p.id ORDER BY r.at DESC,r.id DESC LIMIT 1),'') refund_status
 FROM payments p
 UNION ALL SELECT '',o.id,o.amount,'Pending','',o.created,0,'' FROM orders o WHERE NOT EXISTS(SELECT 1 FROM payments p WHERE p.oid=o.id);
"""

def now():
    return int(time.time())

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def secret_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**15, r=8, p=1, maxmem=64*1024**2)
    return salt + ":" + result.hex()

def path():
    return Path(os.environ.get("AUTOPRINT_DATA", "./data")).resolve()

@contextmanager
def db(write=False):
    con = sqlite3.connect(path() / "autoprint.db", timeout=12)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield con
        con.commit()
    finally:
        con.close()

def init():
    path().mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(path() / "autoprint.db") as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.execute("INSERT OR IGNORE INTO settings VALUES(1,?)",(json.dumps(DEFAULTS),))
    (path() / "autoprint.db").chmod(0o600)

def config(c):
    return json.loads(c.execute("SELECT value FROM settings WHERE id=1").fetchone()[0])

def changed(c):
    c.execute("UPDATE revision SET n=n+1 WHERE id=1")

def event(c, kind, message):
    c.execute("INSERT INTO events(at,kind,message) VALUES(?,?,?)",(now(),kind,message))
    changed(c)

def audit(c, actor, action, subject=""):
    c.execute("INSERT INTO audit(at,actor,action,subject) VALUES(?,?,?,?)",(now(),actor,action,subject))
    changed(c)

def rows(c,sql,params=()):
    return [dict(r) for r in c.execute(sql,params)]

def housekeeping():
    with db(True) as c:
        t = now()
        for a in c.execute("SELECT id FROM agents WHERE online=1 AND seen<?",(t-45,)).fetchall():
            c.execute("UPDATE agents SET online=0 WHERE id=?",a)
            event(c,"warning","Local print agent disconnected · "+a[0])
            for p in c.execute("SELECT name FROM printers WHERE online AND agent=?",a):
                event(c,"warning","Printer disconnected · "+p[0])
            c.execute("UPDATE printers SET online=0,available=0 WHERE agent=?",a)
        c.execute("DELETE FROM drafts WHERE expires<=?",(t,))
        s=config(c)
        if s["cleanup"]:
            cutoff=t-s["retention"]*3600
            expired=c.execute("""UPDATE orders SET state='Upload expired' WHERE state='Awaiting payment' AND created<?
                                 AND EXISTS(SELECT 1 FROM files WHERE oid=orders.id)""",(cutoff,))
            if expired.rowcount:
                changed(c)
            c.execute("""DELETE FROM files WHERE oid IN (SELECT id FROM orders WHERE state NOT IN ('Printing','Queued')
                       AND COALESCE(finished,created)<?)""",(cutoff,))
        c.execute("DELETE FROM attempts WHERE at<?",(t-900,))
        c.execute("DELETE FROM logins WHERE expires<? OR seen<?",(t,t-3600))
