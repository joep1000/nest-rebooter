# 🌙 Nest Rebooter

**Automated scheduled network restarts for Google Nest WiFi / Google WiFi — no smart plugs, no Chrome extensions, just software.**

Uses the same cloud API as the Google Home app to restart your entire mesh network (router + all access points) on a schedule. Built for headless Linux machines.

---

## The Problem

Google Nest WiFi and Google WiFi networks suffer from a well-known speed degradation bug. Over the course of 1–3 days, speeds drop from 300+ Mbps to under 10 Mbps. The only fix is manually restarting the network in the Google Home app. Google has known about this since at least [2021](https://www.googlenestcommunity.com/t5/Nest-Wifi/Scheduled-network-restart/m-p/58814) and has never added a scheduled restart feature.

This script fixes that.

## What It Does

- **Full network restart** — reboots the router and all mesh points simultaneously, identical to tapping "Restart network" in the Google Home app
- **Scheduled via systemd** — runs automatically at 3:00 AM (configurable)
- **Post-reboot speed test** — runs a WAN speed test 10 minutes after restart and logs the results
- **Headless-friendly** — designed for Raspberry Pi, NAS, Mac Mini, or any always-on Linux box on your network
- **One-time browser setup** — authenticate once via a browser cookie, then it's fully automated

## How It Works

The Google Home app doesn't use the router's local API to restart the network. It calls a **cloud REST API** on `googlehomefoyer-pa.googleapis.com`:

```
POST /v2/groups/{group_id}/reboot
```

This sends a `base.reboot` command to every access point in the mesh. The auth chain:

```
Browser cookie (one-time)
    ↓  gpsoauth.exchange_token()
Master token (long-lived, stored locally)
    ↓  gpsoauth.perform_oauth()
Access token (refreshed automatically each run)
    ↓
Google Home Foyer REST API → full network restart
```

This was discovered by reverse-engineering the Google Home Android APK — specifically the `UtilityService.RebootGroupWhenUpdateReady` gRPC path and the Foyer v2 REST endpoints.

## Requirements

- Python 3.8+
- A Linux machine on the same network (Raspberry Pi, NAS, etc.)
- The Google account that **owns** the WiFi network (home owner in Google Home)
- A browser (any device) for one-time authentication

## Installation

### Quick install

```bash
git clone https://github.com/YOUR_USERNAME/nest-rebooter.git
cd nest-rebooter
bash install.sh
```

### Manual install

```bash
pip install gpsoauth requests grpcio
# Download nest_rebooter.py and run it directly
```

## Setup (5 minutes, one-time)

```bash
nest-rebooter setup
```

The script will guide you through:

1. **Enter the WiFi owner's email** (the Google account that owns the home)
2. **Get the OAuth cookie:**
   - Open `https://accounts.google.com/EmbeddedSetup` in any browser
   - Log in with the WiFi owner's Google account
   - Click "I agree" (page may hang — that's fine)
   - Open DevTools (F12) → Application → Cookies
   - Copy the `oauth_token` value (starts with `oauth2_4/`)
3. **Paste it** into the terminal

The script will authenticate, discover your network, and save the configuration.

## Usage

```bash
# Test that everything works (no reboot)
nest-rebooter test

# Restart the network right now
nest-rebooter reboot

# Run a speed test
nest-rebooter speedtest

# Install the daily 3 AM timer
nest-rebooter install

# Check status and next scheduled reboot
nest-rebooter status

# Remove the timer
nest-rebooter uninstall
```

### Changing the reboot time

Edit `~/.config/nest-rebooter/config.json`:

```json
{
  "reboot_time": "04:30"
}
```

Then reinstall the timer:

```bash
nest-rebooter install
```

## What a Successful Reboot Looks Like

```
$ nest-rebooter reboot
2026-03-09 19:20:10 [INFO] Network: MyNetwork
2026-03-09 19:20:10 [INFO] Authenticating...
2026-03-09 19:20:10 [INFO] Auth OK.
2026-03-09 19:20:10 [INFO] Method 1: REST reboot...
2026-03-09 19:20:11 [INFO] REST reboot accepted! Network will restart.
2026-03-09 19:20:11 [INFO] Waiting for internet (180s timeout)...
2026-03-09 19:20:56 [INFO] Internet back after ~24s.
2026-03-09 19:30:56 [INFO] Starting WAN speed test...
2026-03-09 19:31:30 [INFO] Speed test results: ↓ 342 Mbps / ↑ 28 Mbps
2026-03-09 19:31:30 [INFO] Network restart complete.
```

Each access point receives its own `base.reboot` command with `code: SUCCESS`.

## Supported Devices

Tested with:
- Nest Wifi Router + Nest Wifi Points (Wi-Fi 5)
- Google Wifi Points (Wi-Fi 5)
- Mixed Nest Wifi + Google Wifi mesh networks

Should also work with:
- Nest Wifi Pro (Wi-Fi 6E) — uses the same Foyer API

**Not compatible with:**
- Nest Wifi Pro cannot mesh with older Nest/Google Wifi devices (Google limitation)

## Configuration

Config is stored at `~/.config/nest-rebooter/config.json`:

| Key | Description |
|-----|-------------|
| `master_token` | Long-lived Google auth token |
| `email` | Google account email |
| `system_id` | WiFi network group ID |
| `system_name` | Network name |
| `reboot_time` | Daily reboot time (HH:MM) |
| `speedtest_delay_minutes` | Wait time before speed test (default: 10) |
| `last_reboot` | Timestamp of last reboot |
| `last_speed_test` | Last speed test results |

## Troubleshooting

### "Could not get master token"
- The `oauth_token` cookie is **single-use** and expires within minutes
- You must paste it into the terminal immediately after copying
- Make sure the value starts with `oauth2_4/`
- Repeat the browser flow to get a fresh one

### "No networks found"
- Make sure you logged in with the **home owner's** Google account, not a shared member
- The account must be listed as an admin/owner in Google Home → Settings → Household

### "Auth failed"
- Master tokens can expire (rare). Run `nest-rebooter setup --force` and get a new cookie

### Timer doesn't fire when not logged in
```bash
sudo loginctl enable-linger $USER
```
This allows systemd user timers to run without an active login session — essential for headless machines.

## Security

- Your Google password is **never stored** — only a master token derived from a one-time browser cookie
- The master token is stored in `~/.config/nest-rebooter/config.json` with `600` permissions (owner read/write only)
- The reboot command goes through Google's cloud API (same as the Google Home app), not a local exploit

## Background

This project was born from [this 270-reply thread](https://www.googlenestcommunity.com/t5/Nest-Wifi/Scheduled-network-restart/m-p/58814) on the Google Nest Community forum, where users have been requesting a scheduled restart feature since December 2021. Google never added it.

The auth flow was pieced together from:
- [glocaltokens](https://github.com/leikoilja/glocaltokens) — Google Home local auth token library
- [googlewifi-api](https://github.com/djtimca/googlewifi-api) — Google WiFi API wrapper (source of the Foyer REST endpoints)
- [GHLocalApi](https://rithvikvibhu.github.io/GHLocalApi/) — Unofficial Google Home local API docs
- [julianpitt's gist](https://gist.github.com/julianpitt/94774e74d36a46f9ec10ddce13cfd423) — EmbeddedSetup cookie → master token method
- Reverse engineering of the Google Home Android APK (decompiled via JADX) which revealed the cloud gRPC `UtilityService.RebootGroupWhenUpdateReady` path

## License

MIT — do whatever you want with it.

## Contributing

Issues and PRs welcome. If you have a Nest Wifi Pro and can test, that would be especially helpful.
