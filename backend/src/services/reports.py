import time, os, csv, io, hashlib, json
from sqlalchemy import select
from fastapi import HTTPException
from src.models import Job, Report, Delivery
from src.config.settings import ARTIFACT_DIR
def create(db, data):
    j=db.get(Job,data.job_id)
    if not j or j.status!='completed' or not j.approved:
        raise HTTPException(409,'请先选择已完成且获批准的分析结果')
    r=Report(name=data.name, job_id=j.id, destination=data.destination, interval_seconds=data.interval_seconds, next_run=time.time()+data.delay_seconds, enabled=True)
    db.add(r); db.flush()
    return r
def slot_filename(report, slot):
    # Deterministic per (report, due slot): a retry after a crash rewrites the
    # same path instead of scattering duplicate files in the inbox.
    return f'report-{report.id}-slot-{int(round(slot*1000))}.csv'
def _render(job, report, delivery):
    out=io.StringIO()
    out.write('\ufeff')
    table=job.result.get('table',{})
    cols=table.get('columns',[])
    writer=csv.writer(out)
    def safe(v):
        if isinstance(v,(int,float)) and not isinstance(v,bool): return v
        text=str(v)
        return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    writer.writerow([safe(c['label']) for c in cols])
    for row in table.get('rows',[]): writer.writerow([safe(row.get(c['key'],'')) for c in cols])
    csv_bytes=out.getvalue().encode('utf-8')
    csv_sha=hashlib.sha256(csv_bytes).hexdigest()
    # Both halves come from the same approved result snapshot; the JSON embeds
    # the CSV fingerprint so the pair can be verified as one archive.
    payload={'job':job.name,'job_id':job.id,'params':job.params,'result':job.result,
             'checksum':csv_sha,'report_id':report.id,'delivery_id':delivery.id,
             'scheduled_at':delivery.scheduled_at,'generated_at':delivery.created_at}
    json_bytes=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
    return csv_bytes,json_bytes
def _write_atomic(path, data):
    # Temp file + fsync + rename: the final path only ever holds a complete file.
    tmp=f'{path}.tmp-{os.getpid()}'
    with open(tmp,'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)
def attempt(db, delivery):
    """(Re)generate the archive files for one delivery slot and record the outcome."""
    report=db.get(Report,delivery.report_id)
    job=db.get(Job,report.job_id) if report else None
    if not report or not job or not job.approved:
        delivery.status='failed'
        delivery.error='关联分析不存在或未获批准，无法归档'
        return delivery
    try:
        csv_bytes,json_bytes=_render(job,report,delivery)
        os.makedirs(ARTIFACT_DIR,exist_ok=True)
        base=os.path.join(ARTIFACT_DIR,delivery.filename)
        _write_atomic(base,csv_bytes)
        _write_atomic(base+'.json',json_bytes)
        delivery.checksum=hashlib.sha256(csv_bytes).hexdigest()
        delivery.json_checksum=hashlib.sha256(json_bytes).hexdigest()
        delivery.status='success'
        delivery.error=''
    except Exception as e:
        delivery.status='failed'
        delivery.error=str(e)[:990]
    return delivery
def deliver(db, report):
    """Archive the report's current due slot. Each (report, slot) yields exactly
    one delivery row and one pair of files, however often this is retried."""
    slot=report.next_run
    d=db.scalar(select(Delivery).where(Delivery.report_id==report.id,Delivery.scheduled_at==slot))
    if d is None:
        d=Delivery(report_id=report.id, scheduled_at=slot, created_at=time.time(), status='pending',
                   checksum='', json_checksum='', filename=slot_filename(report,slot),
                   destination=report.destination, error='')
        db.add(d); db.flush()
    if d.status!='success':
        attempt(db,d)
    # Whether the slot archived cleanly or not, the schedule moves on; failures
    # stay visible in the inbox and can be retried from there.
    report.next_run=slot+report.interval_seconds
    return d
