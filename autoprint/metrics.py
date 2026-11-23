import csv
import io
from datetime import date, datetime, time, timedelta
from fastapi import HTTPException
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from .store import TZ, rows

def today():
    return datetime.now(TZ).date()

def period(preset,start=None,end=None):
    d=today()
    ranges={"today":(d,d),"yesterday":(d-timedelta(days=1),d-timedelta(days=1)),
            "week":(d-timedelta(days=6),d),"month":(d-timedelta(days=29),d),
            "thismonth":(d.replace(day=1),d),"year":(d.replace(month=1,day=1),d)}
    try:
        a,b=(date.fromisoformat(start),date.fromisoformat(end)) if preset=="custom" else ranges[preset]
        if a>b or (b-a).days>3660:
            raise ValueError()
        conv=lambda v:int(datetime.combine(v,time.min,TZ).timestamp())
        return conv(a),conv(b+timedelta(days=1))
    except (ValueError,KeyError,TypeError,OverflowError):
        raise HTTPException(422,"Choose a valid date range of up to ten years")

def money(c,bounds):
    revenue=c.execute("""SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='Paid' AND at>=? AND at<?""",bounds).fetchone()[0]
    refunds=c.execute("""SELECT COALESCE(SUM(r.amount),0) FROM refunds r JOIN payments p ON p.id=r.pid
                        WHERE p.status='Paid' AND r.status='processed' AND r.at>=? AND r.at<?""",bounds).fetchone()[0]
    return revenue,refunds

def summary(c,bounds):
    a,b=bounds
    s=dict(c.execute("""SELECT COUNT(*) orders,COALESCE(SUM(amount),0) order_amount FROM orders WHERE created>=? AND created<?""",bounds).fetchone())
    s.update(dict(c.execute("""SELECT COUNT(*) FILTER(WHERE state='Completed') documents,
        COALESCE(SUM(pages) FILTER(WHERE state='Completed'),0) pages,COUNT(*) FILTER(WHERE state='Failed') failed_jobs
        FROM outcomes WHERE finished>=? AND finished<?""",bounds).fetchone()))
    for status in ("Paid","Pending","Failed"):
        s[status.lower()]=c.execute("SELECT COUNT(*) FROM transaction_view WHERE status=? AND at>=? AND at<?",(status,a,b)).fetchone()[0]
    s["gross"],s["refunds"]=money(c,bounds)
    s["net"]=s["gross"]-s["refunds"]
    count=c.execute("SELECT COUNT(DISTINCT oid) FROM payments WHERE status='Paid' AND at>=? AND at<?",bounds).fetchone()[0]
    s["average"]=round(s["gross"]/count) if count else 0
    s["transactions"]=c.execute("SELECT COUNT(*) FROM payments WHERE at>=? AND at<?",bounds).fetchone()[0]
    for mode in ("BW","Colour"):
        s[mode]=c.execute("""SELECT COALESCE(SUM(p.amount),0) FROM payments p JOIN orders o ON o.id=p.oid
                            WHERE o.mode=? AND p.status='Paid' AND p.at>=? AND p.at<?""",(mode,a,b)).fetchone()[0]
    return s

def analytics(c,bounds):
    # Asia/Kolkata is UTC+05:30 throughout supported reporting years.
    a,b=bounds
    bucket="strftime('%Y-%m-%d',at,'unixepoch','+330 minutes')"
    points={}
    day=datetime.fromtimestamp(a,TZ).date()
    while day<datetime.fromtimestamp(b,TZ).date():
        points[day.isoformat()]={"day":day.isoformat(),"gross":0,"orders":0}
        day+=timedelta(days=1)
    for field,source,col,predicate,expr in [("gross","payments","at","AND status='Paid'","SUM(amount)"),("orders","orders","created","","COUNT(*)")]:
        sql=f"SELECT {bucket.replace(',at,',','+col+',')} d,{expr} v FROM {source} WHERE {col}>=? AND {col}<? {predicate} GROUP BY d"
        for r in c.execute(sql,bounds):
            points[r["d"]][field]=r["v"]
    months={}
    for d in points.values():
        m=d["day"][:7]
        months[m]=months.get(m,0)+d["gross"]
    return {"summary":summary(c,bounds),"days":list(points.values()),"months":[{"day":k,"gross":v} for k,v in months.items()],
            "periods":{p:money(c,period(p))[0] for p in ("today","yesterday","week","thismonth","year")}}

def printers(c):
    return rows(c,"""SELECT p.*, (SELECT GROUP_CONCAT(mode,', ') FROM routes WHERE printer=p.id) modes,
      (SELECT id FROM orders WHERE printer=p.id AND state='Printing' LIMIT 1) current_job,
      (SELECT COUNT(*) FROM orders WHERE state='Queued' AND mode IN(SELECT mode FROM routes WHERE printer=p.id)) pending,
      (SELECT COUNT(*) FROM outcomes WHERE printer=p.id AND state='Completed') completed,
      (SELECT COUNT(*) FROM outcomes WHERE printer=p.id AND state='Failed') failed,
      (SELECT COALESCE(SUM(pages),0) FROM outcomes WHERE printer=p.id AND state='Completed') pages
      FROM printers p ORDER BY p.name""")

def list_data(c,section,q="",payment="",state="",bounds=None,page=1,limit=30):
    views={"orders":("order_view","created",["id","session","document"]),
           "payments":("transaction_view","at",["id","oid"]),
           "sessions":("""(SELECT s.id,s.created,s.seen,COUNT(o.id) orders,COALESCE(SUM(o.amount),0) amount FROM sessions s
                          LEFT JOIN orders o ON s.id=o.session GROUP BY s.id)""","seen",["id"]),
           "events":("events","at",["kind","message"]),"audit":("audit","at",["actor","action","subject"])}
    if section not in views:
        raise HTTPException(404)
    view,col,search=views[section]
    conditions=["1=1"]
    args=[]
    if bounds:
        conditions+= [f"{col}>=?",f"{col}<?"]
        args.extend(bounds)
    if q:
        conditions.append("("+ " OR ".join(f"instr(lower({f}),lower(?))>0" for f in search)+")")
        args.extend([q]*len(search))
    if payment and section in ("orders","payments"):
        if section=="payments" and payment=="Refunded":
            conditions.append("refunded>0")
        else:
            conditions.append(("payment" if section=="orders" else "status")+"=?")
            args.append(payment)
    if state and section=="orders":
        conditions.append("state=?")
        args.append(state)
    base=" FROM "+view+" WHERE "+" AND ".join(conditions)
    total=c.execute("SELECT COUNT(*)"+base,args).fetchone()[0]
    data=rows(c,"SELECT *"+base+f" ORDER BY {col} DESC, "+("oid" if section=="payments" else "id")+" DESC LIMIT ? OFFSET ?",(*args,limit,(page-1)*limit))
    return {"items":data,"total":total,"page":page}

def export(c,kind,fmt,bounds):
    if kind not in ("daily","weekly","monthly","printing","payments","printers") or fmt not in ("csv","xlsx"):
        raise HTTPException(422,"Unknown report")
    start,end=[datetime.fromtimestamp(t,TZ).date().isoformat() for t in (bounds[0],bounds[1]-1)]
    s=summary(c,bounds)
    fields=["From","Through","Orders","Successful jobs","Failed jobs","Pages printed","Verified revenue (INR)","Processed refunds (INR)","Net revenue (INR)"]
    main=[fields,[start,end,s["orders"],s["documents"],s["failed_jobs"],s["pages"],s["gross"]/100,s["refunds"]/100,s["net"]/100]]
    if kind=="payments":
        detail=[["Transaction","Order","Time (IST)","Method","Status","Amount (INR)","Refund status","Refunded to date (INR)"]]
        for r in c.execute("SELECT * FROM transaction_view WHERE at>=? AND at<? ORDER BY at",bounds):
            detail.append([r["id"],r["oid"],datetime.fromtimestamp(r["at"],TZ).isoformat(),r["method"],r["status"],r["amount"]/100,r["refund_status"],r["refunded"]/100])
        detail.append([])
        detail.append(["Refund","Transaction","Refund event time (IST)","Status","Refund amount (INR)"])
        for r in c.execute("SELECT r.* FROM refunds r JOIN payments p ON r.pid=p.id WHERE r.at>=? AND r.at<? ORDER BY r.at",bounds):
            detail.append([r["id"],r["pid"],datetime.fromtimestamp(r["at"],TZ).isoformat(),r["status"],r["amount"]/100])
    elif kind=="printers":
        detail=[["Printer","Model","Successful jobs","Failed jobs","Pages printed"]]
        for r in c.execute("""SELECT p.name,p.model,COUNT(*) FILTER(WHERE o.state='Completed'),COUNT(*) FILTER(WHERE o.state='Failed'),
            COALESCE(SUM(o.pages) FILTER(WHERE o.state='Completed'),0) FROM printers p LEFT JOIN outcomes o
            ON o.printer=p.id AND o.finished>=? AND o.finished<? GROUP BY p.id""",bounds):
            detail.append(list(r))
    elif kind=="printing":
        detail=[["Order","Session","Document","Order time (IST)","Pages","Copies","Type","Print status","Payment","Amount (INR)"]]
        for r in c.execute("SELECT * FROM order_view WHERE created>=? AND created<? ORDER BY created",bounds):
            detail.append([r["id"],r["session"],r["document"],datetime.fromtimestamp(r["created"],TZ).isoformat(),r["pages"],r["copies"],r["mode"],r["state"],r["payment"],r["amount"]/100])
    else:
        detail=[["Date (IST)","Orders","Verified revenue (INR)"]]+[[d["day"],d["orders"],d["gross"]/100] for d in analytics(c,bounds)["days"]]
    def safe(row):
        return ["'"+v if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@","\t","\r","\n")) else v for v in row]
    if fmt=="csv":
        buf=io.StringIO()
        csv.writer(buf).writerows(safe(r) for r in main+[[]]+detail)
        content=buf.getvalue().encode("utf-8-sig")
        mime="text/csv"
    else:
        wb=Workbook()
        sheet=wb.active
        sheet.title="Summary"
        for title,table in [("Summary",main),("Details",detail)]:
            ws=sheet if title=="Summary" else wb.create_sheet(title)
            for r in table:
                ws.append(safe(r))
            for cell in ws[1]:
                cell.font=Font(bold=True,color="FFFFFF")
                cell.fill=PatternFill("solid",fgColor="4C419C")
            ws.freeze_panes="A2"
            for column in ws.columns:
                ws.column_dimensions[column[0].column_letter].width=min(48,max(len(str(x.value or "")) for x in column)+3)
        buf=io.BytesIO()
        wb.save(buf)
        content=buf.getvalue()
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(content,media_type=mime,headers={"Content-Disposition":f'attachment; filename="autoprint-{kind}-{start}-{end}.{fmt}"'})
