"""Task 1 verification — python ads_funnel/api/_test_task1.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
from ads_funnel.api.targeting_analysis import _parse_adj_pct, get_action

TARGET = 0.30

def make_row(acos, cpc=1.0):
    return pd.Series({
        "label": "✅ 低ACoS出单", "acos": acos, "cpc": cpc,
        "clicks": 10, "sales_share": 0.0,
        "Campaign Name": "C", "Ad Group Name": "AG",
        "Targeting": "T", "Match Type": "exact",
    })

# _parse_adj_pct
assert _parse_adj_pct('+5%')   ==  0.05
assert _parse_adj_pct('+10%')  ==  0.10
assert _parse_adj_pct('+15%')  ==  0.15
assert _parse_adj_pct('+0%')   is None
assert _parse_adj_pct('+0~5%') is None
assert _parse_adj_pct('-10%')  == -0.10
assert _parse_adj_pct('0%')    is None
assert _parse_adj_pct(None)    is None
print("✅ _parse_adj_pct OK")

# get_action headroom tiers
_, p, *_ = get_action(make_row(0.255), TARGET, 0.5, set())
assert p == "+5%",  f"got {p}"   # headroom=15%
_, p, *_ = get_action(make_row(0.195), TARGET, 0.5, set())
assert p == "+10%", f"got {p}"  # headroom=35%
_, p, *_ = get_action(make_row(0.075), TARGET, 0.5, set())
assert p == "+15%", f"got {p}"  # headroom=75%
_, p, *_ = get_action(make_row(0.28),  TARGET, 0.5, set())
assert p == "+0%",  f"got {p}"  # headroom=6.7%
_, p, *_ = get_action(make_row(None),  TARGET, 0.5, set())
assert p == "+0%",  f"got {p}"  # acos=None
_, p, *_ = get_action(make_row(0.35),  TARGET, 0.5, set())
assert p == "+0%",  f"got {p}"  # acos > target → clamped
print("✅ get_action headroom tiers OK")

print("\n🎉 Task 1 passed!")
