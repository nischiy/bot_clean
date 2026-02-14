#!/usr/bin/env python3
"""One-off parse of decision_candle/decision_clean lines for STRATEGY_TRADABILITY_DIAGNOSTIC_REPORT.
Usage: py -3 parse_decision_candles.py [logs/sessions logs/sessions_clean logs]
Outputs JSON summary to stdout. No code changes to app."""
import json
import os
import sys
from collections import defaultdict

def main():
    log_dirs = sys.argv[1:] if len(sys.argv) > 1 else ['logs/sessions', 'logs/sessions_clean', 'logs']
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not os.path.isdir(base):
        base = os.getcwd()
    files_used = []
    rows = []
    for d in log_dirs:
        path_dir = os.path.join(base, d) if not os.path.isabs(d) else d
        if not os.path.isdir(path_dir):
            continue
        for f in sorted(os.listdir(path_dir)):
            if not f.endswith('.log'):
                continue
            path = os.path.join(path_dir, f)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fp:
                    for line in fp:
                        if '"event":"decision_candle"' not in line:
                            continue
                        idx = line.find('INFO TraderApp: ')
                        if idx == -1:
                            continue
                        try:
                            obj = json.loads(line[idx + 15:].strip())
                            obj['_file'] = path
                            rows.append(obj)
                            if path not in files_used:
                                files_used.append(path)
                        except Exception:
                            pass
            except Exception:
                pass

    n = len(rows)
    out = {
        'total_decision_candle_records': n,
        'files_used': files_used,
        'regime_detected': {},
        'regime_used_for_routing': {},
        'selected_strategy': {},
        'eligible_strategies_empty_count': 0,
        'eligible_strategies_empty_pct': 0.0,
        'stable_block_true_count': 0,
        'stable_block_true_pct': 0.0,
        'vol_ok_false_count': 0,
        'vol_ok_false_pct': 0.0,
        'vol_ok_fail_by_regime': {},
        'top10_reject_reasons': [],
        'top_reject_by_prefix': {'P': [], 'C': [], 'B': [], 'M': [], 'A': [], 'S': [], 'E': []},
        'stability_score': None,
        'volume_ratio_5m': None,
        'dist50_curr': None,
        'atr_ratio': None,
        'wick_ratio': None,
        'bb_width_atr': None,
        'count_intent_long': 0,
        'count_intent_short': 0,
        'pct_selected_not_none': 0.0,
        'pct_has_trade_intent': 0.0,
    }
    if n == 0:
        print(json.dumps(out, indent=2))
        return

    regime_det = defaultdict(int)
    regime_rout = defaultdict(int)
    sel_strat = defaultdict(int)
    eligible_empty = 0
    reject_all = defaultdict(int)
    reject_by_prefix = defaultdict(lambda: defaultdict(int))
    stab_scores, vol_ratios, dist50_curr, atr_ratios, wick_ratios, bb_width_atr = [], [], [], [], [], []
    stable_block_true = vol_ok_fail = 0
    vol_ok_fail_by_regime = defaultdict(int)
    count_intent_long = count_intent_short = 0
    count_selected_not_none = 0
    count_has_trade_intent = 0

    for r in rows:
        regime_det[r.get('regime_detected') or ''] += 1
        regime_rout[r.get('regime_used_for_routing') or ''] += 1
        sel_strat[r.get('selected_strategy') or ''] += 1
        el = r.get('eligible_strategies')
        if el is not None and (not isinstance(el, list) or len(el) == 0):
            eligible_empty += 1
        for code in (r.get('reject_reasons') or []):
            if isinstance(code, str):
                reject_all[code] += 1
                pre = code.split(':', 1)[0] if ':' in code else 'other'
                reject_by_prefix[pre][code] += 1
        s = r.get('stability_score')
        if s is not None and isinstance(s, (int, float)):
            stab_scores.append(float(s))
        v = r.get('volume_ratio_5m')
        if v is not None and isinstance(v, (int, float)):
            vol_ratios.append(float(v))
        ep = r.get('explain_pullback') or {}
        d50 = ep.get('dist50_curr') if isinstance(ep, dict) else None
        if d50 is not None and isinstance(d50, (int, float)):
            dist50_curr.append(float(d50))
        a = r.get('atr_ratio')
        if a is not None and isinstance(a, (int, float)):
            atr_ratios.append(float(a))
        w = r.get('wick_ratio')
        if w is not None and isinstance(w, (int, float)):
            wick_ratios.append(float(w))
        bb = r.get('bb_width_atr')
        if bb is not None and isinstance(bb, (int, float)):
            bb_width_atr.append(float(bb))
        if r.get('stable_block') is True:
            stable_block_true += 1
        ep = r.get('explain_pullback') or {}
        if isinstance(ep, dict) and ep.get('vol_ok') is False:
            vol_ok_fail += 1
            vol_ok_fail_by_regime[r.get('regime_used_for_routing') or ''] += 1
        intent = r.get('intent')
        if intent is None and isinstance(r.get('decision'), dict):
            intent = r.get('decision', {}).get('intent')
        if intent == 'LONG':
            count_intent_long += 1
        elif intent == 'SHORT':
            count_intent_short += 1
        sel = r.get('selected_strategy')
        if sel is not None and str(sel).strip().upper() != 'NONE':
            count_selected_not_none += 1
        if intent in ('LONG', 'SHORT'):
            count_has_trade_intent += 1

    def st(arr):
        if not arr:
            return {'N': 0}
        arr = sorted(arr)
        return {'N': len(arr), 'min': round(arr[0], 4), 'median': round(arr[len(arr)//2], 4), 'p90': round(arr[min(int(len(arr)*0.9), len(arr)-1)], 4)}

    out['regime_detected'] = dict(regime_det)
    out['regime_used_for_routing'] = dict(regime_rout)
    out['selected_strategy'] = dict(sel_strat)
    out['eligible_strategies_empty_count'] = eligible_empty
    out['eligible_strategies_empty_pct'] = round(eligible_empty / n * 100, 2)
    out['stable_block_true_count'] = stable_block_true
    out['stable_block_true_pct'] = round(stable_block_true / n * 100, 1)
    out['vol_ok_false_count'] = vol_ok_fail
    out['vol_ok_false_pct'] = round(vol_ok_fail / n * 100, 1)
    out['vol_ok_fail_by_regime'] = dict(vol_ok_fail_by_regime)
    out['top10_reject_reasons'] = [list(x) for x in sorted(reject_all.items(), key=lambda x: -x[1])[:10]]
    for pre in ['P', 'C', 'B', 'M', 'A', 'S', 'E']:
        if pre in reject_by_prefix:
            out['top_reject_by_prefix'][pre] = [list(x) for x in sorted(reject_by_prefix[pre].items(), key=lambda x: -x[1])[:5]]
    out['stability_score'] = st(stab_scores)
    out['volume_ratio_5m'] = st(vol_ratios)
    out['count_intent_long'] = count_intent_long
    out['count_intent_short'] = count_intent_short
    out['pct_selected_not_none'] = round(count_selected_not_none / n * 100, 2) if n else 0.0
    out['pct_has_trade_intent'] = round(count_has_trade_intent / n * 100, 2) if n else 0.0
    out['dist50_curr'] = st(dist50_curr)
    out['atr_ratio'] = st(atr_ratios)
    out['wick_ratio'] = st(wick_ratios)
    out['bb_width_atr'] = st(bb_width_atr)
    print(json.dumps(out, indent=2))

if __name__ == '__main__':
    main()
