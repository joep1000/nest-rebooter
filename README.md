# Nest-Rebooter

**Automate a scheduled network restart for Google Nest WiFi / Google WiFi using only software.**

This python script uses the same Google cloud API the Google Home app uses to restart your mesh network (router and all access points) on a schedule you can set yourself. It is mainly built with a headless Linux machine in mind, but should also work on any Linux setup.

---

## The problem

A lot of Google Nest WiFi and Google WiFi network users suffer from a well-known internet speed bug somewhere in the firmware of the Nest Wifi. For a lot of users, over the course of 1–3 days, speeds drop from near Gigabit speeds to very inconsistent speeds. The only fix seems to be either manually restarting the network or performing a Network Speed Test in the Google Home app. Google has known about this bug since at least [2021](https://www.googlenestcommunity.com/t5/Nest-Wifi/Scheduled-network-restart/m-p/58814) but has never added a scheduled restart feature, also because they have stopped all support for Nest/Google Wifi.

This script aims to fix this bug by allowing you to restart your network remotely and automaticaly at any point in time from any (always-on) linux machine.

## What it aims to do practically

- **A Full network restart** — the script will reboot the main router and all mesh points connected to it simultaneously. This action is identical to tapping "Restart network" in the Google Home app
- **Scheduled via systemd** — It automatically creates a systemd action that runs automatically at 3:00 AM (this is configurable in the script itself though, you can also set it to any other time)
- **Post-reboot speed test** — after the reboot, just to be safe, it runs a WAN speed test. This automatically occurs 10 minutes after the restart.
- **One-time browser setup** — You have to authenticate once via a browser cookie to make it work. Once that's done, it's fully automated.

## How the script works

I have painstakingly checked and reverse-engineered the Google Home APK to see how the API calls the Google/Nest Wifi to do various commands. Interestingly, the Google Home app doesn't use the router's local API to restart the network. It instead calls a **cloud REST API** on `googlehomefoyer-pa.googleapis.com`:

```
POST /v2/groups/{group_id}/reboot
```

This in return sends a `base.reboot` command to every access point in the mesh. The auth chain works as follows:

```
Browser cookie (one-time)
    ↓  gpsoauth.exchange_token()
Master token (long-lived, stored locally)
    ↓  gpsoauth.perform_oauth()
Access token (refreshed automatically each run)
    ↓
Google Home Foyer REST API → full network restart
```

## Requirements

- Python 3.8+
- A Linux machine on the same network as your router and AP/mesh points
- The Google account that **owns** the WiFi network (home owner in Google Home, otherwise this script won't work)
- A browser (any device) for one-time authentication

## Installation

### Quick install

```bash
git clone https://github.com/joep1000/nest-rebooter.git
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
   - Now Open DevTools (press F12 in most browsers) and go to → Application → Cookies. Keep this open for the rest of this process.
   - Log in with the WiFi owner's Google account
   - Click "I agree" (the page may hang after you click agree. This is normal behavior.)
   - You should now see a cookie showing up in your DevTools window. Open this cookie tab and find the  `oauth_token` value. Copy the `oauth_token` value (starts with `oauth2_4/`) to your clipboard. 
3. **Paste the oauth token** into the terminal The `oauth_token` cookie is **single-use** and expires very fast within minutes, so make sure to be quick with this.

The script will then authenticate, discover your network setup, and save the configuration. By default, it will run automatically at 3 AM and restart your network.

## Usage

```bash
# Test that everything works (this won't reboot your network, but will simply test if the endpoint works.)
nest-rebooter test

# Restart the network immediately
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

To change the reboot time, edit this file: `~/.config/nest-rebooter/config.json`:

```json
{
  "reboot_time": "04:30"
}
```

After that, reinstall the timer:

```bash
nest-rebooter install
```

## A succesful reboot looks like this:

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

The script is confirming working at my home setup, which has the following devices:
- Nest Wifi Router + Nest Wifi Points
- Google Wifi Points
- Mixed Nest Wifi + Google Wifi mesh networks

It should also also work with the Nest Wifi Pro (Wi-Fi 6E) because it seems to use the same Foyer API, but I haven't tested this.

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
- The `oauth_token` cookie is **single-use** and expires very fast within minutes
- You must paste it into the terminal immediately after copying
- Make sure the value starts with `oauth2_4/`
- Repeat the browser flow to get a fresh one

### "No networks found"
- Make sure you logged in with the **home owner's** Google account, not a shared member
- The account must be listed as an admin/owner in Google Home → Settings → Household

### "Auth failed"
- The master token can expire (rare), or the google endpoint changed (unlikely but possible, Google has stopped updating Google Wifi a while ago.) If the master token expired, you can run `nest-rebooter setup --force` to get a new cookie and repeat the process from the setup above.

### Timer doesn't fire when not logged in
```bash
sudo loginctl enable-linger $USER
```
This allows systemd user timers to run without an active login session. It's essential for headless machines.

## Security

- Your Google password is **never stored** by this script. The only thing stored on your machine is a master token derived from the one-time browser cookie you obtained during the setup process.
- The master token is stored in `~/.config/nest-rebooter/config.json` with `600` permissions (owner read/write only.)
- The reboot command goes through Google's cloud API (same as the Google Home app.) It is not a local exploit.

## Background

This project was born from [this 270-reply thread](https://www.googlenestcommunity.com/t5/Nest-Wifi/Scheduled-network-restart/m-p/58814) on the Google Nest Community forum, where users have been requesting a scheduled restart feature since December 2021. Google just never added it, so I said fine... I'll do it myself

The auth flow was pieced together from:
- [glocaltokens](https://github.com/leikoilja/glocaltokens) — Google Home local auth token library
- [googlewifi-api](https://github.com/djtimca/googlewifi-api) — Google WiFi API wrapper (source of the Foyer REST endpoints)
- [GHLocalApi](https://rithvikvibhu.github.io/GHLocalApi/) — Unofficial Google Home local API docs
- [julianpitt's gist](https://gist.github.com/julianpitt/94774e74d36a46f9ec10ddce13cfd423) — EmbeddedSetup cookie → master token method
- Reverse engineering of the Google Home Android APK (decompiled via JADX) which revealed the cloud gRPC `UtilityService.RebootGroupWhenUpdateReady` path

## License

MIT — do whatever you want with it.

## Contributing

Issues and PRs are always welcome. If you have a Nest Wifi Pro and can test if it works with this setup too, that would be especially helpful.
