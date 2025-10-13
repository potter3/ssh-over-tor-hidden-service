


<img width="1536" height="1024" alt="ChatGPT Image Oct 13, 2025, 02_01_03 PM" src="https://github.com/user-attachments/assets/b90eb4c8-ec11-41ee-bcb7-e5cf6cee7bc4" />





# ssh-over-tor-hidden-service
Secure guide for setting up SSH over a Tor Hidden Service on Parrot OS, Kali Linux, and Debian-based systems with Fail2Ban, Anonsurf, and latency testing, providing full anonymity and global access without port forwarding.




---

## 🔒 Overview
This guide explains how to host an SSH server **entirely inside the Tor network** using **Parrot OS**, accessible securely from **anywhere in the world** even behind NAT or dynamic IPs.  
It also includes optional hardening with **Fail2Ban**, **Anonsurf**, and latency testing.


This setup allows you to:
- Run SSH entirely inside the Tor network (`.onion`)
- Access it from any device or network (no port forwarding)
- Stay anonymous with Anonsurf (This is integrated with Parrot os)
- Protect logins using Fail2Ban
- Measure latency and throughput easily



---

## 🧱 Server Setup (Parrot OS, Kali or any Debian-based systems)

### 1️⃣ Install dependencies
```bash
sudo apt update
sudo apt install -y tor openssh-server fail2ban torsocks micro pv
#This is to Install Fail2ban
```


### 2️⃣ Backup current configs (safe practice)
```bash
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak
sudo cp /etc/tor/torrc /etc/tor/torrc.bak
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.conf.bak || true
```


### 3️⃣ Configure SSH
Configure SSH to listen on your custom port and only on localhost. By doing this, you won't be able to use your IP address to SSH. Doing this will increase privacy and security.
```bash
sudo micro /etc/ssh/sshd_config
```

🍂 If Micro is not installed


```bash
sudo apt install micro
```


✏️ Uncomment "remove the '#' " or add these lines (replace the port number with your custom port if you have one )

```nginx
Port 22
ListenAddress 127.0.0.1
# this_is_the_localhost_ssh_will_listen_to
PermitRootLogin no
PasswordAuthentication yes
PubkeyAuthentication yes
UseDNS no
GSSAPIAuthentication no
```
Save (Ctrl+S) and quit (Ctrl+Q).


🔥Validate and restart:

```bash
sudo sshd -t   # no output = OK
sudo systemctl restart ssh
sudo ss -tlnp | grep ssh
# Expect output showing 127.0.0.1:<your_custom_ssh_port if you don't have one, it will be 22 by default >
```

🍂If you don’t have Tor installed (quick install commands)
Debian-family (Parrot/Kali/Ubuntu)
```bash
sudo apt update
sudo apt install -y tor
# optionally make sure tor user exists and directory permissions are right
sudo mkdir -p /var/lib/tor/ssh_service
sudo chown -R debian-tor:debian-tor /var/lib/tor/ssh_service
sudo systemctl enable --now tor
sudo systemctl status tor --no-pager  #this will check tor status
```


### 4️⃣ Configure Tor Hidden Service
Edit the Tor configuration:
```bash
sudo micro /etc/tor/torrc
```
✏️ Scroll to the bottom and add:
```nginx
# SSH Hidden Service
HiddenServiceDir /var/lib/tor/ssh_service/
HiddenServicePort 22 127.0.0.1:22 
#<if you have a custom port for SSH, change that 22 "the last one" to your custom port number>
```
Save (Ctrl+S) and quit (Ctrl+Q).

🛻 Save & quit. Then set ownership (important on Debian family):
```bash
# ensure directory ownership (Debian/Ubuntu/Parrot/Kali)
sudo mkdir -p /var/lib/tor/ssh_service
sudo chown -R debian-tor:debian-tor /var/lib/tor/ssh_service || sudo chown -R toranon:toranon /var/lib/tor/ssh_service || true
```

🔥Restart Tor:
```bash
sudo systemctl restart tor
sudo systemctl status tor --no-pager
```

### 5️⃣ Check Tor hidden service exists
```bash
sudo cat /var/lib/tor/ssh_service/hostname
# copy the printed string -> this is <your_onion>.onion
sudo cat /var/lib/tor/ssh_service/hostname > ssh.txt
# This line is used to write the hostname in a file, "the hostname is too long" 

```

### 6️⃣ Configure Fail2Ban (explain below & commands)

**What Fail2Ban does (short)**

Fail2Ban watches logs (e.g. /var/log/auth.log) for failed login attempts; when a source hits maxretry within findtime, it bans that IP (iptables or nftables) for bantime. With Tor hidden service, attackers must know your onion to try brute force Fail2Ban adds an extra layer if they do.

Create local jail:
```bash
sudo micro /etc/fail2ban/jail.local
```
✏️paste
```ini
[DEFAULT]
bantime  = 3600 # will lock for 1hr
findtime = 600
maxretry = 3  # enter wrong password 3 times 

[sshd]
enabled  = true
port     = 22
filter   = sshd
logpath  = /var/log/auth.log
maxretry = 3
```

🔥Save & quit, then enable/start:
```bash
sudo systemctl enable --now fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status          # list all jails
sudo fail2ban-client status sshd     # You should see a banned IP in the Banned IP list
```
---
Simulate failed logins to test

From another machine (or WSL), attempt wrong password repeatedly (replace with your onion and username)



🏁 Enable services at boot
```bash
sudo systemctl enable ssh
sudo systemctl enable tor
```
- Check Tor hidden service exists
```bash
sudo ls -l /var/lib/tor/ssh_service/
# private_key is inside — keep it secret

```

- Test SSH locally via Tor (from server itself)
```bash
sudo apt install -y torsocks  # if not present
torsocks ssh -p 22 <USERNAME>@<your_onion>.onion exit
#username is that of your Linux and your_onion is what you found in your hostname
```

If it connects asks for password it’s working.....

---
### Installing WSL (Debian) on Windows / Connecting to SSH over Tor (WSL) 
Connecting to an SSH server over Tor is different from regular SSH.
Instead of using a public IP address, you connect to a private .onion domain that exists entirely inside the Tor network.
This keeps both your client and server completely anonymous, no IPs are exposed, and no port forwarding is needed.

To make things easier on Windows, we’ll use WSL (Windows Subsystem for Linux) with the Debian distribution.
WSL lets you run a full Linux environment directly inside Windows, no virtual machine, no dual boot, and it’s simpler to set up than using CMD or PowerShell for Tor connections.


### Option 1: Download from Microsoft store

<img width="1488" height="1163" alt="image" src="https://github.com/user-attachments/assets/23004987-787b-4b8c-9dd4-ea0c81df028a" />


### Option 2: 
1. Open PowerShell as Administrator
Press **Win + X** → select Windows PowerShell (Admin) or Terminal (Admin).

2. Run this command to install WSL with Debian
``` bash
wsl --install -d Debian
```
3. Restart your computer when prompted.
4. **Launch Debian**

After restart, open the Start Menu → search for Debian → run it.
It will initialize the system and ask you to create a Linux username and password.

5. Update packages (inside Debian terminal):
```bash
sudo apt update && sudo apt upgrade -y
```
6. Install required tools
```bash
sudo apt install -y torsocks openssh-client
sudo apt install ssh
```

### Once that’s done, your Windows system is ready to connect securely to your .onion SSH service using:

```bash
torsocks ssh -p 22 <username>@<your_onion>.onion
```



### 🔒 Security Notes

SSH listens only on localhost → invisible to LAN/WAN.

Tor Hidden Service → hides your IP completely.

Fail2Ban → limits brute-force attempts.

Use strong passwords or SSH keys.

Works anywhere — no need for port forwarding or static IP.




### NB: There are still issues with fail2ban i will fix it in the nearest future or find alternatives

