import time, json, logging, glob, os
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL, ARTIFACT_DIR
from src.models import Job, Dataset, Report, Delivery
from src.services.business import analyze
from src.services.reports import deliver, attempt
from src.services.audit import record
logging.basicConfig(level=logging.INFO)
def execute(job_id):
    with Session() as db:
        j=db.get(Job,job_id)
        if not j or j.status!='queued':return
        j.status='running'; db.commit()
        try:
            d=db.get(Dataset,j.dataset_id)
            result=analyze(d.records,j.params)
            json.dumps(result,allow_nan=False)
            j.result=result;j.status='completed';j.finished_at=time.time()
            record(db,'worker','job.completed',{'id':j.id})
        except Exception as e:
            logging.exception('Analysis failed')
            j.status='failed';j.error=str(e)[:990];j.finished_at=time.time()
            record(db,'worker','job.failed',{'id':j.id,'error':j.error})
        db.commit()
def _deliver_due(report_id):
    # One short transaction per due report: a failure or crash here can never
    # roll back other plans, and the slot row makes a retry idempotent.
    with Session() as db:
        r=db.get(Report,report_id)
        if not r or not r.enabled or r.next_run>time.time():return
        d=deliver(db,r)
        if d.status=='success':
            record(db,'worker','report.delivered',{'id':r.id,'delivery':d.id})
        else:
            record(db,'worker','report.delivery_failed',{'id':r.id,'delivery':d.id,'error':d.error})
        db.commit()
def _resume_pending(delivery_id):
    # Finish archives interrupted mid-write; slots that already succeeded are
    # never touched again.
    with Session() as db:
        d=db.get(Delivery,delivery_id)
        if not d or d.status!='pending':return
        attempt(db,d)
        r=db.get(Report,d.report_id)
        if d.status=='success' and r and r.next_run<=d.scheduled_at:
            r.next_run=d.scheduled_at+r.interval_seconds
        record(db,'worker','delivery.resumed',{'delivery':d.id,'status':d.status,'error':d.error})
        db.commit()
def tick():
    with Session() as db:
        queued=list(db.scalars(select(Job.id).where(Job.status=='queued').limit(3)))
    for job_id in queued:execute(job_id)
    with Session() as db:
        due=list(db.scalars(select(Report.id).where(Report.enabled==True,Report.next_run<=time.time())))
    for report_id in due:
        try:_deliver_due(report_id)
        except Exception:logging.exception('Delivery pass failed for report %s',report_id)
    with Session() as db:
        pending=list(db.scalars(select(Delivery.id).where(Delivery.status=='pending').limit(10)))
    for delivery_id in pending:
        try:_resume_pending(delivery_id)
        except Exception:logging.exception('Resume pass failed for delivery %s',delivery_id)
def main():
    r=Redis.from_url(REDIS_URL,socket_timeout=3)
    # This Compose stack has a single worker. A crash must not strand its jobs.
    with Session() as db:
        for j in db.scalars(select(Job).where(Job.status=='running')):
            j.status='queued';record(db,'worker','job.recovered',{'id':j.id})
        db.commit()
    # Half-written temp files from an interrupted archive are never final
    # artifacts; only atomic renames land at a delivery path.
    for tmp in glob.glob(os.path.join(ARTIFACT_DIR,'*.tmp-*')):
        try:os.remove(tmp)
        except OSError:pass
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item=r.blpop('analysis:queue',timeout=1)
            if item:execute(int(item[1]))
            tick()
        except Exception:
            logging.exception('Worker retry');time.sleep(2)
if __name__=='__main__':main()
