#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  NEST REBOOTER                                               ║
║  Scheduled full network restart for Google/Nest WiFi         ║
║                                                               ║
║  Uses the Google Home Foyer cloud API — the same backend     ║
║  the Google Home app calls — to restart the entire network.  ║
║                                                               ║
║  Auth: master_token (EmbeddedSetup cookie) → gpsoauth →      ║
║        access_token → googlehomefoyer-pa.googleapis.com      ║
║                                                               ║
║  Both REST and gRPC paths are attempted for maximum compat.  ║
╚══════════════════════════════════════════════════════════════╝

Requirements: pip install glocaltokens gpsoauth requests grpcio
"""

import argparse, json, logging, os, socket, struct, subprocess, sys, time, urllib3
from datetime import datetime
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APP_NAME = "nest-rebooter"
APP_DIR = Path.home() / f".config/{APP_NAME}"
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "nest-rebooter.log"
SYSTEMD_SERVICE = f"{APP_NAME}.service"
SYSTEMD_TIMER = f"{APP_NAME}.timer"
SYSTEMD_DIR = Path.home() / ".config/systemd/user"
ANDROID_ID = "0123456789abcdef"
FOYER_HOST = "googlehomefoyer-pa.googleapis.com"
FOYER_REST = f"https://{FOYER_HOST}/v2"

BANNER = r"""
   ╭─────────────────────────────────────╮
   │     🌙  N E S T   R E B O O T E R  │
   │     Scheduled WiFi network reboot   │
   ╰─────────────────────────────────────╯
"""

def setup_logging(verbose=False):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
    )

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f: return json.load(f)
    return {}

def save_config(config):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f: json.dump(config, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)

# ─── Auth ──────────────────────────────────────────────────────────────────────

def get_master_token_interactive(email):
    import gpsoauth
    print()
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Get a Master Token (one-time)")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("  1. Open: \033[1mhttps://accounts.google.com/EmbeddedSetup\033[0m")
    print(f'  2. Log in with: \033[1m{email}\033[0m')
    print('  3. Click "I agree" (page may hang — fine)')
    print('  4. DevTools (F12) → Application → Cookies → \033[1moauth_token\033[0m')
    print()
    oauth_token = input("  Paste oauth_token here: ").strip().strip('"\'')
    if oauth_token.startswith("oauth_token="): oauth_token = oauth_token[len("oauth_token="):]
    if not oauth_token: return None
    try:
        resp = gpsoauth.exchange_token(email, oauth_token, ANDROID_ID)
        if "Token" in resp:
            logging.info("Master token obtained!")
            return resp["Token"]
        logging.error(f"Exchange failed: {resp.get('Error')}")
    except Exception as e:
        logging.error(f"Exchange error: {e}")
    return None


def get_access_token(master_token, email):
    """Get access token — uses the exact same parameters as glocaltokens
    which we know work (it successfully queries googlehomefoyer-pa every time)."""
    import gpsoauth
    logging.debug("Getting access token from master token...")

    res = gpsoauth.perform_oauth(
        email, master_token, ANDROID_ID,
        service="oauth2:https://www.google.com/accounts/OAuthLogin",
        app="com.google.android.apps.chromecast.app",
        client_sig="24bb24c05e47e0aefa68a58a766179d9b613a600",
    )
    token = res.get("Auth")
    if token:
        logging.debug("Access token obtained.")
        return token
    logging.error(f"Access token failed: {res}")
    return None

# ─── Cloud API: REST + gRPC ────────────────────────────────────────────────────

def foyer_rest_get(access_token, path):
    """REST GET to the Foyer API using the access token."""
    import requests
    r = requests.get(f"{FOYER_REST}{path}", headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }, timeout=30)
    logging.debug(f"REST GET {path}: HTTP {r.status_code}")
    return r.status_code, r.json() if r.text and r.status_code == 200 else r.text

def foyer_rest_post(access_token, path):
    """REST POST to the Foyer API."""
    import requests
    r = requests.post(f"{FOYER_REST}{path}", headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }, json={}, timeout=30)
    logging.debug(f"REST POST {path}: HTTP {r.status_code}")
    return r.status_code, r.json() if r.text and r.status_code < 400 else r.text


def encode_protobuf_string(field_number, value):
    """Encode a single string field in protobuf wire format."""
    tag = (field_number << 3) | 2  # wire type 2 = length-delimited
    encoded_value = value.encode('utf-8')
    length = len(encoded_value)
    # Encode varint for length
    varint = b""
    while length > 0x7F:
        varint += bytes([0x80 | (length & 0x7F)])
        length >>= 7
    varint += bytes([length])
    return bytes([tag]) + varint + encoded_value


def grpc_reboot(access_token, group_id):
    """Call UtilityService.RebootGroupWhenUpdateReady via gRPC.
    This is the exact call the Google Home app makes."""
    import grpc

    logging.info("Attempting gRPC reboot call...")

    # Build authenticated channel (same as glocaltokens does for HomeGraph)
    creds = grpc.access_token_call_credentials(access_token)
    ssl = grpc.ssl_channel_credentials()
    composite = grpc.composite_channel_credentials(ssl, creds)
    channel = grpc.secure_channel(f"{FOYER_HOST}:443", composite)

    # The request protobuf: bhwm with single string field b (field 1) = group_id
    request_bytes = encode_protobuf_string(1, group_id)

    # Method path from the reverse engineering
    method = "/google.wirelessaccess.accesspoints.v2.UtilityService/RebootGroupWhenUpdateReady"

    # Make the raw unary-unary call
    call = channel.unary_unary(
        method,
        request_serializer=lambda x: x,  # Already bytes
        response_deserializer=lambda x: x,  # Return raw bytes
    )

    try:
        response = call(request_bytes, timeout=30)
        logging.info(f"gRPC reboot response: {response.hex() if response else 'empty'}")
        channel.close()
        return True
    except grpc.RpcError as e:
        logging.error(f"gRPC error: code={e.code()}, details={e.details()}")
        channel.close()
        return False


def discover_groups_rest(access_token):
    """Discover WiFi networks via REST API."""
    status, data = foyer_rest_get(access_token, "/groups?prettyPrint=false")
    if status == 200 and isinstance(data, dict):
        groups = data.get("groups", [])
        result = []
        for g in groups:
            aps = g.get("accessPoints", [])
            ap_names = []
            for ap in aps:
                name = ap.get("accessPointSettings", {}).get("accessPointOtherSettings", {}).get("apName", "")
                model = ap.get("accessPointProperties", {}).get("hardwareInfo", {}).get("hardwareType", "")
                ap_names.append(f"{name or 'unnamed'} ({model})" if model else (name or "unnamed"))
            gs = g.get("groupSettings", {})
            network_name = gs.get("lanSettings", {}).get("networkName", "") or gs.get("name", "") or "Unknown"
            result.append({
                "system_id": g.get("id", ""),
                "name": network_name,
                "num_aps": len(aps),
                "access_points": ap_names,
            })
        return result
    logging.debug(f"REST groups response: {status} {str(data)[:200]}")
    return None


def discover_groups_grpc(access_token):
    """Discover WiFi networks via gRPC GroupsService."""
    import grpc
    logging.info("Trying gRPC groups discovery...")
    creds = grpc.access_token_call_credentials(access_token)
    ssl = grpc.ssl_channel_credentials()
    composite = grpc.composite_channel_credentials(ssl, creds)
    channel = grpc.secure_channel(f"{FOYER_HOST}:443", composite)

    # Try known method paths for listing groups
    methods = [
        "/google.wirelessaccess.accesspoints.v2.GroupsService/GetGroups",
        "/google.wirelessaccess.accesspoints.v2.GroupService/ListGroups",
        "/google.internal.home.foyer.v1.StructuresService/GetHomeGraph",
    ]
    for method in methods:
        call = channel.unary_unary(method,
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )
        try:
            # Empty request (or minimal)
            resp = call(b"", timeout=15)
            logging.info(f"gRPC {method}: got {len(resp)} bytes")
            # For GetHomeGraph, we can't easily parse, but at least we know it works
            channel.close()
            return resp
        except grpc.RpcError as e:
            logging.debug(f"gRPC {method}: {e.code()} {e.details()}")
    channel.close()
    return None


def restart_network(access_token, group_id):
    """Try all methods to restart the network."""
    # Method 1: REST POST to /v2/groups/{id}/reboot
    logging.info("Method 1: REST reboot...")
    status, data = foyer_rest_post(access_token, f"/groups/{group_id}/reboot?prettyPrint=false")
    if status == 200:
        state = data.get("operation", {}).get("operationState", "") if isinstance(data, dict) else ""
        logging.info(f"REST reboot response: {data}")
        if state == "CREATED":
            logging.info("REST reboot accepted! Network will restart.")
            return True

    # Method 2: gRPC UtilityService.RebootGroupWhenUpdateReady
    logging.info("Method 2: gRPC reboot...")
    if grpc_reboot(access_token, group_id):
        return True

    # Method 3: REST with different auth — try issuetoken flow
    logging.info("Method 3: Trying alternative auth for REST...")
    try:
        import requests
        # Try using access token directly to get an api token
        r = requests.post("https://oauthaccountmanager.googleapis.com/v1/issuetoken", headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }, data=(
            "app_id=com.google.OnHub"
            "&client_id=586698244315-vc96jg3mn4nap78iir799fc2ll3rk18s.apps.googleusercontent.com"
            "&hl=en-US&lib_ver=3.3&response_type=token"
            "&scope=https%3A//www.googleapis.com/auth/accesspoints"
            "%20https%3A//www.googleapis.com/auth/clouddevices"
        ), timeout=30)
        api_data = r.json()
        api_token = api_data.get("token")
        if api_token:
            logging.info("Got API token via issuetoken! Trying REST reboot with it...")
            status2, data2 = foyer_rest_post(api_token, f"/groups/{group_id}/reboot?prettyPrint=false")
            logging.info(f"REST reboot with API token: HTTP {status2}")
            if status2 == 200:
                return True
        else:
            logging.debug(f"issuetoken: {api_data}")
    except Exception as e:
        logging.debug(f"issuetoken attempt: {e}")

    logging.error("All reboot methods failed.")
    return False


def run_speed_test(access_token, group_id):
    """Run a WAN speed test via the same REST API and return results."""
    import requests
    logging.info("Starting WAN speed test...")

    # Start the speed test
    status, data = foyer_rest_post(access_token, f"/groups/{group_id}/wanSpeedTest?prettyPrint=false")
    if status != 200 or not isinstance(data, dict):
        logging.error(f"Speed test start failed: HTTP {status}")
        return None

    operation_id = data.get("operation", {}).get("operationId", "")
    if not operation_id:
        logging.error(f"No operation ID returned: {data}")
        return None

    logging.info(f"Speed test started (operation: {operation_id[:30]}...)")

    # Poll for completion
    for _ in range(24):  # 2 minutes max
        time.sleep(5)
        op_status, op_data = foyer_rest_get(access_token, f"/operations/{operation_id}?prettyPrint=false")
        if op_status == 200 and isinstance(op_data, dict):
            state = op_data.get("operationState", "")
            logging.debug(f"Speed test state: {state}")
            if state == "DONE":
                break
        else:
            logging.debug(f"Poll: HTTP {op_status}")

    # Get results
    res_status, res_data = foyer_rest_get(access_token, f"/groups/{group_id}/speedTestResults?prettyPrint=false&maxResultCount=1")
    if res_status == 200 and isinstance(res_data, dict):
        results = res_data.get("speedTestResults", [])
        if results:
            r = results[0]
            down = r.get("downloadSpeedMbps", "?")
            up = r.get("uploadSpeedMbps", "?")
            timestamp = r.get("timestamp", "")
            logging.info(f"Speed test results: ↓ {down} Mbps / ↑ {up} Mbps")
            return {"download_mbps": down, "upload_mbps": up, "timestamp": timestamp}

    logging.warning("Could not retrieve speed test results.")
    return None


def verify_internet(timeout=180):
    logging.info(f"Waiting for internet ({timeout}s timeout)...")
    time.sleep(20)
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(5)
            s.connect(("8.8.8.8", 53)); s.close()
            logging.info(f"Internet back after ~{int(time.time()-start)}s.")
            return True
        except: time.sleep(5)
    logging.warning("Timeout.")
    return False


# ─── Commands ──────────────────────────────────────────────────────────────────

def cmd_setup(args):
    print(BANNER)
    config = load_config()
    master_token = config.get("master_token")
    email = config.get("email", "")

    if master_token and args.force:
        print("  Existing config found.")
        reuse = input("  Reuse saved master token? [Y/n] ").strip().lower()
        if reuse == "n": master_token = None

    if not master_token:
        email = input("  Google account email (WiFi owner): ").strip()
        if not email: print("  ✗ Required."); sys.exit(1)
        master_token = get_master_token_interactive(email)
        if not master_token: print("  ✗ Failed."); sys.exit(1)

    config["master_token"] = master_token
    config["email"] = email
    save_config(config)
    print(f"  ✓ Master token saved.\n")

    # Get access token
    print("  Authenticating to Google...")
    access_token = get_access_token(master_token, email)
    if not access_token:
        print("  ✗ Could not get access token.")
        sys.exit(1)
    print("  ✓ Authenticated.\n")

    # Discover networks via REST
    print("  Discovering WiFi networks...\n")
    systems = discover_groups_rest(access_token)

    if not systems:
        # Try gRPC discovery
        logging.info("REST discovery failed, trying gRPC...")
        grpc_resp = discover_groups_grpc(access_token)
        if grpc_resp:
            print(f"  Got gRPC response ({len(grpc_resp)} bytes) but can't parse groups from it.")
            print("  You can manually enter your group ID.")
            group_id = input("  Group ID (or Enter to skip): ").strip()
            if group_id:
                config["system_id"] = group_id
                config["system_name"] = "Manual"
                config["reboot_time"] = config.get("reboot_time", "03:00")
                config["created"] = datetime.now().isoformat()
                save_config(config)
                print(f"\n  ✓ Configured with manual group ID.")
                return
        print("  ✗ No networks found.")
        sys.exit(1)

    for i, s in enumerate(systems):
        print(f"    [{i+1}] \033[1m{s['name']}\033[0m")
        print(f"        {s['num_aps']} access point(s):")
        for ap in s["access_points"]:
            print(f"          • {ap}")
        print()

    if len(systems) == 1:
        selected = 0
        print(f"  Auto-selected: {systems[0]['name']}")
    else:
        sel = input("  Which network? ").strip()
        try: selected = int(sel) - 1
        except ValueError: print("  ✗ Invalid."); sys.exit(1)

    target = systems[selected]
    config.update({
        "system_id": target["system_id"],
        "system_name": target["name"],
        "num_aps": target["num_aps"],
        "reboot_time": config.get("reboot_time", "03:00"),
        "created": datetime.now().isoformat(),
    })
    save_config(config)
    print(f"\n  ✓ '{target['name']}' configured ({target['num_aps']} APs)")
    print(f"  ✓ Saved to {CONFIG_FILE}")
    print(f"\n  Next: nest-rebooter test && nest-rebooter install\n")


def cmd_reboot(args):
    config = load_config()
    mt, email, sid = config.get("master_token"), config.get("email"), config.get("system_id")
    if not all([mt, email, sid]):
        logging.error("Not configured. Run 'nest-rebooter setup'.")
        sys.exit(1)

    dry = getattr(args, "dry_run", False)
    logging.info(f"{'[DRY RUN] ' if dry else ''}Network: {config.get('system_name','?')}")

    logging.info("Authenticating...")
    access_token = get_access_token(mt, email)
    if not access_token:
        logging.error("Auth failed. Run 'nest-rebooter setup --force'.")
        sys.exit(1)
    logging.info("Auth OK.")

    if dry:
        logging.info("[DRY RUN] Would restart network. Auth is working!")
        return

    if not restart_network(access_token, sid):
        logging.error("Restart failed.")
        sys.exit(1)

    verify_internet()

    # Speed test after reboot (wait for network to stabilize)
    speedtest_delay = config.get("speedtest_delay_minutes", 10)
    logging.info(f"Waiting {speedtest_delay} minutes for network to stabilize before speed test...")
    time.sleep(speedtest_delay * 60)

    logging.info("Re-authenticating for speed test...")
    access_token = get_access_token(mt, email)
    if access_token:
        result = run_speed_test(access_token, sid)
        if result:
            config["last_speed_test"] = result
    else:
        logging.warning("Re-auth failed, skipping speed test.")

    config["last_reboot"] = datetime.now().isoformat()
    save_config(config)
    logging.info("Network restart complete.")


def cmd_test(args):
    print(BANNER)
    print("  Auth test (no reboot)...\n")
    args.dry_run = True
    cmd_reboot(args)
    print("\n  ✓ All good.\n")


def cmd_speedtest(args):
    """Run a speed test right now."""
    config = load_config()
    mt, email, sid = config.get("master_token"), config.get("email"), config.get("system_id")
    if not all([mt, email, sid]):
        logging.error("Not configured. Run 'nest-rebooter setup'.")
        sys.exit(1)

    logging.info("Authenticating...")
    access_token = get_access_token(mt, email)
    if not access_token:
        logging.error("Auth failed.")
        sys.exit(1)

    result = run_speed_test(access_token, sid)
    if result:
        print(f"\n  ✓ Speed test: ↓ {result['download_mbps']} Mbps / ↑ {result['upload_mbps']} Mbps\n")
        config["last_speed_test"] = result
        save_config(config)
    else:
        print("\n  ✗ Speed test failed.\n")


def cmd_install(args):
    config = load_config()
    t = config.get("reboot_time", "03:00")
    script, python = os.path.abspath(__file__), sys.executable
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    (SYSTEMD_DIR / SYSTEMD_SERVICE).write_text(f"[Unit]\nDescription=Nest WiFi Restart\nAfter=network-online.target\nWants=network-online.target\n[Service]\nType=oneshot\nExecStart={python} {script} reboot\nWorkingDirectory={APP_DIR}\nEnvironment=PYTHONUNBUFFERED=1\n[Install]\nWantedBy=default.target\n")
    (SYSTEMD_DIR / SYSTEMD_TIMER).write_text(f"[Unit]\nDescription=Nest WiFi Daily Reboot\n[Timer]\nOnCalendar=*-*-* {t}:00\nPersistent=true\nRandomizedDelaySec=60\n[Install]\nWantedBy=timers.target\n")
    print(BANNER)
    print(f"  Installing timer for {t} daily...\n")
    user = os.environ.get("USER", "")
    for cmd in [["systemctl","--user","daemon-reload"],["systemctl","--user","enable",SYSTEMD_TIMER],["systemctl","--user","start",SYSTEMD_TIMER]]:
        try: subprocess.run(cmd, check=True, capture_output=True, text=True); print(f"  ✓ {' '.join(cmd)}")
        except Exception as e: print(f"  ✗ {' '.join(cmd)}: {e}")
    if user:
        try: subprocess.run(["sudo","loginctl","enable-linger",user], check=True, capture_output=True, text=True); print(f"  ✓ loginctl enable-linger {user}")
        except: print(f"  ⚠ Run: sudo loginctl enable-linger {user}")
    print(f"\n  ✓ Network restarts at {t} daily.\n  Logs: {LOG_FILE}\n")


def cmd_uninstall(args):
    print(BANNER)
    for cmd in [["systemctl","--user","stop",SYSTEMD_TIMER],["systemctl","--user","disable",SYSTEMD_TIMER]]:
        try: subprocess.run(cmd, check=True, capture_output=True, text=True); print(f"  ✓ {' '.join(cmd)}")
        except: pass
    for f in [SYSTEMD_SERVICE, SYSTEMD_TIMER]:
        p = SYSTEMD_DIR / f
        if p.exists(): p.unlink(); print(f"  ✓ Removed {p}")
    subprocess.run(["systemctl","--user","daemon-reload"], capture_output=True)
    print(f"\n  ✓ Timer removed.\n")


def cmd_status(args):
    print(BANNER)
    c = load_config()
    if not c: print("  Not configured. Run: nest-rebooter setup\n"); return
    print(f"  Network:     {c.get('system_name','-')}")
    print(f"  APs:         {c.get('num_aps','-')}")
    print(f"  Reboot at:   {c.get('reboot_time','-')}")
    print(f"  Last reboot: {c.get('last_reboot','never')}")
    print(f"  Token:       {'✓' if c.get('master_token') else '✗'}")
    st = c.get("last_speed_test")
    if st:
        print(f"  Last speed:  ↓ {st.get('download_mbps','?')} / ↑ {st.get('upload_mbps','?')} Mbps")
    try:
        r = subprocess.run(["systemctl","--user","is-active",SYSTEMD_TIMER], capture_output=True, text=True)
        active = r.stdout.strip() == "active"
    except: active = False
    print(f"  Timer:       {'✓ active' if active else '✗ not installed'}")
    print()


def main():
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("-v","--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sp = sub.add_parser("setup"); sp.add_argument("--force", action="store_true")
    sp = sub.add_parser("reboot"); sp.add_argument("--dry-run", action="store_true")
    sub.add_parser("test"); sub.add_parser("speedtest"); sub.add_parser("install"); sub.add_parser("uninstall"); sub.add_parser("status")
    args = parser.parse_args()
    if not args.command: parser.print_help(); sys.exit(0)
    setup_logging(args.verbose)
    for mod in ["gpsoauth","requests","grpc"]:
        try: __import__(mod)
        except ImportError: print(f"\n  pip install {'grpcio' if mod=='grpc' else mod}\n"); sys.exit(1)
    cmds = {"setup":cmd_setup,"reboot":cmd_reboot,"test":cmd_test,"speedtest":cmd_speedtest,"install":cmd_install,"uninstall":cmd_uninstall,"status":cmd_status}
    try: cmds[args.command](args)
    except KeyboardInterrupt: print("\n  Cancelled."); sys.exit(130)
    except Exception as e: logging.error(f"Error: {e}", exc_info=True); sys.exit(1)

if __name__ == "__main__":
    main()
