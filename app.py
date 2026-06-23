"""
CryptoGuard — Multi-Chain Fraud Detection Flask Application
Author : Obiyomi Oluwanifemi (BU22CSC1008)
Chains : Ethereum (ETH), Bitcoin (BTC)
"""
from flask import Flask, request, jsonify, render_template
import joblib, json, os, requests, re
import numpy as np
import pandas as pd
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'cryptoguard-secret-2024'
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR     = os.path.join(BASE_DIR, 'saved_models')
ETHERSCAN_KEY = 'VV5HMS8GG4PTZNJDP4KCXQAJJAJ3FVSSAY'

# ─────────────────────────────────────────────────────────────────────────────
# FRAUD BLACKLISTS — hardcoded base + auto-updated from OFAC live feed
# ─────────────────────────────────────────────────────────────────────────────
import threading, time as _time

# ── Base hardcoded lists (always present, never removed) ─────────────────────
_ETH_BASE = {
    '0x098b716b8aaf21512996dc57eb0615e2383e2f96': 'Ronin Bridge Hacker — Lazarus Group ($625M)',
    '0x59abf3837fa962d6853b4cc0a19513aa031fd32d': 'FTX Exchange Exploiter ($477M)',
    '0x56d8b635a7c88fd1104d23d632af40c1c3aac4e3': 'Nomad Bridge Exploiter ($190M)',
    '0xe74b28c2eae8679e3ccc3a94d5d0de83ccb84705': 'Wintermute Hacker ($160M)',
    '0xb66cd966670d962c227b3eaba30a872dbfb995db': 'Euler Finance Exploiter ($197M)',
    '0x629e7da20197a5429d30da36e77d06cdf796b71a': 'Wormhole Bridge Exploiter ($320M)',
    '0xb7f190099440ce6da67ef0f2a80ae70c69e96b3a': 'Binance Bridge Exploiter ($570M)',
    '0x722122df12d4e14e13ac3b6895a86e84145b6967': 'Tornado Cash — OFAC Sanctioned Mixer',
    '0xd90e2f925da726b50c4ed8d0fb90ad053324f31b': 'Tornado Cash Router — OFAC Sanctioned',
    '0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be': 'Lazarus Group (North Korea state hacker)',
    '0xc8a65fadf0e0ddaf421f28feab69bf6e2e589963': 'Poly Network Exploiter ($611M)',
    '0xeb31da939878d1d780fdbcc244531c0fb80a2cf3': 'KuCoin Exchange Hacker ($281M)',
    '0x58f479b894dc25ccdb7aa6ded2d12a48ebce6573': 'Harmony Horizon Bridge Hacker ($100M)',
    '0x4f47bc496083c727c5fbe3ce9cdf2b0f6496270c': 'Multichain Exploiter ($126M)',
    '0x6f1ca141a28907f78ebaa64fb83a9088b02a8352': 'Atomic Wallet Hacker — Lazarus Group',
}

_BTC_BASE = {
    '1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uF': 'Alleged Satoshi wallet — monitored address',
    '12ib7dApVFvg82TXKycWBNpN8kFyiAN1dr': 'BitFinex Hack Associated Wallet (2016)',
    '1HQ3Go3ggs8pFnXuHVHRytPCq5fGG8Hbhx': 'Silk Road Seized Wallet (FBI)',
    'bc1qa5wkgaew2dkv56kfvj49j0av5nml45x9ek9hz6': 'Colonial Pipeline Ransom Wallet',
    '1Die1ygN2hUBgcJPJunxZ2eJFyGfigdPaG': 'BTC-e Exchange Hacker',
    '1Kuf2Rd8mDyAViwBozGTNYnvWL8uDdjqGq': 'Mt. Gox Hack Associated Wallet',
    '13AM4VW2dhxYgXeQepoHkHSQuy6NgaEb94': 'Ransomware Payment Wallet (WannaCry)',
    '115p7UMMngoj1pMvkpHijcRdfJNXj6LrLn': 'Lazarus Group BTC Wallet',
}

# ── Live blacklist dicts (start as copies of base, updated by background thread)
ETH_BLACKLIST = dict(_ETH_BASE)
BTC_BLACKLIST = dict(_BTC_BASE)

# ── Track last update ────────────────────────────────────────────────────────
blacklist_meta = {
    'last_updated'  : 'Not yet fetched',
    'ofac_eth_added': 0,
    'ofac_btc_added': 0,
    'total_eth'     : len(ETH_BLACKLIST),
    'total_btc'     : len(BTC_BLACKLIST),
    'status'        : 'Starting...',
}


def fetch_ofac_blacklist():
    """
    Fetch the official OFAC sanctions list from a public GitHub mirror
    maintained by 0xB10C — updated within hours of every OFAC release.
    Source: github.com/0xB10C/ofac-sanctioned-digital-currency-addresses

    Falls back silently to the hardcoded base list if any fetch fails.
    """
    global ETH_BLACKLIST, BTC_BLACKLIST, blacklist_meta

    OFAC_SOURCES = {
        'ETH': [
            'https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/main/output/sanctioned_addresses_ETH.txt',
            'https://raw.githubusercontent.com/ultrasoundmoney/ofac-ethereum-addresses/main/data/addresses.json',
        ],
        'BTC': [
            'https://raw.githubusercontent.com/0xB10C/ofac-sanctioned-digital-currency-addresses/main/output/sanctioned_addresses_BTC.txt',
        ],
    }

    new_eth = dict(_ETH_BASE)
    new_btc = dict(_BTC_BASE)
    eth_added = 0
    btc_added = 0

    # ── Fetch ETH sanctions ────────────────────────────────────────────────
    for url in OFAC_SOURCES['ETH']:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue

            # Handle plain text (one address per line)
            if url.endswith('.txt'):
                lines = r.text.strip().splitlines()
                for line in lines:
                    addr = line.strip().lower()
                    if re.match(r'^0x[a-f0-9]{40}$', addr):
                        if addr not in new_eth:
                            new_eth[addr] = 'OFAC Sanctioned Address'
                            eth_added += 1
                break  # success — no need to try next URL

            # Handle JSON array
            if url.endswith('.json'):
                data = r.json()
                addrs = data if isinstance(data, list) else data.get('addresses', [])
                for addr in addrs:
                    addr = str(addr).strip().lower()
                    if re.match(r'^0x[a-f0-9]{40}$', addr):
                        if addr not in new_eth:
                            new_eth[addr] = 'OFAC Sanctioned Address'
                            eth_added += 1
                break

        except Exception:
            continue

    # ── Fetch BTC sanctions ────────────────────────────────────────────────
    for url in OFAC_SOURCES['BTC']:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code != 200:
                continue
            lines = r.text.strip().splitlines()
            for line in lines:
                addr = line.strip()
                # BTC: preserve original case (Base58 is case-sensitive)
                if re.match(r'^(1|3)[a-zA-Z0-9]{25,34}$', addr) or \
                   re.match(r'^bc1[a-zA-Z0-9]{6,87}$', addr):
                    if addr not in new_btc:
                        new_btc[addr] = 'OFAC Sanctioned Address'
                        btc_added += 1
            break
        except Exception:
            continue

    # ── Atomically update the live dicts ──────────────────────────────────
    ETH_BLACKLIST = new_eth
    BTC_BLACKLIST = new_btc

    blacklist_meta.update({
        'last_updated'  : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ofac_eth_added': eth_added,
        'ofac_btc_added': btc_added,
        'total_eth'     : len(ETH_BLACKLIST),
        'total_btc'     : len(BTC_BLACKLIST),
        'status'        : 'Live (OFAC + hardcoded)' if (eth_added + btc_added) > 0
                          else 'Hardcoded only (OFAC fetch failed or returned 0 new)',
    })

    print(f'  [Blacklist] Updated: {len(ETH_BLACKLIST)} ETH '
          f'(+{eth_added} OFAC) | {len(BTC_BLACKLIST)} BTC (+{btc_added} OFAC) '
          f'at {blacklist_meta["last_updated"]}')


def _blacklist_refresh_loop():
    """
    Background thread: fetch OFAC list at startup, then every 24 hours.
    Never crashes the app — all errors are caught silently.
    """
    # Small delay so Flask finishes starting before first fetch
    _time.sleep(3)
    while True:
        try:
            fetch_ofac_blacklist()
        except Exception as e:
            print(f'  [Blacklist] Refresh error: {e}')
        # Refresh every 24 hours
        _time.sleep(86400)


# Start the background refresh thread (daemon=True means it dies with the app)
_bl_thread = threading.Thread(target=_blacklist_refresh_loop, daemon=True)
_bl_thread.start()

COMBINED_BLACKLIST_SIZE = len(ETH_BLACKLIST) + len(BTC_BLACKLIST)

# ─────────────────────────────────────────────────────────────────────────────
# LOAD MODELS
# ─────────────────────────────────────────────────────────────────────────────
print('\nLoading models...')
try:
    rf_model     = joblib.load(os.path.join(MODEL_DIR, 'model_rf.pkl'))
    xgb_model    = joblib.load(os.path.join(MODEL_DIR, 'model_xgb.pkl'))
    gcn_model    = joblib.load(os.path.join(MODEL_DIR, 'model_gcn.pkl'))
    gat_model    = joblib.load(os.path.join(MODEL_DIR, 'model_gat.pkl'))
    scaler       = joblib.load(os.path.join(MODEL_DIR, 'scaler.pkl'))
    feature_cols = joblib.load(os.path.join(MODEL_DIR, 'feature_columns.pkl'))
    with open(os.path.join(MODEL_DIR, 'model_metadata.json')) as f:
        metadata = json.load(f)
    MODELS = {'Random Forest': rf_model, 'XGBoost': xgb_model,
              'GCN': gcn_model, 'GAT': gat_model}
    print('  All models loaded')
except Exception as e:
    print(f'  Model load error: {e}')
    MODELS, feature_cols, metadata = {}, [], {}

prediction_history = []


# ─────────────────────────────────────────────────────────────────────────────
# CHAIN DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def detect_chain(address: str) -> str:
    """
    Auto-detect which blockchain an address belongs to.
    Returns 'ETH', 'BTC', or 'UNKNOWN'.
    """
    addr = address.strip()
    if re.match(r'^0x[a-fA-F0-9]{40}$', addr):          return 'ETH'
    if re.match(r'^(1|3)[a-zA-Z0-9]{25,34}$', addr):    return 'BTC'
    if re.match(r'^bc1[a-zA-Z0-9]{6,87}$', addr):       return 'BTC'
    if re.match(r'^0x[a-fA-F0-9]{64}$', addr):          return 'ETH'  # ETH tx hash
    if re.match(r'^[a-fA-F0-9]{64}$', addr):            return 'BTC'  # BTC tx hash
    return 'UNKNOWN'


# ─────────────────────────────────────────────────────────────────────────────
# BLACKLIST CHECK
# ─────────────────────────────────────────────────────────────────────────────
def check_blacklist(from_addr, to_addr, chain):
    """
    ETH addresses are hex and case-insensitive — safe to lowercase.
    BTC addresses are Base58 and CASE-SENSITIVE — must NOT be lowercased.
    """
    frm = from_addr.strip()
    to  = to_addr.strip()

    if chain == 'ETH':
        # ETH: lowercase both address and blacklist keys for safe comparison
        bl = {k.lower(): v for k, v in ETH_BLACKLIST.items()}
        if frm.lower() in bl:
            return True, f'Sender matches confirmed fraud wallet: {bl[frm.lower()]}'
        if to.lower() in bl:
            return True, f'Receiver matches confirmed fraud wallet: {bl[to.lower()]}'
    else:
        # BTC: compare as-is — Base58 is case-sensitive
        if frm in BTC_BLACKLIST:
            return True, f'Sender matches confirmed fraud wallet: {BTC_BLACKLIST[frm]}'
        if to in BTC_BLACKLIST:
            return True, f'Receiver matches confirmed fraud wallet: {BTC_BLACKLIST[to]}'

    return False, None


# ─────────────────────────────────────────────────────────────────────────────
# ETHEREUM — ETHERSCAN LIVE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────
def fetch_eth_wallet(address: str) -> dict:
    stats = {}
    base  = 'https://api.etherscan.io/api'
    addr  = address.strip().lower()
    try:
        r = requests.get(base, params={'module':'account','action':'balance',
            'address':addr,'tag':'latest','apikey':ETHERSCAN_KEY}, timeout=6).json()
        stats['balance'] = int(r.get('result', 0)) / 1e18

        r = requests.get(base, params={'module':'account','action':'txlist',
            'address':addr,'startblock':0,'endblock':99999999,
            'sort':'asc','apikey':ETHERSCAN_KEY}, timeout=10).json()
        txs = r.get('result', [])

        if not isinstance(txs, list) or len(txs) == 0:
            return stats

        sent = [t for t in txs if t.get('from','').lower() == addr]
        recv = [t for t in txs if t.get('to','').lower()   == addr]
        sv   = [int(t.get('value',0))/1e18 for t in sent]
        rv   = [int(t.get('value',0))/1e18 for t in recv]

        stats.update({
            'sent_count'    : len(sent),
            'recv_count'    : len(recv),
            'total_txns'    : len(txs),
            'unique_sent_to': len(set(t.get('to','') for t in sent)),
            'total_sent'    : sum(sv),
            'total_recv'    : sum(rv),
            'avg_sent'      : float(np.mean(sv)) if sv else 0,
            'contracts_created': sum(1 for t in txs if not t.get('to')),
        })

        ts = sorted([int(t.get('timeStamp',0)) for t in txs if t.get('timeStamp')])
        if len(ts) >= 2:
            gaps = [(ts[i+1]-ts[i])/60 for i in range(len(ts)-1)]
            stats['avg_gap_mins']  = float(np.mean(gaps))
            stats['time_span_hrs'] = (ts[-1]-ts[0])/3600
        else:
            stats['avg_gap_mins']  = 0
            stats['time_span_hrs'] = 0

    except Exception as e:
        stats['lookup_error'] = str(e)
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# BITCOIN — BLOCKCHAIN.COM LIVE LOOKUP (no API key needed)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_btc_wallet(address: str) -> dict:
    stats = {}
    try:
        r   = requests.get(
            f'https://blockchain.info/rawaddr/{address}?limit=50',
            timeout=10
        ).json()
        txs = r.get('txs', [])
        addr = address.strip()

        stats['balance']   = r.get('final_balance', 0)   / 1e8
        stats['total_recv']= r.get('total_received', 0)  / 1e8
        stats['total_sent']= r.get('total_sent', 0)      / 1e8
        stats['total_txns']= r.get('n_tx', 0)

        sent_txs, recv_txs = [], []
        sent_vals, recv_vals = [], []
        sent_to = set()

        for tx in txs:
            inputs  = tx.get('inputs', [])
            outputs = tx.get('out', [])
            is_sender = any(
                i.get('prev_out', {}).get('addr') == addr for i in inputs
            )
            if is_sender:
                sent_txs.append(tx)
                for o in outputs:
                    if o.get('addr') and o.get('addr') != addr:
                        sent_vals.append(o.get('value', 0) / 1e8)
                        sent_to.add(o['addr'])
            else:
                recv_txs.append(tx)
                for o in outputs:
                    if o.get('addr') == addr:
                        recv_vals.append(o.get('value', 0) / 1e8)

        stats['sent_count']     = len(sent_txs)
        stats['recv_count']     = len(recv_txs)
        stats['unique_sent_to'] = len(sent_to)
        stats['avg_sent']       = float(np.mean(sent_vals)) if sent_vals else 0
        stats['contracts_created'] = 0   # N/A for BTC

        ts = sorted([tx.get('time', 0) for tx in txs if tx.get('time')])
        if len(ts) >= 2:
            gaps = [(ts[i+1]-ts[i])/60 for i in range(len(ts)-1)]
            stats['avg_gap_mins']  = float(np.mean(gaps))
            stats['time_span_hrs'] = (ts[-1]-ts[0])/3600
        else:
            stats['avg_gap_mins']  = 0
            stats['time_span_hrs'] = 0

    except Exception as e:
        stats['lookup_error'] = str(e)
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# SHARED WALLET BEHAVIOUR SCORER (works for both ETH and BTC stats)
# ─────────────────────────────────────────────────────────────────────────────
def wallet_behaviour_score(stats: dict, chain: str) -> tuple:
    score = 0.0; flags = []
    unit  = 'ETH' if chain == 'ETH' else 'BTC'

    sent      = stats.get('sent_count', 0)
    recv      = stats.get('recv_count', 0)
    balance   = stats.get('balance', 0)
    t_sent    = stats.get('total_sent', 0)
    t_recv    = stats.get('total_recv', 0)
    avg_gap   = stats.get('avg_gap_mins', 0)
    uniq_sent = stats.get('unique_sent_to', 0)
    contracts = stats.get('contracts_created', 0)
    time_span = stats.get('time_span_hrs', 0)

    if uniq_sent > 50:
        score += 0.40
        flags.append(f'Sends to {uniq_sent} unique addresses — fund scattering pattern')
    elif uniq_sent > 20:
        score += 0.20
        flags.append(f'Sends to {uniq_sent} unique addresses — elevated distribution')

    if recv == 0 and sent > 5:
        score += 0.30
        flags.append('Wallet only sends, never receives — one-way draining pattern')
    elif recv > 0 and sent / recv > 10:
        score += 0.25
        flags.append(f'Sent/received ratio {sent/recv:.0f}x — suspicious one-way flow')

    if t_sent > 1 and balance < 0.001:
        score += 0.30
        flags.append(f'Sent {t_sent:.4f} {unit} but balance near zero — draining pattern')

    if 0 < avg_gap < 2:
        score += 0.30
        flags.append(f'Avg {avg_gap:.1f} mins between transactions — bot-like speed')
    elif 0 < avg_gap < 10:
        score += 0.15
        flags.append(f'Avg {avg_gap:.1f} mins between transactions — unusually fast')

    if t_recv > 0 and t_sent > 0 and t_sent / t_recv > 5:
        score += 0.20
        flags.append(f'Total sent ({t_sent:.2f} {unit}) far exceeds total received ({t_recv:.2f} {unit})')

    if contracts > 5:
        score += 0.25
        flags.append(f'Deployed {contracts} smart contracts — possible scam deployer')

    if time_span > 0 and sent > 0 and (sent / time_span) > 10:
        score += 0.20
        flags.append(f'{sent} transactions in {time_span:.1f} hours — burst activity')

    return min(score, 1.0), flags


# ─────────────────────────────────────────────────────────────────────────────
# TRANSACTION FIELD RULES
# ─────────────────────────────────────────────────────────────────────────────
def rule_based_score(amount, fee, value_usd, from_addr, to_addr, chain):
    score = 0.0; flags = []
    unit  = 'ETH' if chain == 'ETH' else 'BTC'
    frm   = str(from_addr).lower().strip()
    to    = str(to_addr).lower().strip()

    if frm and to and frm == to:
        score += 0.55
        flags.append('Self-transfer: sender and receiver are the same address')
    if amount == 0 and fee > 0.0001:
        score += 0.30
        flags.append('Zero value transferred but significant network fee paid')
    if amount > 0 and fee > 0 and fee / amount > 0.05:
        score += 0.20
        flags.append(f'Fee ({fee:.6f} {unit}) disproportionately high vs amount ({amount:.6f} {unit})')
    if value_usd > 1000:
        score += 0.15
        flags.append(f'High-value transaction (${value_usd:,.2f})')
    if amount > 50 and chain == 'ETH':
        score += 0.15
        flags.append(f'Unusually large ETH amount ({amount:.4f} ETH)')
    if amount > 5 and chain == 'BTC':
        score += 0.15
        flags.append(f'Unusually large BTC amount ({amount:.4f} BTC)')

    return min(score, 1.0), flags


# ─────────────────────────────────────────────────────────────────────────────
# AI MODEL PREDICTION
# ─────────────────────────────────────────────────────────────────────────────
def model_predict(amount, fee, value_usd):
    if not MODELS or not feature_cols:
        return {}, 0.0
    row = {col: 0.0 for col in feature_cols}
    for val, possible in [
        (amount,    ['avg_val_sent','avg val sent','btc_feature_5']),
        (fee,       ['avg_value_sent_to_contract','avg value sent to contract','btc_feature_6']),
        (value_usd, ['max_value_received','max value received','btc_feature_14']),
    ]:
        for col in possible:
            if col in row: row[col] = val; break
    df     = pd.DataFrame([row])[feature_cols]
    scaled = pd.DataFrame(scaler.transform(df), columns=feature_cols)
    results = {}; probs = []
    for name, model in MODELS.items():
        p    = int(model.predict(scaled)[0])
        prob = float(model.predict_proba(scaled)[0][1])
        probs.append(prob)
        results[name] = {'prediction':p,
                         'verdict':'Fraudulent' if p==1 else 'Legitimate',
                         'confidence':round(prob*100,1)}
    return results, float(np.mean(probs))


# ─────────────────────────────────────────────────────────────────────────────
# EXPLANATION BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def build_explanation(verdict, prob, flags, blacklist_hit, wallet_checked, chain):
    unit      = 'Ethereum' if chain == 'ETH' else 'Bitcoin'
    flag_text = (' Key indicators: ' + '; '.join(flags[:3]) + '.') if flags else ''
    if blacklist_hit:
        return (f"This {unit} transaction involves a confirmed fraudulent wallet address "
                f"in our fraud blacklist.{flag_text} "
                f"This address has been publicly linked to a major cryptocurrency theft. "
                f"Do not proceed with any transaction involving this address.")
    elif verdict == 'Fraudulent':
        src = 'live wallet behaviour analysis and AI models' if wallet_checked else 'transaction pattern analysis'
        return (f"This {unit} transaction has been flagged as potentially fraudulent "
                f"with {prob:.1f}% confidence via {src}.{flag_text} "
                f"Halt this transaction and verify both addresses before proceeding.")
    elif prob >= 35:
        return (f"This {unit} transaction shows suspicious characteristics "
                f"({prob:.1f}% fraud probability).{flag_text} Manual review recommended.")
    else:
        src = 'Live wallet history and' if wallet_checked else ''
        return (f"This {unit} transaction appears legitimate with {100-prob:.1f}% confidence. "
                f"{src} transaction fields show no significant fraud indicators.")


# ─────────────────────────────────────────────────────────────────────────────
# LIVE DATA (home page)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_crypto_prices():
    try:
        r = requests.get('https://api.coingecko.com/api/v3/simple/price'
            '?ids=bitcoin,ethereum,binancecoin&vs_currencies=usd', timeout=5).json()
        return {'btc_price': f"{r['bitcoin']['usd']:,}",
                'eth_price': f"{r['ethereum']['usd']:,}",
                'bnb_price': f"{r['binancecoin']['usd']:,}"}
    except:
        return {'btc_price':'N/A','eth_price':'N/A','bnb_price':'N/A'}

def fetch_crypto_news():
    try:
        r = requests.get('https://cryptopanic.com/api/v1/posts/'
            '?auth_token=public&kind=news&public=true', timeout=5).json()
        return [{'title':a.get('title',''),'url':a.get('url','#'),
                 'published_at':a.get('published_at','')[:10]}
                for a in r.get('results',[])[:6]]
    except:
        return [{'title':'Blockchain security remains a top priority in 2024',
                 'url':'#','published_at':datetime.now().strftime('%Y-%m-%d')}]


# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html',
                           news=fetch_crypto_news(), **fetch_crypto_prices())

@app.route('/detect')
def detect():
    return render_template('detect.html', result=None, error=None)

@app.route('/detect-pro')
def detect_pro():
    return render_template('detect_pro.html', result=None, error=None)

@app.route('/dashboard')
def dashboard():
    total  = len(prediction_history)
    frauds = sum(1 for p in prediction_history if p['prediction']==1)
    best   = max((m.get('accuracy',0) for m in metadata.get('models',{}).values()), default=0)
    return render_template('dashboard.html',
        total_transactions=total, fraud_count=frauds,
        legit_count=total-frauds, model_accuracy=round(best*100,2),
        history=prediction_history[-10:][::-1])


@app.route('/predict-ai', methods=['POST'])
def predict_ai():
    page      = request.form.get('page', 'detect')
    tx_hash   = request.form.get('tx_hash', '').strip()
    from_addr = request.form.get('from_addr', '').strip()
    to_addr   = request.form.get('to_addr', '').strip()
    amount    = float(request.form.get('amount') or 0)
    fee       = float(request.form.get('fee') or 0)
    value_usd = float(request.form.get('value_usd') or 0)
    tmpl      = 'detect_pro.html' if page == 'detect_pro' else 'detect.html'

    if not tx_hash:
        return render_template(tmpl, result=None,
                               error='Please enter a transaction hash.')

    # ── Auto-detect chain ──────────────────────────────────────────────────────
    chain = detect_chain(from_addr) if from_addr else detect_chain(tx_hash)
    if chain == 'UNKNOWN':
        chain = 'ETH'   # default to ETH if undetectable

    all_flags      = []
    blacklist_hit  = False
    blacklist_msg  = None
    wallet_stats   = {}
    wallet_checked = False

    # ── Layer 1: Blacklist check ───────────────────────────────────────────────
    if from_addr or to_addr:
        hit, bl_msg = check_blacklist(from_addr, to_addr, chain)
        if hit:
            blacklist_hit = True
            blacklist_msg = bl_msg
            all_flags.append(f'🚨 BLACKLISTED: {bl_msg}')

    # ── Layer 2: Live wallet lookup ────────────────────────────────────────────
    lookup_addr = from_addr or to_addr
    if lookup_addr:
        try:
            if chain == 'ETH' and re.match(r'^0x[a-fA-F0-9]{40}$', lookup_addr):
                wallet_stats   = fetch_eth_wallet(lookup_addr)
                wallet_checked = True
            elif chain == 'BTC':
                wallet_stats   = fetch_btc_wallet(lookup_addr)
                wallet_checked = True

            if wallet_checked and 'lookup_error' not in wallet_stats:
                w_score, w_flags = wallet_behaviour_score(wallet_stats, chain)
                all_flags.extend(w_flags)
            else:
                w_score = 0.0
        except:
            w_score = 0.0
    else:
        w_score = 0.0

    # ── Layer 3: Rule-based score ──────────────────────────────────────────────
    r_score, r_flags = rule_based_score(amount, fee, value_usd,
                                         from_addr, to_addr, chain)
    all_flags.extend(r_flags)

    # ── Layer 4: AI model score ────────────────────────────────────────────────
    model_breakdown, avg_model_prob = model_predict(amount, fee, value_usd)

    # ── Blend all layers ───────────────────────────────────────────────────────
    if blacklist_hit:
        fraud_pct = 100.0
    else:
        blended   = (avg_model_prob * 0.20) + (r_score * 0.40) + (w_score * 0.40)
        fraud_pct = round(blended * 100, 1)

    if fraud_pct >= 60:   verdict, rlevel = 'Fraudulent', 'High'
    elif fraud_pct >= 35: verdict, rlevel = 'Suspicious', 'Medium'
    else:                 verdict, rlevel = 'Legitimate',  'Low'

    # ── Build wallet display ───────────────────────────────────────────────────
    unit = 'ETH' if chain == 'ETH' else 'BTC'
    wallet_display = {}
    if wallet_stats and 'lookup_error' not in wallet_stats:
        wallet_display = {
            f'{unit} Balance'            : f"{wallet_stats.get('balance',0):.6f} {unit}",
            f'Total {unit} Sent'         : f"{wallet_stats.get('total_sent',0):.4f} {unit}",
            f'Total {unit} Received'     : f"{wallet_stats.get('total_recv',0):.4f} {unit}",
            'Sent Transactions'          : wallet_stats.get('sent_count', 0),
            'Received Transactions'      : wallet_stats.get('recv_count', 0),
            'Unique Addresses Sent To'   : wallet_stats.get('unique_sent_to', 0),
            'Avg Time Between Txns'      : f"{wallet_stats.get('avg_gap_mins',0):.1f} mins",
            'Contracts Deployed'         : wallet_stats.get('contracts_created', 0),
        }

    # Determine which API was used
    api_used = ('Etherscan API' if chain == 'ETH' else
                'Blockchain.com API') if wallet_checked else 'N/A'

    result = {
        'tx_hash'         : tx_hash,
        'from_addr'       : from_addr,
        'to_addr'         : to_addr,
        'amount'          : amount,
        'fee'             : fee,
        'value_usd'       : value_usd,
        'chain'           : chain,
        'chain_label'     : '⟠ Ethereum' if chain == 'ETH' else '₿ Bitcoin',
        'api_used'        : api_used,
        'is_self_transfer': (from_addr.lower()==to_addr.lower()
                             if from_addr and to_addr else False),
        'blacklist_hit'   : blacklist_hit,
        'blacklist_msg'   : blacklist_msg,
        'wallet_checked'  : wallet_checked,
        'wallet_display'  : wallet_display,
        'verdict'         : verdict,
        'risk_level'      : rlevel,
        'fraud_prob'      : fraud_pct,
        'confidence'      : f'{fraud_pct:.1f}%',
        'flags'           : all_flags,
        'rule_score'      : round(r_score * 100, 1),
        'wallet_score'    : round(w_score * 100, 1),
        'model_score'     : round(avg_model_prob * 100, 1),
        'model_breakdown' : model_breakdown,
        'explanation'     : build_explanation(verdict, fraud_pct, all_flags,
                                              blacklist_hit, wallet_checked, chain),
    }

    prediction_history.append({
        'prediction': 1 if verdict == 'Fraudulent' else 0,
        'verdict'   : verdict,
        'confidence': fraud_pct,
        'query'     : tx_hash[:20] + '...',
        'chain'     : chain,
        'timestamp' : datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })

    return render_template(tmpl, result=result, error=None)


@app.route('/health')
def health():
    return jsonify({'status':'ok','models':list(MODELS.keys()),
                    'features':len(feature_cols),
                    'predictions':len(prediction_history),
                    'blacklist': blacklist_meta})

@app.route('/api/blacklist-status')
def blacklist_status():
    """Returns live blacklist stats — call this from the dashboard or browser."""
    return jsonify({
        **blacklist_meta,
        'eth_addresses': len(ETH_BLACKLIST),
        'btc_addresses': len(BTC_BLACKLIST),
        'sample_eth'   : list(ETH_BLACKLIST.keys())[:3],
        'sample_btc'   : list(BTC_BLACKLIST.keys())[:3],
    })

@app.route('/api/metadata')
def api_metadata():
    return jsonify(metadata)


if __name__ == '__main__':
    print('\n'+'='*60)
    print('  CryptoGuard — Multi-Chain Fraud Detection')
    print(f'  Chains  : Ethereum (Etherscan) + Bitcoin (Blockchain.com)')
    print(f'  Blacklist: {len(ETH_BLACKLIST)} ETH + {len(BTC_BLACKLIST)} BTC addresses')
    print('  URL     : http://localhost:5000')
    print('='*60+'\n')
    app.run(debug=True, host='0.0.0.0', port=5000)
