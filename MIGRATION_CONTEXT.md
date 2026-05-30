Goal:
Migrate MikroTik RouterOS Telegram polling bot to Python aiogram 3 webhook bot running on Ubuntu Lightsail VPS.

Old behavior:
- Allowed Telegram chat IDs: 366911247, 666356121
- Commands:
  - /tvon: run RouterOS script stbon, remove scheduler stb-timer-autooff, reply "TV is on; nothing scheduled"
  - /tvoff: run RouterOS script stboff, remove scheduler stb-timer-autooff, reply "TV is off; nothing scheduled"
  - /status: inspect firewall filter rules with comment="block-stb"; if all enabled then TV is off, otherwise TV is on. Also check scheduler stb-timer-autooff and include scheduled off time if present.
  - /timerXX: run stbon, remove existing scheduler, create one-shot RouterOS scheduler stb-timer-autooff to run stboff later.
- Old polling/getUpdates must be removed.
- New bot must use Telegram webhook via aiogram 3.
- Telegram token must live only on VPS, not on MikroTik.
- RouterOS actions should be executed over SSH from VPS.
- Keep actual auto-off timer on MikroTik scheduler, not only in Python, so it survives VPS/bot restart.

Security requirements:
- Use .env for secrets.
- Use Telegram webhook secret_token.
- Validate allowed chat IDs.
- Validate /timer input strictly.
- Do not expose MikroTik SSH to public internet if avoidable; assume private VPN/WireGuard or private reachable address.
- Use a dedicated low-privilege MikroTik user.

Deliverables:
- app.py
- requirements.txt
- .env.example
- systemd unit example
- nginx reverse proxy example
- README.md with install/test/deploy instructions
