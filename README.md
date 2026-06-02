# Telegram STB Control Bot

Small Telegram webhook bot for controlling a TV/STB through MikroTik RouterOS scripts.

The bot runs on a VPS, receives Telegram updates through an HTTPS webhook, and executes RouterOS commands over SSH. Auto-off timers are stored on the MikroTik scheduler so they survive bot or VPS restarts.

## Features

- `/tvon` turns the TV on and clears any pending auto-off scheduler.
- `/tvoff` turns the TV off and clears any pending auto-off scheduler.
- `/status` reads the STB address-list entry state and the pending auto-off scheduler.
- `/timer30`, `/timer60`, etc. turns the TV on and schedules it to turn off later.
- Reply keyboard buttons are shown for `TV On`, `TV Off`, `Status`, `5 min`, `15 min`, `30 min`, `45 min`, `60 min`, `90 min`, and `Help`.
- Allowed Telegram chat IDs are enforced.
- Telegram webhook `secret_token` is enforced.
- `DRY_RUN=true` logs RouterOS commands without executing them.

## RouterOS Assumptions

The bot expects these RouterOS scripts to exist:

```routeros
/system script run stbon
/system script run stboff
```

Status is inferred from firewall address-list entries with:

```routeros
comment="block-stb"
```

If all matching entries are enabled, the TV is treated as off. If any matching entry is disabled, the TV is treated as on.

Use a dedicated low-privilege MikroTik user for SSH access. The VPS should reach MikroTik over a private path when possible, such as WireGuard or a private network.

## Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Fill in:

```dotenv
BOT_TOKEN=123456:telegram-token

BASE_WEBHOOK_URL=https://bot.example.com
WEBHOOK_PATH=/tg-stb-webhook
WEBHOOK_SECRET=change-this-long-random-secret

ALLOWED_CHAT_IDS=123456788,123456789

MIKROTIK_HOST=10.0.0.1
MIKROTIK_PORT=22
MIKROTIK_USER=bot
MIKROTIK_SSH_KEY=/opt/tg-stb-bot/.ssh/id_ed25519

SCHEDULER_NAME=stb-timer-autooff
ROUTER_TIMEZONE=Asia/Tbilisi
ROUTEROS_DATE_FORMAT=iso
DRY_RUN=true
```

`ROUTER_TIMEZONE` must match the router's clock timezone. Use an IANA timezone name such as `Asia/Tbilisi`, `Europe/Berlin`, or `UTC`. `/timerXX` uses this timezone when creating RouterOS scheduler `start-date` and `start-time`.

`ROUTEROS_DATE_FORMAT=iso` uses `YYYY-MM-DD`, which is usually right for RouterOS v7. Use `legacy` if your scheduler expects dates like `jul/04/2025`.

Keep `DRY_RUN=true` until webhook delivery and command logging look correct.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m py_compile app/app.py
python app/app.py
```

The app listens on `127.0.0.1:8080`.

In dry-run mode, RouterOS commands are logged like:

```text
DRY_RUN: would execute RouterOS command: /system script run stbon
```

## Telegram Webhook

On startup, the bot registers:

```text
{BASE_WEBHOOK_URL}{WEBHOOK_PATH}
```

For example:

```text
https://bot.example.com/tg-stb-webhook
```

Telegram must be able to reach this URL over public HTTPS. Put nginx or another reverse proxy in front of the local aiohttp app.

## nginx Example

```nginx
server {
    listen 80;
    server_name bot.example.com;

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name bot.example.com;

    ssl_certificate /etc/letsencrypt/live/bot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bot.example.com/privkey.pem;

    location /tg-stb-webhook {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Reload nginx after testing the config:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## systemd Example

Create `/etc/systemd/system/tg-stb-control.service`:

```ini
[Unit]
Description=Telegram STB Control control
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tg-stb-control
Group=tg-stb-control
WorkingDirectory=/opt/tg-stb-control
EnvironmentFile=/opt/tg-stb-control/.env
ExecStart=/opt/tg-stb-control/.venv/bin/python /opt/tg-stb-control/app/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-stb-control
sudo journalctl -u tg-stb-control -f
```

## Deploy Sketch

One possible layout on Ubuntu:

```bash
sudo useradd --system --create-home --home-dir /opt/tg-stb-control tg-stb-control
sudo mkdir -p /opt/tg-stb-control
sudo chown tg-stb-control:tg-stb-control /opt/tg-stb-control
```

Copy this repo to `/opt/tg-stb-control`, then as that user:

```bash
python3 -m venv /opt/tg-stb-control/.venv
/opt/tg-stb-control/.venv/bin/pip install -r /opt/tg-stb-control/requirements.txt
cp /opt/tg-stb-control/.env.example /opt/tg-stb-control/.env
```

Place the MikroTik SSH private key at the path configured by `MIKROTIK_SSH_KEY`, then lock down permissions:

```bash
chmod 700 /opt/tg-stb-control/.ssh
chmod 600 /opt/tg-stb-control/.ssh/id_ed25519
chmod 600 /opt/tg-stb-control/.env
```

## Testing Checklist

1. Set `DRY_RUN=true`.
2. Start the service and watch logs.
3. Send `/start` from an allowed chat.
4. Send `/tvon`, `/tvoff`, `/status`, and `/timer30`.
5. Confirm the logs show the RouterOS commands the bot would execute.
6. Confirm denied chats receive `Access denied`.
7. Set `DRY_RUN=false`.
8. Restart the service and test against MikroTik.

## Useful Commands

```bash
sudo systemctl status tg-stb-control
sudo journalctl -u tg-stb-control -f
sudo systemctl restart tg-stb-control
```

Check Telegram webhook state:

```bash
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

## Security Notes

- Do not commit `.env` or SSH keys.
- Use a long random `WEBHOOK_SECRET`.
- Restrict `ALLOWED_CHAT_IDS`.
- Prefer private MikroTik reachability over public SSH exposure.
- Keep the MikroTik SSH user limited to only the commands this bot needs.

## MikroTik preparation

The bot controls the STB through SSH by running predefined MikroTik scripts. The bot should not contain complex firewall logic; MikroTik should expose a small, stable command surface: `stbon`, `stboff`, and one temporary scheduler entry for auto-off timers.

### 1. Create or verify the STB firewall address-list entry

The scripts below expect an existing address-list entry with the comment `block-stb`.

Check that it exists:

```routeros
/ip firewall address-list print detail where comment="block-stb"
````

Example entry:

```routeros
/ip firewall address-list add list=blocked-devices address=192.168.88.50 comment="block-stb" disabled=no
```

Make sure a firewall rule actually blocks traffic from this list:

```routeros
/ip firewall filter print detail where src-address-list="blocked-devices"
```

Example blocking rule:

```routeros
/ip firewall filter add chain=forward src-address-list=blocked-devices action=drop comment="block devices in blocked-devices list"
```

### 2. Create STB control scripts

Create two MikroTik scripts. `stbon` disables the blocking entry, and `stboff` enables it.

```routeros
/system script add name=stbon policy=read,write,test source="/ip firewall address-list set [find comment=\"block-stb\"] disabled=yes; :log info \"STB ON\""

/system script add name=stboff policy=read,write,test source="/ip firewall address-list set [find comment=\"block-stb\"] disabled=no; :log info \"STB OFF\""
```

If the scripts already exist, update them instead:

```routeros
/system script set [find name=stbon] source="/ip firewall address-list set [find comment=\"block-stb\"] disabled=yes; :log info \"STB ON\""

/system script set [find name=stboff] source="/ip firewall address-list set [find comment=\"block-stb\"] disabled=no; :log info \"STB OFF\""
```

Verify and test:

```routeros
/system script print detail where name~"stb"
/system script run stboff
/system script run stbon
/log print where message~"STB"
```

### 3. Create a dedicated MikroTik user for the bot

Do not use the router admin account from the VPS. Create a dedicated group and user with only the permissions required to run scripts and manage scheduler entries.

```routeros
/user group add name=stb-bot policy=ssh,read,write,test
/user add name=stb-bot group=stb-bot disabled=no
```

Avoid giving this user unnecessary permissions such as:

```text
ftp,reboot,policy,password,sensitive,romon,sniff
```

### 4. Add the VPS SSH public key to MikroTik

On the VPS, generate an SSH key for the bot if it does not already exist:

```bash
sudo -u tg-stb-control mkdir -p /opt/tg-stb-control/.ssh

sudo -u tg-stb-control ssh-keygen \
  -t ed25519 \
  -f /opt/tg-stb-control/.ssh/id_ed25519_mikrotik \
  -N ""
```

Print the public key:

```bash
sudo cat /opt/tg-stb-control/.ssh/id_ed25519_mikrotik.pub
```

Upload or paste this public key to MikroTik and import it for the `stb-bot` user:

```routeros
/user ssh-keys import user=stb-bot public-key-file=id_ed25519_mikrotik.pub
```

Then test SSH access from the VPS:

```bash
sudo -u tg-stb-control ssh \
  -i /opt/tg-stb-control/.ssh/id_ed25519_mikrotik \
  -p 22 \
  stb-bot@example.com \
  '/system identity print'
```

Test script execution:

```bash
sudo -u tg-stb-control ssh \
  -i /opt/tg-stb-control/.ssh/id_ed25519_mikrotik \
  -p 22 \
  stb-bot@example.com \
  '/system script run stboff'
```

```bash
sudo -u tg-stb-control ssh \
  -i /opt/tg-stb-control/.ssh/id_ed25519_mikrotik \
  -p 22 \
  stb-bot@example.com \
  '/system script run stbon'
```

### 5. Restrict SSH access on MikroTik

Check the SSH service:

```routeros
/ip service print detail where name=ssh
```

Use the required SSH port:

```routeros
/ip service set ssh port=22
```

If the VPS has a static public IP, restrict SSH access to that IP only:

```routeros
/ip service set ssh address=203.0.113.10/32
```

Replace `203.0.113.10` with the real public IP address of the VPS.

### 6. Prepare SSH known_hosts on the VPS

The systemd service cannot answer the first interactive SSH host-key prompt. Initialize `known_hosts` for the service user:

```bash
sudo -u tg-stb-control mkdir -p /opt/tg-stb-control/.ssh

sudo -u tg-stb-control ssh-keyscan -p 22 example.com \
  | sudo -u tg-stb-control tee -a /opt/tg-stb-control/.ssh/known_hosts
```

Set safe permissions:

```bash
sudo chown -R tg-stb-control:tg-stb-control /opt/tg-stb-control/.ssh
sudo chmod 700 /opt/tg-stb-control/.ssh
sudo chmod 600 /opt/tg-stb-control/.ssh/id_ed25519_mikrotik
sudo chmod 644 /opt/tg-stb-control/.ssh/id_ed25519_mikrotik.pub
sudo chmod 644 /opt/tg-stb-control/.ssh/known_hosts
```

### 7. Check scheduler cleanup

The bot may create a temporary scheduler entry for auto-off functionality, for example `stb-timer-autooff`.

Check for stale entries:

```routeros
/system scheduler print detail where name~"stb"
```

Remove stale test timers if needed:

```routeros
/system scheduler remove [find name="stb-timer-autooff"]
```

The bot should remove an existing timer before creating a new one.

### 8. Configure MikroTik clock and timezone

The auto-off scheduler depends on MikroTik local time. Check the current clock configuration:

```routeros
/system clock print
/system clock get time-zone-name
```

Set the expected timezone:

```routeros
/system clock set time-zone-name=Asia/Tbilisi
```

Enable NTP:

```routeros
/system ntp client set enabled=yes
/system ntp client servers add address=pool.ntp.org
```

Verify:

```routeros
/system clock print
```

### 9. Final MikroTik validation checklist

Run these commands before enabling the bot in production:

```routeros
/system script print detail where name=stbon
/system script print detail where name=stboff
/ip firewall address-list print detail where comment="block-stb"
/ip firewall filter print detail where comment~"block"
/user print detail where name=stb-bot
/user group print detail where name=stb-bot
/ip service print detail where name=ssh
/system scheduler print detail where name~"stb"
/system clock print
```

Then run an end-to-end test from the VPS:

```bash
sudo -u tg-stb-control ssh \
  -i /opt/tg-stb-control/.ssh/id_ed25519_mikrotik \
  -p 22 \
  stb-bot@example.com \
  '/system script run stboff; /delay 2; /system script run stbon'
```

Required MikroTik-side state:

```text
[ ] Working firewall rule that blocks the STB when the address-list entry is enabled
[ ] Address-list entry with comment="block-stb"
[ ] /system script stbon
[ ] /system script stboff
[ ] Dedicated MikroTik user stb-bot
[ ] Dedicated low-privilege group with ssh,read,write,test
[ ] VPS SSH public key added for stb-bot
[ ] SSH service reachable from the VPS
[ ] SSH restricted to the VPS public IP if possible
[ ] MikroTik clock, timezone, and NTP configured
[ ] No stale stb-timer-autooff scheduler entries
[ ] Manual SSH command from the VPS can run stbon and stboff
```
