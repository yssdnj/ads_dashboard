from ads_funnel.api import market_monitor, market_monitor_collector, market_monitor_db
from ads_funnel.api.market_monitoring import collector, repository, router


def test_compatibility_modules_export_package_objects():
    assert market_monitor.router is router.router
    assert market_monitor_db.list_keywords is repository.list_keywords
    assert market_monitor_db.get_market_engine is repository.get_market_engine
    assert (
        market_monitor_collector.persist_daily_source_batch
        is collector.persist_daily_source_batch
    )
