import os, hashlib, json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from src.config.database import get_db
from src.config.settings import ARTIFACT_DIR
from src.models import Report, Delivery
from src.schemas.report import ReportInput
from src.middlewares.auth import current_user, admin_only
from src.services import reports
from src.services.audit import record
from src.utils.serialization import report_dict, delivery_dict
router=APIRouter()
@router.get('/reports')
def listing(db=Depends(get_db),u=Depends(current_user)):
    return [report_dict(r) for r in db.scalars(select(Report).order_by(Report.id.desc()))]
@router.post('/reports',status_code=201)
def create(data:ReportInput,db=Depends(get_db),u=Depends(admin_only)):
    r=reports.create(db,data); record(db,u.email,'report.schedule',{'id':r.id,'destination':r.destination}); db.commit()
    return report_dict(r)
@router.post('/reports/{id}/toggle')
def toggle(id:int,db=Depends(get_db),u=Depends(admin_only)):
    r=db.get(Report,id)
    if not r:raise HTTPException(404,'分发计划不存在')
    r.enabled=not r.enabled;record(db,u.email,'report.toggle',{'id':id,'enabled':r.enabled});db.commit()
    return report_dict(r)
@router.get('/deliveries')
def deliveries(db=Depends(get_db),u=Depends(current_user)):
    return [delivery_dict(d) for d in db.scalars(select(Delivery).order_by(Delivery.id.desc()).limit(100))]
@router.post('/deliveries/{id}/retry')
def retry(id:int,db=Depends(get_db),u=Depends(admin_only)):
    d=db.get(Delivery,id)
    if not d:raise HTTPException(404,'归档不存在')
    if d.status=='success':raise HTTPException(409,'该归档已成功，无需重试')
    reports.attempt(db,d)
    r=db.get(Report,d.report_id)
    if d.status=='success' and r and r.next_run<=d.scheduled_at:
        r.next_run=d.scheduled_at+r.interval_seconds
    record(db,u.email,'delivery.retry',{'id':d.id,'status':d.status,'error':d.error})
    db.commit()
    if d.status!='success':raise HTTPException(409,'重试失败：'+(d.error or '未知原因'))
    return delivery_dict(d)
def _sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(65536),b''):h.update(chunk)
    return h.hexdigest()
@router.get('/deliveries/{id}/verify')
def verify(id:int,db=Depends(get_db),u=Depends(current_user)):
    d=db.get(Delivery,id)
    if not d:raise HTTPException(404,'归档不存在')
    csv_path=os.path.join(ARTIFACT_DIR,d.filename) if d.filename else None
    json_path=csv_path+'.json' if csv_path else None
    csv_actual=_sha256(csv_path) if csv_path and os.path.exists(csv_path) else None
    json_actual=_sha256(json_path) if json_path and os.path.exists(json_path) else None
    result={'id':d.id,'status':d.status,
            'csv':{'exists':csv_actual is not None,'recorded':d.checksum or None,'actual':csv_actual,
                   'matches':(csv_actual==d.checksum) if csv_actual and d.checksum else None},
            'json':{'exists':json_actual is not None,'recorded':d.json_checksum or None,'actual':json_actual,
                    'matches':(json_actual==d.json_checksum) if json_actual and d.json_checksum else None}}
    # The JSON half embeds the CSV fingerprint of the same approval snapshot.
    linked=None
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path,encoding='utf-8') as f:embedded=json.load(f).get('checksum')
            result['json']['embedded_csv_checksum']=embedded
            linked=(embedded==csv_actual) if csv_actual else None
        except Exception:linked=False
    result['consistent']=linked
    checks=[result['csv']['matches'],result['json']['matches'],linked]
    result['ok']=csv_actual is not None and json_actual is not None and all(c is not False for c in checks)
    return result
@router.get('/deliveries/{id}/download')
def download(id:int,format:str='csv',db=Depends(get_db),u=Depends(current_user)):
    d=db.get(Delivery,id)
    if not d:raise HTTPException(404,'归档不存在')
    if format not in ('csv','json'):raise HTTPException(422,'支持 CSV 或 JSON')
    filename=d.filename+('.json' if format=='json' else '')
    p=os.path.join(ARTIFACT_DIR,filename)
    if not os.path.exists(p):raise HTTPException(404,'归档文件不存在，请重新安排分发')
    return FileResponse(p,filename=filename,media_type='application/json' if format=='json' else 'text/csv; charset=utf-8')
