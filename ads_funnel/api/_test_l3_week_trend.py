"""
测试 get_campaign_stats_by_date_range 和 get_campaign_trend。
运行：cd ads_dashboard && $env:PYTHONUTF8=1; python ads_funnel/api/_test_l3_week_trend.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from ads_funnel.api.db import get_campaign_stats_by_date_range, get_campaign_trend

def test_campaign_stats_returns_dict():
    """返回值是 dict，不抛异常。"""
    result = get_campaign_stats_by_date_range('UK', '2026-01-01', '2026-12-31')
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    print(f"  返回 {len(result)} 条广告活动")

def test_campaign_stats_bad_country():
    """未知国家返回空 dict，不抛异常。"""
    result = get_campaign_stats_by_date_range('ZZZZ', '2026-01-01', '2026-12-31')
    assert result == {}, f"Expected {{}}, got {result}"

def test_campaign_stats_keys():
    """有数据时每条记录含必要字段。"""
    result = get_campaign_stats_by_date_range('UK', '2026-01-01', '2026-12-31')
    if not result:
        print("  无数据，跳过字段检查")
        return
    camp = next(iter(result.values()))
    for key in ('sp', 'sl', 'cl', 'im', 'or_'):
        assert key in camp, f"Missing key: {key}"
    print(f"  字段齐全: {list(camp.keys())}")

def test_campaign_trend_returns_structure():
    """返回含 weekly/daily/bid_events 的 dict。"""
    stats = get_campaign_stats_by_date_range('UK', '2026-01-01', '2026-12-31')
    if not stats:
        print("  无数据，跳过 trend 测试")
        return
    camp_name = next(iter(stats.keys()))
    result = get_campaign_trend(camp_name, 'UK', '2026-01-01', '2026-12-31')
    assert 'weekly' in result and 'daily' in result and 'bid_events' in result
    print(f"  weekly {len(result['weekly'])} 周, daily {len(result['daily'])} 天, bid_events {len(result['bid_events'])} 条")

def test_campaign_trend_bad_campaign():
    """未知广告活动返回空列表结构，不抛异常。"""
    result = get_campaign_trend('__NONEXISTENT__', 'UK', '2026-01-01', '2026-12-31')
    assert result['weekly'] == [] and result['daily'] == []

def test_campaign_trend_weekly_derived_from_daily():
    """weekly 数据从 daily 聚合得到，周数不超过 daily 天数。"""
    stats = get_campaign_stats_by_date_range('UK', '2026-01-01', '2026-12-31')
    if not stats:
        return
    camp_name = next(iter(stats.keys()))
    result = get_campaign_trend(camp_name, 'UK', '2026-04-01', '2026-05-31')
    assert len(result['weekly']) <= len(result['daily']), \
        f"weekly ({len(result['weekly'])}) > daily ({len(result['daily'])})"
    print(f"  通过: weekly {len(result['weekly'])} <= daily {len(result['daily'])}")

if __name__ == '__main__':
    tests = [
        test_campaign_stats_returns_dict,
        test_campaign_stats_bad_country,
        test_campaign_stats_keys,
        test_campaign_trend_returns_structure,
        test_campaign_trend_bad_campaign,
        test_campaign_trend_weekly_derived_from_daily,
    ]
    passed = 0
    for t in tests:
        try:
            print(f"[RUN] {t.__name__}")
            t()
            print(f"[OK]  {t.__name__}")
            passed += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
