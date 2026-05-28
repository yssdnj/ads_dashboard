"""Task 2 verification — python ads_funnel/api/_test_task2.py"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pandas as pd
from unittest.mock import patch
from ads_funnel.api.targeting_analysis import run_multi_round_analysis_6r

TARGET = 0.30

def _make_row(targeting, label, acos, orders, adj_pct, action, cpc=1.0):
    return {
        'campaign': 'C', 'ad_group': 'AG', 'targeting': targeting,
        'label': label, 'acos': acos, 'orders': orders, 'cpc': cpc,
        'clicks': 50, 'spend': 10.0, 'sales': orders * 20.0, 'sales_share': 0.01,
        'adj_pct': adj_pct, 'action': action, 'reason': '',
        'Campaign Name': 'C', 'Ad Group Name': 'AG', 'Match Type': 'exact',
    }

def _run(rows_per_round, up_orders_threshold=2):
    sundays = pd.date_range('2026-01-05', periods=6, freq='7D').tolist()
    tar_df = pd.DataFrame({'col': [1]})
    ap_df  = pd.DataFrame({'col': [1]})
    rnames_order = ['R1', 'R2', 'R3', 'R4', 'R5', 'R6']
    call_idx = [0]

    def _mock(**kwargs):
        rname = rnames_order[min(call_idx[0], len(rnames_order)-1)]
        call_idx[0] += 1
        return {'rows': rows_per_round.get(rname, []), 'summary': {}, 'layers': {}, 'label_counts': {}, 'error': None}

    import ads_funnel.api.targeting_analysis as ta
    with patch.object(ta, 'run_analysis', side_effect=_mock), \
         patch.object(ta, '_filter_by_week_range', return_value=(tar_df, ap_df)), \
         patch.object(ta, 'get_asins_for_product', return_value=['B001']):
        return run_multi_round_analysis_6r(
            tar_df=tar_df, ap_df=ap_df, week_sundays=sundays,
            product_target='TEST', asins=['B001'],
            target_acos=TARGET, avg_clicks_per_order=10,
            up_orders_threshold=up_orders_threshold,
        )

low_row = _make_row('kw-alpha', '✅ 低ACoS出单', acos=TARGET*0.84, orders=5, adj_pct='+5%', action='↗ 提价')

# Test 1: 6/6 rounds → appears in consensus_up
result = _run({r: [dict(low_row)] for r in ['R1','R2','R3','R4','R5','R6']})
assert 'consensus_up' in result, "❌ consensus_up missing"
cup = result['consensus_up']
assert cup['count'] >= 1, f"❌ expected ≥1, got {cup['count']}"
row = cup['rows'][0]
assert 'R3' in row['hit_rounds'], f"❌ R3 not in hit_rounds: {row['hit_rounds']}"
assert row['adj_pct'] == '+5%', f"❌ adj_pct={row['adj_pct']}"
print(f"✅ Test 1: 6/6 rounds → consensus_up count={cup['count']}, hit_rounds={row['hit_rounds']}")

# Test 2: only 2 rounds (R1,R2), no R3 → excluded
result2 = _run({'R1': [dict(low_row)], 'R2': [dict(low_row)], 'R3': [], 'R4': [], 'R5': [], 'R6': []})
assert result2['consensus_up']['count'] == 0, f"❌ expected 0, got {result2['consensus_up']['count']}"
print("✅ Test 2: 2 rounds no R3 → excluded")

# Test 3: 3 rounds (R1,R2,R4) but no R3 → excluded
result3 = _run({'R1': [dict(low_row)], 'R2': [dict(low_row)], 'R3': [], 'R4': [dict(low_row)], 'R5': [], 'R6': []})
assert result3['consensus_up']['count'] == 0, f"❌ expected 0, got {result3['consensus_up']['count']}"
print("✅ Test 3: 3 rounds but no R3 → excluded")

# Test 4: orders < threshold → excluded
low_orders = dict(low_row); low_orders['orders'] = 1
result4 = _run({r: [dict(low_orders)] for r in ['R1','R2','R3','R4','R5','R6']}, up_orders_threshold=2)
assert result4['consensus_up']['count'] == 0, f"❌ expected 0, got {result4['consensus_up']['count']}"
print("✅ Test 4: orders < threshold → excluded")

# Test 5: most conservative adj_pct
row_5  = dict(low_row); row_5['adj_pct']  = '+5%'
row_10 = dict(low_row); row_10['adj_pct'] = '+10%'
row_15 = dict(low_row); row_15['adj_pct'] = '+15%'
result5 = _run({'R1': [row_5], 'R2': [row_10], 'R3': [row_15], 'R4': [row_10], 'R5': [], 'R6': []})
assert result5['consensus_up']['count'] >= 1
assert result5['consensus_up']['rows'][0]['adj_pct'] == '+5%', f"❌ expected +5%, got {result5['consensus_up']['rows'][0]['adj_pct']}"
print("✅ Test 5: most conservative adj_pct = +5%")

# Test 6: 降价 consensus unaffected
high_row = _make_row('kw-beta', '⚠️ 高ACoS出单', acos=TARGET*1.4, orders=5, adj_pct='-10%', action='↘ 降价')
result6 = _run({r: [dict(high_row)] for r in ['R1','R2','R3','R4','R5','R6']})
assert result6['consensus']['count'] >= 1,    f"❌ 降价 consensus broken"
assert result6['consensus_up']['count'] == 0, f"❌ 高ACoS should not be in consensus_up"
print(f"✅ Test 6: 降价 consensus={result6['consensus']['count']}, consensus_up=0")

print("\n🎉 Task 2 passed!")
