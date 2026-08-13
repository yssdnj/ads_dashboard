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


def test_campaign_stats_by_date_range_returns_weekly_acos(monkeypatch):
    campaign_col = "\u5e7f\u544a\u6d3b\u52a8"
    date_col = "\u65e5\u671f"

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self.rows

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

        def execute(self, statement, params):
            sql = str(statement)
            if "GROUP BY `\u5e7f\u544a\u6d3b\u52a8`, `" in sql:
                return Result([
                    {campaign_col: "camp-a", date_col: date(2026, 7, 27), "sp": 10, "sl": 100},
                    {campaign_col: "camp-a", date_col: date(2026, 8, 3), "sp": 30, "sl": 100},
                    {campaign_col: "camp-b", date_col: date(2026, 8, 3), "sp": 0, "sl": 0},
                ])
            return Result([
                {
                    campaign_col: "camp-a",
                    "sp": 40,
                    "sl": 200,
                    "cl": 4,
                    "im": 400,
                    "or_": 2,
                    "ac": 20,
                    "ro": 5,
                    "cp": 10,
                    "ct": 1,
                    "cv": 50,
                },
                {
                    campaign_col: "camp-b",
                    "sp": 0,
                    "sl": 0,
                    "cl": 0,
                    "im": 10,
                    "or_": 0,
                    "ac": None,
                    "ro": None,
                    "cp": None,
                    "ct": 0,
                    "cv": None,
                },
            ])

    class Engine:
        def connect(self):
            return Connection()

    monkeypatch.setattr(db, "get_engine", lambda: Engine())

    result = db.get_campaign_stats_by_date_range("US", "2026-07-27", "2026-08-09")

    assert result["camp-a"]["wa"] == [10.0, 30.0]
    assert result["camp-b"]["wa"] == [None, None]
