"""CM3 Engine — matches Shopify orders to Meta campaigns, produces 6 report tables."""
import re
import os
import bz2
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from config import (
    ACCOUNTS, SUFFIX_TO_CM3, CM3_TO_SHORT, NAME_TO_CM3, NAME_TO_SHORT,
    STATE_GROUPS, REDIR_STATES, NCR_UP_DISTRICTS,
    STATE_LABELS, STATE_ABBR, STATE_ORDER,
    CAMPAIGN_STATE_PATTERNS,
)


def _load_pincode_db():
    """Load pincode geocoding database."""
    pins_path = os.path.join(os.path.dirname(__file__), 'pins.json.bz2')
    rows = [json.loads(l) for l in bz2.open(pins_path, 'rt') if l.strip()]
    pg = pd.DataFrame(rows)
    pg['Latitude'] = pd.to_numeric(pg.Latitude, errors='coerce')
    pg['Longitude'] = pd.to_numeric(pg.Longitude, errors='coerce')
    pg = pg.dropna(subset=['Latitude', 'Longitude'])
    pg = pg[(pg.Latitude.between(6, 37)) & (pg.Longitude.between(67, 98))]
    pg['pin'] = pg.Pincode.astype(int).astype(str).str.zfill(6)
    return pg

_PG = None
def get_pincode_db():
    global _PG
    if _PG is None:
        _PG = _load_pincode_db()
    return _PG


def haversine(lat, lon, arr):
    la1, lo1 = math.radians(lat), math.radians(lon)
    la2, lo2 = np.radians(arr[:, 0]), np.radians(arr[:, 1])
    a = np.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(a))


def classify_campaign_state(name):
    t = name.lower()
    for pat, st in CAMPAIGN_STATE_PATTERNS:
        if re.search(pat, t):
            return st
    return 'NCR'


def build_campaign_dict(spend_rows):
    """Build campaign dictionary from spend data.
    Returns list of campaign tuples: (cid, acc, state, kind, spend, 'Active', name)
    """
    campaigns = {}
    for r in spend_rows:
        cid = r['campaign_id']
        acc_name = r['account']
        cm3_acc = NAME_TO_CM3.get(acc_name, acc_name)
        state = classify_campaign_state(r['campaign_name'])
        is_milk = bool(re.search(r'milk tea|black', r['adset_name'].lower()))
        kind = 'milk' if is_milk else 'core'

        key = (cid, kind)
        if key not in campaigns:
            campaigns[key] = {
                'cid': cid, 'acc': cm3_acc, 'state': state,
                'kind': kind, 'spend': 0.0, 'name': r['campaign_name'],
            }
        campaigns[key]['spend'] += r['spend']

    result = []
    for c in campaigns.values():
        result.append((c['cid'], c['acc'], c['state'], c['kind'], c['spend'], 'Active', c['name']))

    return result


def process_orders(orders_data, campaign_list, date_str):
    """Process Shopify orders and match to campaigns.
    Returns DataFrame of matched orders.
    """
    if not orders_data:
        return pd.DataFrame()

    df = pd.DataFrame(orders_data)
    n_raw = len(df)

    df = df.drop_duplicates('Name').copy()
    df['ts'] = pd.to_datetime(df['Created at'], utc=True).dt.tz_convert('Asia/Kolkata')
    df['date'] = df['ts'].dt.date
    df['hour'] = df['ts'].dt.hour

    target_date = pd.Timestamp(date_str).date()
    df = df[df['date'] == target_date].copy()

    if len(df) == 0:
        return pd.DataFrame()

    # Filter test orders
    def is_test(r):
        t = r['Total']
        sn = str(r['Shipping Name']).lower()
        if r['Source'] == 'shopify_draft_order':
            return True
        if any(k in sn for k in ('test', 'vansh shah', 'mohit patel')):
            return True
        if t in (30, 60, 90, 120, 150, 98, 128, 158, 188, 218):
            return True
        if t <= 300 and t % 30 == 0:
            return True
        if 68 <= t <= 368 and (t - 68) % 30 == 0:
            return True
        if r['Lineitem price'] in (30.0, 98.0):
            return True
        return False

    df = df[~df.apply(is_test, axis=1)].copy()

    # Classify products
    ln = df['Lineitem name'].str.lower()
    df['is_black'] = ln.str.contains('black leaf tea', na=False)
    df['is_core'] = ln.str.contains('arjuna', na=False) & ~ln.str.contains('milk tea|black tea|black leaf tea', na=False)
    df = df[df.is_black | df.is_core].copy()

    if len(df) == 0:
        return pd.DataFrame()

    # Calculate units
    def calc_units(r):
        n = r['Lineitem name'].lower()
        q = r['Lineitem quantity']
        return (3 if 'pack of 3' in n else 2 if 'pack of 2' in n else 1) * q
    df['units'] = df.apply(calc_units, axis=1)
    df['kind'] = np.where(df.is_black, 'milk', 'core')

    # Build CID lookup
    CID = {r[0]: r for r in campaign_list}
    MILK_CIDS = {r[0] for r in campaign_list if r[3] == 'milk'}

    # UTM matching
    rx = re.compile(r'(?:utm_campaign|campaign_id|utm_id)[:=]\s*(\d+)')
    df['cid'] = df['Note Attributes'].apply(lambda s: (rx.search(str(s)).group(1) if rx.search(str(s)) else None))
    df['cid_known'] = df.cid.isin(CID)
    df['acc'] = df.cid.map(lambda c: CID[c][1] if c in CID else None)
    df['state'] = df.cid.map(lambda c: CID[c][2] if c in CID else None)
    df['src'] = np.where(df.cid.isna(), 'non-utm', np.where(df.cid_known, 'utm', 'unknown-cid'))

    # Black Leaf orders with core CID → treat as non-UTM
    m = (df.kind == 'milk') & (df.cid_known) & (~df.cid.isin(MILK_CIDS))
    df.loc[m, ['acc', 'state']] = None
    df.loc[m, 'src'] = 'non-utm'

    # Pincode-based allocation for non-UTM orders
    pg = get_pincode_db()
    pin_ll = pg.groupby('pin')[['Latitude', 'Longitude']].mean()
    pre3 = pg.assign(p3=pg.pin.str[:3]).groupby('p3')[['Latitude', 'Longitude']].mean()
    pin_meta = pg.drop_duplicates('pin').set_index('pin')[['State', 'District']]

    STATE2GROUP = {st: g for g, sts in STATE_GROUPS.items() for st in sts}
    for st, g in REDIR_STATES.items():
        STATE2GROUP[st] = g

    # Build coverage arrays
    dist = pg.groupby(['State', 'District'])[['Latitude', 'Longitude']].median().reset_index()
    stmed = dist.groupby('State')[['Latitude', 'Longitude']].median()

    def _far(r):
        if r.State not in stmed.index:
            return 999
        m = stmed.loc[r.State]
        return haversine(m.Latitude, m.Longitude, np.array([[r.Latitude, r.Longitude]]))[0]

    dist['far'] = dist.apply(_far, axis=1)
    dist = dist[dist.far < 700]
    dist['pgroup'] = dist.State.map(STATE2GROUP)

    _ncr_up = dist[(dist.State == 'UTTAR PRADESH') & dist.District.str.upper().isin(NCR_UP_DISTRICTS)]
    dist.loc[_ncr_up.index, 'pgroup'] = 'NCR'

    cover = {}
    for g in ['MH', 'NCR', 'UP', 'BR', 'WB', 'KA', 'TN', 'KL', 'AP']:
        vals = dist[dist.pgroup == g][['Latitude', 'Longitude']].to_numpy()
        if len(vals) > 0:
            cover[g] = vals

    tel_d = dist[dist.State == 'TELANGANA']
    if len(tel_d) > 0:
        cover['TS'] = tel_d[['Latitude', 'Longitude']].to_numpy()

    hyd_pins = pg[(pg.State == 'TELANGANA') & pg.pin.str.startswith('500')]
    if len(hyd_pins) > 0:
        hyd_d = hyd_pins.groupby('District')[['Latitude', 'Longitude']].median()
        cover['HYD'] = hyd_d[['Latitude', 'Longitude']].to_numpy()

    dist_ll = dist.set_index(['State', 'District'])[['Latitude', 'Longitude']]

    # Active states per kind
    camp_df = pd.DataFrame(campaign_list, columns=['cid', 'acc', 'state', 'kind', 'spend', 'deliv', 'name'])

    def pool_for(kind, state):
        c = camp_df[(camp_df.kind == kind) & (camp_df.state == state) & (camp_df.spend > 0)]
        ok = {'Acc 1', 'Acc 2', 'Acc 3', 'Acc 4', 'FA3', 'FA4', 'FA5'}
        c = c[c.acc.isin(ok)]
        c = c[c.state != 'APTS']
        out = c.groupby('acc').spend.sum()
        if kind == 'milk' and out.empty:
            c2 = camp_df[(camp_df.kind == kind) & (camp_df.state == state) & (camp_df.spend > 0)]
            out = c2.groupby('acc').spend.sum()
        return out

    def active_states(kind):
        out = []
        for s in ['MH', 'NCR', 'UP', 'WB', 'HYD', 'AP', 'TS', 'KA', 'TN', 'KL', 'BR', 'JH']:
            if len(pool_for(kind, s)) > 0:
                out.append(s)
        return out

    ACT = {k: active_states(k) for k in ('core', 'milk')}

    def own_group(pin):
        if pin not in pin_meta.index:
            return None
        st = pin_meta.loc[pin, 'State']
        g = STATE2GROUP.get(st)
        if g == 'TELANGANA':
            return 'HYD' if pin.startswith('500') else 'TS'
        if g == 'UP' and str(pin_meta.loc[pin, 'District']).upper() in NCR_UP_DISTRICTS:
            return 'NCR'
        return g

    def locate(r):
        pin = re.sub(r'\D', '', str(r['Shipping Zip']))[:6]
        if pin in pin_meta.index:
            k = (pin_meta.loc[pin, 'State'], pin_meta.loc[pin, 'District'])
            if k in dist_ll.index:
                return dist_ll.loc[k].tolist(), pin
        if pin[:3] in pre3.index:
            return pre3.loc[pin[:3]].tolist(), pin
        return None, pin

    # Allocate non-UTM orders
    assigned = {}
    for i, r in df[df.src.isin(['non-utm', 'unknown-cid'])].iterrows():
        ll, pin = locate(r)
        og = own_group(pin)
        kind = r['kind']

        if og and og in ACT.get(kind, []):
            best = og
        else:
            if ll is None:
                continue
            best, bd = None, 1e18
            for s_ in ACT.get(kind, []):
                if s_ not in cover:
                    continue
                d_ = haversine(ll[0], ll[1], cover[s_]).min()
                if d_ < bd:
                    best, bd = s_, d_

        if best is None:
            continue

        df.at[i, 'state'] = best

        if r['src'] == 'unknown-cid':
            cid_val = str(r['cid'])
            df.at[i, 'acc'] = SUFFIX_TO_CM3.get(cid_val[-3:])
            continue

        pool = pool_for(kind, best)
        if pool.empty:
            continue
        w = pool / pool.sum()
        key = (kind, best)
        cnt = assigned.setdefault(key, {a: 0 for a in w.index})
        n = sum(cnt.values()) + 1
        acc = max(w.index, key=lambda a: (w[a] * n - cnt[a], w[a]))
        cnt[acc] += 1
        df.at[i, 'acc'] = acc

    final = df[df.acc.notna()].copy()
    return final


def build_campaign_dict_hourly(hourly_spend_rows):
    """Build campaign dict per hour from hourly spend data.
    Returns dict: hour -> list of campaign tuples.
    """
    by_hour = defaultdict(list)
    for r in hourly_spend_rows:
        by_hour[r['hour']].append(r)
    return {h: build_campaign_dict(rows) for h, rows in by_hour.items()}


def build_delta_tables(today_df, hourly_spend_rows, campaign_list, today_str):
    """Build delta tables for each hourly slot.
    Returns list of slot dicts, each containing 5 tables.
    """
    if len(today_df) == 0:
        return []

    last_hour = today_df.hour.max()
    camp_df = pd.DataFrame(campaign_list, columns=['cid', 'acc', 'state', 'kind', 'spend', 'deliv', 'name'])

    SEQ_STATES = ['MH', 'NCR', 'UP', 'WB', 'KA', 'TN', 'KL', 'BR', 'JH', 'APTS', 'RJ']
    ACC_ORDER = ['Acc 1', 'Acc 2', 'Acc 3', 'Acc 4', 'Flank MA 4', 'Flank RS NPD', 'FA3', 'FA4', 'FA5']
    ACCS_SHORT = {'MA1': 'Acc 1', 'MA2': 'Acc 2', 'MA3': 'Acc 3', 'MA4': 'Acc 4', 'FA1': 'Flank MA 4', 'FA2': 'Flank RS NPD', 'FA3': 'FA3', 'FA4': 'FA4', 'FA5': 'FA5'}

    hourly_spend = defaultdict(lambda: defaultdict(float))
    for r in hourly_spend_rows:
        cm3_acc = NAME_TO_CM3.get(r['account'], r['account'])
        state = classify_campaign_state(r['campaign_name'])
        is_milk = bool(re.search(r'milk tea|black', r['adset_name'].lower()))
        kind = 'milk' if is_milk else 'core'
        hourly_spend[r['hour']][(cm3_acc, state, kind)] += r['spend']

    slots = []
    for h in range(0, last_hour + 1):
        h_orders = today_df[today_df.hour == h]
        h_spend = hourly_spend.get(h, {})
        if len(h_orders) == 0 and not h_spend:
            continue

        a = h % 12 or 12
        b = (h + 1) % 12 or 12
        ap = 'AM' if h < 12 else 'PM'
        bp = 'AM' if (h + 1) % 24 < 12 else 'PM'
        slot_label = f"{a}–{b} {ap}" if ap == bp else f"{a} {ap}–{b} {bp}"

        def _matrix(kind):
            od = h_orders[h_orders.kind == kind] if len(h_orders) > 0 else pd.DataFrame()
            rows = []
            tot = {'o': 0, 'u': 0, 'g': 0.0, 's': 0.0}
            for acc in ACC_ORDER:
                states = list(SEQ_STATES)
                if len(od) > 0:
                    have = set(od[od.acc == acc].state.dropna())
                else:
                    have = set()
                have |= {s for (a_, s, k), v in h_spend.items() if a_ == acc and k == kind and v > 0}
                states += [s for s in sorted(have) if s not in states]

                for st in states:
                    o = od[(od.acc == acc) & (od.state == st)] if len(od) > 0 else pd.DataFrame()
                    spend = float(h_spend.get((acc, st, kind), 0.0))
                    if len(o) == 0 and spend <= 0:
                        continue
                    n_orders = len(o)
                    units = int(o.units.sum()) if len(o) > 0 else 0
                    gross = float(o.Total.sum()) if len(o) > 0 else 0
                    gst_spend = spend * 1.18
                    cpt = round(gst_spend / units) if units > 0 else 0

                    rows.append({
                        'segment': STATE_LABELS.get(st, st),
                        'orders': n_orders, 'units': units, 'cpt': cpt,
                        'gross': round(gross), 'raw_spend': round(spend),
                        'account': CM3_TO_SHORT.get(acc, acc),
                    })
                    tot['o'] += n_orders
                    tot['u'] += units
                    tot['g'] += gross
                    tot['s'] += spend
            tot['cpt'] = round(tot['s'] * 1.18 / tot['u']) if tot['u'] > 0 else 0
            return rows, tot

        core_rows, core_tot = _matrix('core')
        milk_rows, milk_tot = _matrix('milk')

        # State summary from core
        state_data = defaultdict(lambda: [0, 0, 0.0, 0.0])
        for r in core_rows:
            abbr = STATE_ABBR.get(r['segment'], r['segment'])
            d = state_data[abbr]
            d[0] += r['orders']; d[1] += r['units']; d[2] += r['gross']; d[3] += r['raw_spend']
        state_rows = []
        ranked = sorted(
            [(st, state_data[st]) for st in STATE_ORDER if st in state_data and (state_data[st][0] > 0 or state_data[st][3] > 0)],
            key=lambda x: -x[1][1]
        )
        for rank, (st, d) in enumerate(ranked, 1):
            cpt = round(d[3] * 1.18 / d[1]) if d[1] > 0 else 0
            state_rows.append({'rank': rank, 'state': st, 'orders': d[0], 'units': d[1], 'cpt': cpt, 'gross': round(d[2]), 'raw_spend': round(d[3])})
        state_tot = {
            'orders': sum(r['orders'] for r in state_rows), 'units': sum(r['units'] for r in state_rows),
            'gross': sum(r['gross'] for r in state_rows), 'raw_spend': sum(r['raw_spend'] for r in state_rows),
        }
        state_tot['cpt'] = round(state_tot['raw_spend'] * 1.18 / state_tot['units']) if state_tot['units'] > 0 else 0

        # Account summary from core
        acc_data = defaultdict(lambda: [0, 0, 0.0, 0.0])
        for r in core_rows:
            d = acc_data[r['account']]
            d[0] += r['orders']; d[1] += r['units']; d[2] += r['gross']; d[3] += r['raw_spend']
        acc_rows = []
        for an in ['MA1', 'MA2', 'MA3', 'MA4', 'FA1', 'FA2', 'FA3', 'FA4', 'FA5']:
            if an in acc_data:
                d = acc_data[an]
                cpt = round(d[3] * 1.18 / d[1]) if d[1] > 0 else 0
                acc_rows.append({'account': an, 'orders': d[0], 'units': d[1], 'cpt': cpt, 'gross': round(d[2]), 'raw_spend': round(d[3])})
        acc_tot = {
            'orders': sum(r['orders'] for r in acc_rows), 'units': sum(r['units'] for r in acc_rows),
            'gross': sum(r['gross'] for r in acc_rows), 'raw_spend': sum(r['raw_spend'] for r in acc_rows),
        }
        acc_tot['cpt'] = round(acc_tot['raw_spend'] * 1.18 / acc_tot['units']) if acc_tot['units'] > 0 else 0

        # Account growth (this hour's units per account)
        tc = h_orders[h_orders.kind == 'core'] if len(h_orders) > 0 else pd.DataFrame(columns=['units', 'acc'])
        growth_rows = []
        for name, acode in list(ACCS_SHORT.items()) + [('Overall', None)]:
            if len(tc) > 0:
                u = int(tc[tc.acc == acode].units.sum()) if acode else int(tc.units.sum())
            else:
                u = 0
            growth_rows.append({'account': name, 'units': u})

        slots.append({
            'slot': slot_label,
            'hour': h,
            'table1': {'rows': core_rows, 'totals': core_tot, 'title': f'Arjuna Tea (Core)'},
            'table2': {'rows': milk_rows, 'totals': milk_tot, 'title': f'Black Leaf Tea'},
            'table3': {'rows': state_rows, 'totals': state_tot},
            'table4': {'rows': acc_rows, 'totals': acc_tot},
            'table5': {'rows': growth_rows},
        })

    return slots


def build_tables(today_df, yest_df, spend_rows, campaign_list, today_str, yest_str):
    """Build all 6 CM3 report tables. Returns dict with table data."""
    camp_df = pd.DataFrame(campaign_list, columns=['cid', 'acc', 'state', 'kind', 'spend', 'deliv', 'name'])

    # Spend by account
    spend_by_acc = defaultdict(float)
    for r in spend_rows:
        spend_by_acc[r['account']] += r['spend']
    total_spend = sum(spend_by_acc.values())

    # Last order time
    if len(today_df) > 0:
        last_ts = today_df.ts.max()
        last_hour = last_ts.hour
        last_time = last_ts.time()
        last_str = last_ts.strftime('%-I:%M %p')
    else:
        last_hour = 0
        last_time = None
        last_str = 'N/A'

    # Campaign spend by (acc, state, kind)
    camp_spend = camp_df.groupby(['acc', 'state', 'kind']).spend.sum()

    SEQ_STATES = ['MH', 'NCR', 'UP', 'WB', 'KA', 'TN', 'KL', 'BR', 'JH', 'APTS', 'RJ']
    ACC_ORDER = ['Acc 1', 'Acc 2', 'Acc 3', 'Acc 4', 'Flank MA 4', 'Flank RS NPD', 'FA3', 'FA4', 'FA5']

    def build_matrix(kind):
        rows = []
        tot = {'o': 0, 'u': 0, 'g': 0.0, 's': 0.0}
        od = today_df[today_df.kind == kind] if len(today_df) > 0 else pd.DataFrame()

        for acc in ACC_ORDER:
            states = list(SEQ_STATES)
            if len(od) > 0:
                have = set(od[od.acc == acc].state.dropna())
            else:
                have = set()
            have |= {s for (a, s, k), v in camp_spend.items() if a == acc and k == kind and v > 0}
            states += [s for s in sorted(have) if s not in states]

            for st in states:
                o = od[(od.acc == acc) & (od.state == st)] if len(od) > 0 else pd.DataFrame()
                spend = float(camp_spend.get((acc, st, kind), 0.0))
                if len(o) == 0 and spend <= 0:
                    continue
                n_orders = len(o)
                units = int(o.units.sum()) if len(o) > 0 else 0
                gross = float(o.Total.sum()) if len(o) > 0 else 0
                gst_spend = spend * 1.18
                cpt = round(gst_spend / units) if units > 0 else 0

                rows.append({
                    'segment': STATE_LABELS.get(st, st),
                    'orders': n_orders,
                    'units': units,
                    'cpt': cpt,
                    'gross': round(gross),
                    'raw_spend': round(spend),
                    'account': CM3_TO_SHORT.get(acc, acc),
                })
                tot['o'] += n_orders
                tot['u'] += units
                tot['g'] += gross
                tot['s'] += spend

        tot['cpt'] = round(tot['s'] * 1.18 / tot['u']) if tot['u'] > 0 else 0
        return rows, tot

    core_rows, core_tot = build_matrix('core')
    milk_rows, milk_tot = build_matrix('milk')

    # Table 3: State Summary (Core)
    state_data = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for r in core_rows:
        abbr = STATE_ABBR.get(r['segment'], r['segment'])
        d = state_data[abbr]
        d[0] += r['orders']
        d[1] += r['units']
        d[2] += r['gross']
        d[3] += r['raw_spend']

    state_rows = []
    ranked = sorted(
        [(st, state_data[st]) for st in STATE_ORDER if st in state_data and (state_data[st][0] > 0 or state_data[st][3] > 0)],
        key=lambda x: -x[1][1]
    )
    for rank, (st, d) in enumerate(ranked, 1):
        cpt = round(d[3] * 1.18 / d[1]) if d[1] > 0 else 0
        state_rows.append({
            'rank': rank, 'state': st,
            'orders': d[0], 'units': d[1], 'cpt': cpt,
            'gross': round(d[2]), 'raw_spend': round(d[3]),
        })
    state_tot = {
        'orders': sum(d['orders'] for d in state_rows),
        'units': sum(d['units'] for d in state_rows),
        'gross': sum(d['gross'] for d in state_rows),
        'raw_spend': sum(d['raw_spend'] for d in state_rows),
    }
    state_tot['cpt'] = round(state_tot['raw_spend'] * 1.18 / state_tot['units']) if state_tot['units'] > 0 else 0

    # Table 4: Account Summary (Core)
    acc_data = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for r in core_rows:
        d = acc_data[r['account']]
        d[0] += r['orders']
        d[1] += r['units']
        d[2] += r['gross']
        d[3] += r['raw_spend']

    acc_rows = []
    for an in ['MA1', 'MA2', 'MA3', 'MA4', 'FA1', 'FA2', 'FA3', 'FA4', 'FA5']:
        if an in acc_data:
            d = acc_data[an]
            cpt = round(d[3] * 1.18 / d[1]) if d[1] > 0 else 0
            acc_rows.append({
                'account': an, 'orders': d[0], 'units': d[1],
                'cpt': cpt, 'gross': round(d[2]), 'raw_spend': round(d[3]),
            })
    acc_tot = {
        'orders': sum(d['orders'] for d in acc_rows),
        'units': sum(d['units'] for d in acc_rows),
        'gross': sum(d['gross'] for d in acc_rows),
        'raw_spend': sum(d['raw_spend'] for d in acc_rows),
    }
    acc_tot['cpt'] = round(acc_tot['raw_spend'] * 1.18 / acc_tot['units']) if acc_tot['units'] > 0 else 0

    # Table 5: Account-wise Growth
    ACCS_MAP = {'MA1': 'Acc 1', 'MA2': 'Acc 2', 'MA3': 'Acc 3', 'MA4': 'Acc 4', 'FA1': 'Flank MA 4', 'FA2': 'Flank RS NPD', 'FA3': 'FA3', 'FA4': 'FA4', 'FA5': 'FA5'}

    def acc_units(d, acc_code):
        if len(d) == 0:
            return 0
        if acc_code is None:
            return int(d.units.sum())
        return int(d[d.acc == acc_code].units.sum())

    tc = today_df[today_df.kind == 'core'] if len(today_df) > 0 else pd.DataFrame(columns=['units', 'acc', 'hour', 'ts', 'kind'])
    if len(yest_df) > 0:
        sc = yest_df[yest_df.kind == 'core'].copy()
        if last_time is not None:
            sc = sc[(sc.hour < last_hour) | ((sc.hour == last_hour) & (sc.ts.dt.time <= last_time))]
    else:
        sc = pd.DataFrame(columns=['units', 'acc', 'hour', 'ts', 'kind'])

    growth_rows = []
    for name, acode in list(ACCS_MAP.items()) + [('Overall', None)]:
        u_yest = acc_units(sc, acode)
        u_today = acc_units(tc, acode)
        if u_yest > 0:
            pct = f"{(u_today - u_yest) / u_yest * 100:+.1f}%"
        else:
            pct = f"0 → {u_today}" if u_today else "0.0%"
        growth_rows.append({
            'account': name,
            'units_yest': u_yest,
            'units_today': u_today,
            'growth': pct,
        })

    # Table 6: Hour-by-Hour
    def hour_label(h):
        a = h % 12 or 12
        b = (h + 1) % 12 or 12
        ap = 'AM' if h < 12 else 'PM'
        bp = 'AM' if h + 1 < 12 else 'PM'
        if h + 1 == 24:
            bp = 'AM'
        if ap == bp:
            return f"{a}–{b} {ap}"
        return f"{a} {ap}–{b} {bp}"

    def units_by_hour(d, h):
        if len(d) == 0:
            return 0
        return int(d[d.hour == h].units.sum())

    def orders_by_hour(d, h):
        if len(d) == 0:
            return 0
        return d[d.hour == h].Name.nunique()

    def trend(a, b):
        return 'Up' if b > a else 'Down' if b < a else 'Even'

    def pct_change(a, b):
        if a > 0:
            return f"{(b - a) / a * 100:+.1f}%"
        return f"0 → {b}" if b else "0.0%"

    hourly_rows = []
    tot_us = tot_ut = tot_os = tot_ot = 0
    for h in range(0, last_hour + 1):
        lbl = hour_label(h)
        if h == last_hour:
            lbl += f" (till {last_str})"
        us = units_by_hour(sc, h)
        ut = units_by_hour(tc, h)
        os_ = orders_by_hour(sc, h)
        ot = orders_by_hour(tc, h)
        tot_us += us
        tot_ut += ut
        tot_os += os_
        tot_ot += ot
        hourly_rows.append({
            'slot': lbl,
            'units_yest': us, 'units_today': ut,
            'units_pct': pct_change(us, ut), 'units_trend': trend(us, ut),
            'orders_yest': os_, 'orders_today': ot,
            'orders_pct': pct_change(os_, ot), 'orders_trend': trend(os_, ot),
        })

    hourly_tot = {
        'units_yest': tot_us, 'units_today': tot_ut,
        'units_pct': pct_change(tot_us, tot_ut), 'units_trend': trend(tot_us, tot_ut),
        'orders_yest': tot_os, 'orders_today': tot_ot,
        'orders_pct': pct_change(tot_os, tot_ot), 'orders_trend': trend(tot_os, tot_ot),
    }

    today_dt = pd.Timestamp(today_str)
    yest_dt = pd.Timestamp(yest_str)

    return {
        'header': {
            'date': today_dt.strftime('%-d %b'),
            'till': last_str,
            'spend_total': round(total_spend),
            'spend_parts': {NAME_TO_SHORT.get(a, a): round(v) for a, v in spend_by_acc.items()},
        },
        'today_str': today_str,
        'yest_str': yest_str,
        'today_label': today_dt.strftime('%-d %b'),
        'yest_label': yest_dt.strftime('%-d %b'),
        'table1': {'rows': core_rows, 'totals': core_tot, 'title': 'Arjuna Tea (Core)'},
        'table2': {'rows': milk_rows, 'totals': milk_tot, 'title': 'Black Leaf Tea'},
        'table3': {'rows': state_rows, 'totals': state_tot},
        'table4': {'rows': acc_rows, 'totals': acc_tot},
        'table5': {'rows': growth_rows},
        'table6': {'rows': hourly_rows, 'totals': hourly_tot},
    }
