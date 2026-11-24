import os
from dotenv import load_dotenv
load_dotenv()

API_VERSION = 'v21.0'
META_API_BASE = f'https://graph.facebook.com/{API_VERSION}'

SHOPIFY_STORE = os.getenv('SHOPIFY_STORE_DOMAIN', 'jaadudiet.myshopify.com')
SHOPIFY_TOKEN = os.getenv('SHOPIFY_ACCESS_TOKEN', '')
API_SECRET = os.getenv('API_SECRET', '')

ACCOUNTS = [
    {'name': 'Jaadu Diet',      'id': os.getenv('ACCOUNT_1_ID', ''), 'token': os.getenv('ACCOUNT_1_TOKEN', ''), 'cm3': 'Acc 1',         'short': 'MA1', 'suffix': '015'},
    {'name': 'DBC 2',           'id': os.getenv('ACCOUNT_2_ID', ''), 'token': os.getenv('ACCOUNT_2_TOKEN', ''), 'cm3': 'Acc 2',         'short': 'MA2', 'suffix': '680'},
    {'name': 'Chimal Bajjer',   'id': os.getenv('ACCOUNT_3_ID', ''), 'token': os.getenv('ACCOUNT_3_TOKEN', ''), 'cm3': 'Acc 3',         'short': 'MA3', 'suffix': '044'},
    {'name': "Dr Bimal's",      'id': os.getenv('ACCOUNT_4_ID', ''), 'token': os.getenv('ACCOUNT_4_TOKEN', ''), 'cm3': 'Acc 4',         'short': 'MA4', 'suffix': '768'},
    {'name': 'FA1',             'id': os.getenv('ACCOUNT_5_ID', ''), 'token': os.getenv('ACCOUNT_5_TOKEN', ''), 'cm3': 'Flank MA 4',    'short': 'FA1', 'suffix': '389'},
    {'name': 'FA2',             'id': os.getenv('ACCOUNT_6_ID', ''), 'token': os.getenv('ACCOUNT_6_TOKEN', ''), 'cm3': 'Flank RS NPD',  'short': 'FA2', 'suffix': '204'},
]

SUFFIX_TO_CM3 = {a['suffix']: a['cm3'] for a in ACCOUNTS}
CM3_TO_SHORT = {a['cm3']: a['short'] for a in ACCOUNTS}
NAME_TO_CM3 = {a['name']: a['cm3'] for a in ACCOUNTS}
NAME_TO_SHORT = {a['name']: a['short'] for a in ACCOUNTS}

STATE_GROUPS = {
    'MH': ['MAHARASHTRA'],
    'NCR': ['DELHI', 'HARYANA', 'PUNJAB', 'CHANDIGARH', 'UTTARAKHAND', 'HIMACHAL PRADESH', 'JAMMU AND KASHMIR', 'LADAKH'],
    'UP': ['UTTAR PRADESH'],
    'BR': ['BIHAR', 'CHHATTISGARH', 'JHARKHAND'],
    'WB': ['WEST BENGAL', 'ODISHA', 'ASSAM', 'ARUNACHAL PRADESH', 'MEGHALAYA', 'TRIPURA', 'MIZORAM', 'MANIPUR', 'NAGALAND', 'SIKKIM', 'ANDAMAN AND NICOBAR ISLANDS'],
    'KA': ['KARNATAKA'],
    'TN': ['TAMIL NADU', 'PUDUCHERRY'],
    'KL': ['KERALA', 'LAKSHADWEEP'],
    'AP': ['ANDHRA PRADESH'],
    'TELANGANA': ['TELANGANA'],
}

REDIR_STATES = {'RAJASTHAN': 'MH', 'MADHYA PRADESH': 'MH', 'GUJARAT': 'MH', 'GOA': 'MH'}

NCR_UP_DISTRICTS = {'MEERUT', 'BULANDSHAHR', 'MUZAFFARNAGAR', 'HAPUR', 'SHAMLI', 'GHAZIABAD', 'GAUTAM BUDDHA NAGAR', 'BAGHPAT'}

STATE_LABELS = {
    'MH': 'Maharashtra', 'NCR': 'Delhi NCR', 'UP': 'Uttar Pradesh',
    'WB': 'West Bengal', 'HYD': 'Hyderabad', 'AP': 'Andhra Pradesh',
    'TS': 'Telangana', 'KA': 'Karnataka', 'TN': 'Tamil Nadu',
    'KL': 'Kerala', 'BR': 'Bihar', 'JH': 'Jharkhand',
    'APTS': 'APTS', 'RJ': 'Rajasthan',
}

STATE_ABBR = {v: k for k, v in STATE_LABELS.items()}

STATE_ORDER = ['MH', 'NCR', 'UP', 'WB', 'KA', 'TN', 'KL', 'BR', 'APTS', 'RJ']

CAMPAIGN_STATE_PATTERNS = [
    (r"\bapts?\b", "APTS"), (r"rajaasthan|rajasthan|jaipur|\brj\b", "RJ"),
    (r"uttar pradesh|lucknow|kanpur|\bup\b|bhojpuri", "UP"),
    (r"\bmh\b|maharashtra|mumbai|pune|marathi", "MH"),
    (r"delhi|\bdl\b|ncr|meerut|punjab|\bat tf\b|diwali sale", "NCR"),
    (r"west bengal|\bwb\b|mp_wb|_wb_|darjeeling|alipurduar|kolkata|\bben\b|bengali", "WB"),
    (r"\bhy|hyder", "APTS"), (r"\bap\b|visakha|andhra", "APTS"),
    (r"telang|\bts\b|telugu", "APTS"),
    (r"\btn\b|tamil", "TN"), (r"\bkl\b|kerala|malayalam", "KL"),
    (r"\bkn\b|kar|bengal?ore|belgaum|kannada", "KA"),
    (r"bihar|\bbr\b|mp_br|_br_", "BR"), (r"\bjh\b|jharkhand", "BR"),
    (r"\bhin\b|hindi\b|hindi |hindi", "NCR"),
    (r"\beng\b|english\b| eng | hin ", "NCR"),
]
