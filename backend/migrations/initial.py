import os
from sqlalchemy import text, inspect, select
from src.config.database import Base, engine, Session
from src.config.settings import ARTIFACT_DIR
from src import models
from src.models import Delivery
# Columns added after the first release. create_all only creates missing
# tables, so existing databases are upgraded here idempotently.
DELIVERY_COLUMNS = [
    ('scheduled_at', 'DOUBLE NOT NULL DEFAULT 0'),
    ('status', "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
    ('error', "VARCHAR(1000) NOT NULL DEFAULT ''"),
    ('json_checksum', "VARCHAR(64) NOT NULL DEFAULT ''"),
]
def migrate():
    Base.metadata.create_all(engine)
    _upgrade_deliveries()
def _upgrade_deliveries():
    existing = {c['name'] for c in inspect(engine).get_columns('deliveries')}
    with engine.begin() as conn:
        for name, ddl in DELIVERY_COLUMNS:
            if name not in existing:
                conn.execute(text(f'ALTER TABLE deliveries ADD COLUMN {name} {ddl}'))
    _backfill_legacy()
    insp = inspect(engine)
    names = {i['name'] for i in insp.get_indexes('deliveries')} | {c['name'] for c in insp.get_unique_constraints('deliveries')}
    if 'uq_delivery_slot' not in names:
        with engine.begin() as conn:
            conn.execute(text('CREATE UNIQUE INDEX uq_delivery_slot ON deliveries (report_id, scheduled_at)'))
def _backfill_legacy():
    # Rows written before archiving became slot-based keep their files and stay
    # downloadable; ones whose files are missing become retryable failures.
    with Session() as db:
        legacy = list(db.scalars(select(Delivery).where(Delivery.scheduled_at == 0)))
        for d in legacy:
            d.scheduled_at = d.created_at
            if d.filename and os.path.exists(os.path.join(ARTIFACT_DIR, d.filename)):
                d.status = 'success'
            else:
                d.status = 'failed'
                d.error = '历史归档文件缺失，可重试生成'
        db.commit()
if __name__ == '__main__':
    migrate()
