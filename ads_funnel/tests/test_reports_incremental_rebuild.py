from datetime import date

from ads_funnel.api import db


def test_get_raw_dfs_filters_by_country_and_week_keys(monkeypatch):
    calls = []

    class Inspector:
        def get_table_names(self):
            return ["raw_camp_lx", "raw_port_lx"]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class Engine:
        def connect(self):
            return Connection()

    class DataFrame:
        empty = False
        columns = []

        def __setitem__(self, _key, _value):
            pass

    def fake_read_sql(statement, _conn, params=None):
        calls.append((str(statement), params))
        return DataFrame()

    monkeypatch.setattr(db, "get_engine", lambda: Engine())
    monkeypatch.setattr(db, "sa_inspect", lambda _engine: Inspector())
    monkeypatch.setattr(db.pd, "read_sql", fake_read_sql)

    db.get_raw_dfs(countries=["US"], week_keys=["26W31", "26W33"])

    assert len(calls) == 2
    for sql, params in calls:
        assert "WHERE" in sql
        assert "`国家` IN" in sql
        assert "`日期` BETWEEN" in sql
        assert params["country_0"] == "US"
        assert params["date_from"] == date(2026, 7, 27)
        assert params["date_to"] == date(2026, 8, 16)


def test_get_raw_dfs_allows_empty_port_rows_when_campaign_rows_exist(monkeypatch):
    class Inspector:
        def get_table_names(self):
            return ["raw_camp_lx", "raw_port_lx"]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class Engine:
        def connect(self):
            return Connection()

    class DataFrame:
        def __init__(self, empty):
            self.empty = empty
            self.columns = []

        def __setitem__(self, _key, _value):
            pass

    frames = [DataFrame(False), DataFrame(True)]

    monkeypatch.setattr(db, "get_engine", lambda: Engine())
    monkeypatch.setattr(db, "sa_inspect", lambda _engine: Inspector())
    monkeypatch.setattr(db.pd, "read_sql", lambda *_args, **_kwargs: frames.pop(0))

    df_c, df_p = db.get_raw_dfs(countries=["US"], week_keys=["26W33"])

    assert df_c is not None
    assert df_p is not None
    assert df_p.empty is True
