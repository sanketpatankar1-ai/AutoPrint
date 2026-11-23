import sqlite3
from windows_agent import process


def test_interrupted_submission_never_repeats():
    class Adapter:
        count=0
        def validate(self,*args): pass
        def submit(self,*args):
            self.count+=1
            raise RuntimeError('Crash after possible submission')
    adapter=Adapter();journal=sqlite3.connect(':memory:');journal.execute('CREATE TABLE jobs(lease TEXT PRIMARY KEY,spool INTEGER)')
    calls=[]
    def api(path,data=None,claim=None,binary=False):
        calls.append(path)
        return b'%PDF-test' if binary else {}
    job=dict(claim='lease',id='AP-test',printer='shop/Exact printer')
    import pytest
    with pytest.raises(RuntimeError):process(adapter,journal,job,api)
    process(adapter,journal,job,api)
    assert adapter.count==1
    assert calls.count('jobs/AP-test/file')==1


def test_disappeared_spool_job_requires_review():
    from windows_agent import Windows
    class Spooler:
        def OpenPrinter(self,name): return name
        def EnumJobs(self,*args): return []
        def ClosePrinter(self,*args): pass
    adapter=Windows.__new__(Windows);adapter.w=Spooler()
    assert adapter.observe('Exact',42,'AP lease')=='REQUIRES_REVIEW'
