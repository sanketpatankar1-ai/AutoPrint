"""Native Windows GDI adapter. PDF bytes are supplied only by authenticated claims."""
import io
import logging
import os
import signal
import random
import sqlite3
import threading
from urllib.parse import urlencode, urlsplit
from pathlib import Path
import print_agent as transport


def process(adapter, journal, job, api=transport.api):
    lease=job['claim']; oid=job['id']
    name=job['printer'].split('/',1)[1]
    prior=journal.execute('SELECT spool FROM jobs WHERE lease=?',(lease,)).fetchone()
    if prior:
        # Never repeat a GDI submission after interruption, even if spool history vanished.
        phase=adapter.observe(name,prior[0],oid+' '+lease) if hasattr(adapter,'observe') else 'REQUIRES_REVIEW'
        api('jobs/'+oid+'/spool',{'phase':phase},lease)
        return
    content=api('jobs/'+oid+'/file',claim=lease,binary=True)
    if len(content)>128*1024**2 or not content.startswith(b'%PDF-'):
        raise ValueError('Invalid PDF')
    adapter.validate(name,job,content)
    journal.execute('INSERT INTO jobs VALUES(?,NULL)',(lease,)); journal.commit()
    try:
        spool=adapter.submit(name,job,content)
        journal.execute('UPDATE jobs SET spool=? WHERE lease=?',(spool,lease)); journal.commit()
    finally:
        api('jobs/'+oid+'/spool',{'phase':'Awaiting printer confirmation'},lease)


class Windows:
    def __init__(self):
        import win32print
        self.w=win32print

    def devices(self):
        result=[]
        for p in self.w.EnumPrinters(self.w.PRINTER_ENUM_LOCAL|self.w.PRINTER_ENUM_CONNECTIONS,None,2):
            status=p['Status']; dm=p['pDevMode']
            blocked=status & (self.w.PRINTER_STATUS_OFFLINE|self.w.PRINTER_STATUS_ERROR|self.w.PRINTER_STATUS_PAPER_JAM|self.w.PRINTER_STATUS_PAPER_OUT|self.w.PRINTER_STATUS_PAUSED)
            result.append(dict(name=p['pPrinterName'],model=p['pDriverName'],online=not bool(status & self.w.PRINTER_STATUS_OFFLINE),available=not bool(blocked),error='Windows status '+str(status) if status else '',capabilities={'papers':['A4'] if dm.PaperSize==9 else [],'orientations':[3,4],'layouts':[1],'max_copies':1,'duplex':False}))
        return result

    def observe(self,name,spool,title):
        handle=self.w.OpenPrinter(name)
        try:
            matches=[j for j in self.w.EnumJobs(handle,0,999,1) if j.get('pDocument')==title]
            if len(matches)!=1 or (spool and matches[0]['JobId']!=spool):
                return 'REQUIRES_REVIEW'
            status=matches[0].get('Status',0)
            if status & (self.w.JOB_STATUS_ERROR|self.w.JOB_STATUS_OFFLINE|self.w.JOB_STATUS_PAPEROUT|self.w.JOB_STATUS_DELETED):
                return 'REQUIRES_REVIEW'
            if status & (self.w.JOB_STATUS_PRINTED|self.w.JOB_STATUS_COMPLETE):
                return 'REQUIRES_REVIEW'
            return 'Printing' if status & self.w.JOB_STATUS_PRINTING else 'SPOOLED'
        finally:
            self.w.ClosePrinter(handle)

    def validate(self,name,job,content):
        device=next((p for p in self.devices() if p['name']==name),None)
        if not device or not device['available']: raise ValueError('Configured printer unavailable')
        if job['paper'] not in device['capabilities']['papers'] or job['copies']!=1 or job['duplex'] or job.get('layout',1)!=1:
            raise ValueError('Unsupported Windows print settings')
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(content) as pdf:
            if not 1<=len(pdf)<=1000: raise ValueError('Invalid page count')

    def submit(self,name,job,content):
        import win32ui
        import pypdfium2 as pdfium
        from PIL import ImageWin, ImageOps
        handle=self.w.OpenPrinter(name)
        dc=win32ui.CreateDC()
        try:
            dm=self.w.GetPrinter(handle,2)['pDevMode']
            dm.Orientation=2 if job.get('orientation')==4 else 1
            dm.Color=1 if job['mode']=='BW' else 2
            dc.CreatePrinterDC(name); dc.ResetDC(dm)
            spool=dc.StartDoc(job['id']+' '+job['claim'])
            with pdfium.PdfDocument(content) as pdf:
                for page in pdf:
                    bitmap=page.render(scale=2)
                    try:
                        image=bitmap.to_pil().convert('RGB')
                        if job['mode']=='BW': image=ImageOps.grayscale(image).convert('RGB')
                        width,height=dc.GetDeviceCaps(8),dc.GetDeviceCaps(10)
                        scale=min(width/image.width,height/image.height)
                        w,h=int(image.width*scale),int(image.height*scale)
                        x,y=(width-w)//2,(height-h)//2
                        dc.StartPage(); ImageWin.Dib(image).draw(dc.GetHandleOutput(),(x,y,x+w,y+h)); dc.EndPage()
                    finally: bitmap.close(); page.close()
            dc.EndDoc()
            return spool
        except Exception:
            try: dc.AbortDoc()
            except Exception: pass
            raise
        finally:
            dc.DeleteDC(); self.w.ClosePrinter(handle)


def main():
    import msvcrt
    if os.name!='nt': raise SystemExit('Windows required')
    if len(transport.TOKEN)<32 or urlsplit(transport.BASE).scheme!='https': raise SystemExit('HTTPS and strong agent token required')
    directory=Path(os.environ.get('AGENT_DATA','./agent-data'));directory.mkdir(parents=True,exist_ok=True)
    lock=open(directory/'windows.lock','a+b');lock.write(b'0');lock.flush();lock.seek(0)
    msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    journal=sqlite3.connect(directory/'windows-jobs.db');journal.execute('CREATE TABLE IF NOT EXISTS jobs(lease TEXT PRIMARY KEY,spool INTEGER)')
    stop=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stop.set());signal.signal(signal.SIGTERM,lambda *_:stop.set())
    adapter=Windows()
    failures=0
    while not stop.is_set():
        try:
            printers=adapter.devices();transport.api('heartbeat',{'printers':printers})
            jobs=transport.api('active');occupied={j['printer'].split('/',1)[1] for j in jobs}
            for p in printers:
                if p['available'] and p['name'] not in occupied:
                    job=transport.api('claim?'+urlencode({'printer':p['name']}),{})
                    if job: jobs.append(job)
            for job in jobs:
                try: process(adapter,journal,job)
                except Exception as error: logging.warning('Windows job held: %s',type(error).__name__)
            failures=0
        except Exception as error:
            failures=min(failures+1,6)
            logging.warning('Windows connection retry: %s',type(error).__name__)
        stop.wait(min(60,2**failures)+random.uniform(0,1) if failures else 5)
    journal.close();lock.close()

if __name__=='__main__':main()
