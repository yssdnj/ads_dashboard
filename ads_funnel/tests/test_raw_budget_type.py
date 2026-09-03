import pandas as pd
from sqlalchemy import String, create_engine, inspect, text

from ads_funnel.api import db


def test_empty_budget_type_creates_text_column(monkeypatch):
    engine = create_engine('sqlite://')
    monkeypatch.setattr(db, 'get_engine', lambda: engine)
    frame = pd.DataFrame({'日期': ['2026-08-10'], '预算类型': [float('nan')]})
    db._upsert_lx('raw_camp_lx', frame, ['日期'])
    columns = {c['name']: c for c in inspect(engine).get_columns('raw_camp_lx')}
    assert isinstance(columns['预算类型']['type'], String)
    db._upsert_lx('raw_camp_lx', pd.DataFrame({
        '日期': ['2026-08-11'], '预算类型': ['每日'],
    }), ['日期'])
    with engine.connect() as conn:
        assert conn.execute(text('SELECT `预算类型` FROM raw_camp_lx ORDER BY `日期`')).scalars().all() == [None, '每日']
