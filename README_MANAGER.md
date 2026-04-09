# SSH over Tor Manager (GUI + CLI)

This is a second README for the manager tools that were added, without changing the original `README.md`.

## Project summary

This project helps you run SSH privately through a Tor hidden service (`.onion`) so your server is reachable without exposing a public IP or opening router port-forwarding.

In short, it gives you tools to install/configure SSH over Tor, control services (SSH/Tor/Fail2Ban), change security settings, and quickly view your onion connection details in either a GUI or CLI manager.

## Files included

- `ssh_tor_manager_gui.py` - Desktop GUI app (recommended for most users)
- `ssh_tor_manager.sh` - Interactive terminal app
- `setup_ssh_over_tor.sh` - One-shot setup script

---

## Which one should I use?

- Use **GUI** if you want a clean visual app with buttons/toggles and speed monitor.
- Use **CLI manager** if you are on a server or no desktop environment.
- Use **one-shot script** for quick first-time provisioning only.

You do **not** need to use all three.

---

## 1) GUI app (recommended)

### Features

- Toggle ON/OFF and restart:
  - SSH
  - Tor
  - Fail2Ban
- Edit and apply:
  - SSH custom port
  - Password authentication
  - Connection limit
- View current information:
  - Hidden service directory
  - Onion address
  - Connection command
- SSH speed panel:
  - Live local SSH handshake latency (`ms`)
  - Quality indicator + progress bar

### Requirements

- Debian/Ubuntu/Parrot/Kali-like Linux
- `systemd` (`systemctl`)
- Root privileges for service/config management
- Python 3 + Tkinter

Install Tkinter if needed:

```bash
sudo apt update
sudo apt install -y python3-tk
```

### Run

```bash
sudo python3 ssh_tor_manager_gui.py
```

---

## 2) Interactive terminal manager

### What it now handles automatically

- Verifies required components and installs missing ones when needed:
  - `openssh-server`
  - `tor`
  - `torsocks`
  - `fail2ban` (when enabled)
- Can remove components and related managed config blocks:
  - Fail2Ban only
  - Tor only
  - both Tor and Fail2Ban

### Run

```bash
chmod +x ssh_tor_manager.sh
sudo ./ssh_tor_manager.sh
```

### Quick options

```bash
sudo ./ssh_tor_manager.sh --show
sudo ./ssh_tor_manager.sh --apply
sudo ./ssh_tor_manager.sh --wizard
sudo ./ssh_tor_manager.sh --remove
```

- `--show`: displays current saved/live configuration, onion hostname, and connection command.
- `--apply`: re-applies saved configuration to SSH/Tor/Fail2Ban immediately.
- `--wizard`: runs guided prompts once, then applies the selected settings.
- `--remove`: opens remove menu to uninstall Tor, Fail2Ban, or both (with managed config cleanup).

### Remove menu (from interactive mode)

From the app menu choose:
- `8) Remove tor/fail2ban components`

Then select:
- remove Fail2Ban only
- remove Tor only
- remove both

---

## 3) One-shot setup script

### What it now handles automatically

- Checks required packages and installs missing ones instead of stopping:
  - `tor`
  - `openssh-server`
  - `torsocks`
  - `fail2ban` (only if `--enable-fail2ban yes`)
- Treats helper tools (`micro`, `pv`) as optional and continues even if they are unavailable.

### Run

```bash
chmod +x setup_ssh_over_tor.sh
sudo ./setup_ssh_over_tor.sh
```

### Example with options

```bash
sudo ./setup_ssh_over_tor.sh \
  --ssh-port 22 \
  --allow-password-auth yes \
  --enable-fail2ban yes \
  --hidden-service-dir /var/lib/tor/ssh_service \
  --hostname-output-file ./ssh.txt
```

---

## Notes

- Scripts/apps use managed config blocks and are designed to be re-run.
- Onion hostname appears after Tor hidden service is initialized.
- The displayed "SSH speed" is latency to local SSH handshake (not internet bandwidth).
- If you change SSH port in the manager, connection command updates accordingly.
- If a component is missing, current scripts prefer installing requirements automatically to continue setup.

---

## Troubleshooting

- If GUI cannot control services, make sure you launched it with `sudo`.
- If onion is not shown yet, wait a few seconds and click refresh.
- If SSH config fails validation, check:
  ```bash
  sudo sshd -t
  ```
- Service logs:
  ```bash
  sudo journalctl -u ssh -n 100 --no-pager
  sudo journalctl -u tor -n 100 --no-pager
  sudo journalctl -u fail2ban -n 100 --no-pager
  ```
