"""Fetch spend from Meta API and orders from Shopify API."""
import time
import requests
from datetime import datetime, timedelta
from config import ACCOUNTS, META_API_BASE, SHOPIFY_STORE, SHOPIFY_TOKEN


def fetch_meta_spend(date_str):
    """Fetch adset-level spend for a given date from all accounts.
    Returns list of dicts: {account, campaign_name, campaign_id, adset_name, adset_id, spend}
    """
    rows = []
    for acc in ACCOUNTS:
        aid, token = acc['id'], acc['token']
        if not aid or not token:
            continue

        url = (
            f"{META_API_BASE}/act_{aid}/insights"
            f"?access_token={token}"
            f"&level=adset"
            f"&fields=adset_name,adset_id,campaign_name,campaign_id,spend"
            f"&time_range={{\"since\":\"{date_str}\",\"until\":\"{date_str}\"}}"
            f"&time_increment=1"
            f"&limit=500"
        )

        while url:
            try:
                resp = requests.get(url, timeout=60)
                data = resp.json()
            except Exception as e:
                print(f"  Meta spend {acc['name']}: {e}")
                break

            if 'error' in data:
                print(f"  Meta spend {acc['name']} error: {data['error'].get('message', '')}")
                break

            for r in data.get('data', []):
                spend = float(r.get('spend', 0))
                if spend > 0:
                    rows.append({
                        'account': acc['name'],
                        'campaign_name': r.get('campaign_name', ''),
                        'campaign_id': r.get('campaign_id', ''),
                        'adset_name': r.get('adset_name', ''),
                        'adset_id': r.get('adset_id', ''),
                        'spend': spend,
                    })

            url = data.get('paging', {}).get('next')
            if url:
                time.sleep(0.5)

        print(f"  Meta spend: {acc['name']} done")

    return rows


def fetch_meta_spend_hourly(date_str):
    """Fetch adset-level spend broken down by hour for a given date.
    Returns list of dicts with extra 'hour' field (0-23).
    """
    rows = []
    for acc in ACCOUNTS:
        aid, token = acc['id'], acc['token']
        if not aid or not token:
            continue

        url = (
            f"{META_API_BASE}/act_{aid}/insights"
            f"?access_token={token}"
            f"&level=adset"
            f"&fields=adset_name,adset_id,campaign_name,campaign_id,spend"
            f"&time_range={{\"since\":\"{date_str}\",\"until\":\"{date_str}\"}}"
            f"&time_increment=1"
            f"&breakdowns=hourly_stats_aggregated_by_advertiser_time_zone"
            f"&limit=500"
        )

        while url:
            try:
                resp = requests.get(url, timeout=60)
                data = resp.json()
            except Exception as e:
                print(f"  Meta hourly {acc['name']}: {e}")
                break

            if 'error' in data:
                print(f"  Meta hourly {acc['name']} error: {data['error'].get('message', '')}")
                break

            for r in data.get('data', []):
                spend = float(r.get('spend', 0))
                if spend > 0:
                    hour_range = r.get('hourly_stats_aggregated_by_advertiser_time_zone', '00:00:00 - 00:59:59')
                    hour = int(hour_range.split(':')[0])
                    rows.append({
                        'account': acc['name'],
                        'campaign_name': r.get('campaign_name', ''),
                        'campaign_id': r.get('campaign_id', ''),
                        'adset_name': r.get('adset_name', ''),
                        'adset_id': r.get('adset_id', ''),
                        'spend': spend,
                        'hour': hour,
                    })

            url = data.get('paging', {}).get('next')
            if url:
                time.sleep(0.5)

        print(f"  Meta hourly: {acc['name']} done")

    return rows


def fetch_shopify_orders(date_str, end_date_str=None):
    """Fetch Shopify orders for a date (or date range).
    Returns list of flattened order-lineitem dicts.
    """
    if not SHOPIFY_TOKEN or not SHOPIFY_STORE:
        print("  Shopify credentials missing")
        return []

    if end_date_str is None:
        end_date_str = date_str

    created_min = f"{date_str}T00:00:00+05:30"
    created_max = f"{end_date_str}T23:59:59+05:30"

    base_url = f"https://{SHOPIFY_STORE}/admin/api/2024-01/orders.json"
    headers = {'X-Shopify-Access-Token': SHOPIFY_TOKEN}

    all_orders = []
    params = {
        'created_at_min': created_min,
        'created_at_max': created_max,
        'status': 'any',
        'limit': 250,
    }

    page = 0
    url = base_url
    while url:
        try:
            resp = requests.get(url, headers=headers, params=params if page == 0 else None, timeout=60)
            data = resp.json()
        except Exception as e:
            print(f"  Shopify fetch error: {e}")
            break

        orders = data.get('orders', [])
        if not orders:
            break

        for order in orders:
            base = {
                'Name': order.get('name', ''),
                'Created at': order.get('created_at', ''),
                'Total': float(order.get('total_price', 0)),
                'Source': order.get('source_name', ''),
                'Shipping Name': '',
                'Shipping Zip': '',
                'Note Attributes': '',
            }

            shipping = order.get('shipping_address') or {}
            base['Shipping Name'] = shipping.get('name', '')
            base['Shipping Zip'] = str(shipping.get('zip', ''))

            note_attrs = order.get('note_attributes', [])
            if note_attrs:
                base['Note Attributes'] = ' '.join(
                    f"{a.get('name', '')}:{a.get('value', '')}" for a in note_attrs
                )

            line_items = order.get('line_items', [])
            if not line_items:
                all_orders.append({**base, 'Lineitem name': '', 'Lineitem quantity': 0, 'Lineitem price': 0.0})
            else:
                for li in line_items:
                    all_orders.append({
                        **base,
                        'Lineitem name': li.get('name', ''),
                        'Lineitem quantity': int(li.get('quantity', 1)),
                        'Lineitem price': float(li.get('price', 0)),
                    })

        link_header = resp.headers.get('Link', '')
        url = None
        if 'rel="next"' in link_header:
            for part in link_header.split(','):
                if 'rel="next"' in part:
                    url = part.split('<')[1].split('>')[0]
                    break

        page += 1
        time.sleep(0.5)

    print(f"  Shopify: {len(all_orders)} line items fetched ({date_str} to {end_date_str})")
    return all_orders
