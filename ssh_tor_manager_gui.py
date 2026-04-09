#!/usr/bin/env python3
"""
Desktop GUI for managing SSH over Tor services/settings.

Features:
- Start/stop/restart Tor, SSH, Fail2Ban
- View current SSH/Tor configuration and onion address
- Change SSH port, password authentication, and connection limit
- Live SSH response-speed indicator (localhost handshake latency)
"""

import os
import shutil
import socket
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


STATE_FILE = Path("/etc/ssh-over-tor-manager.env")
SSH_CONFIG = Path("/etc/ssh/sshd_config")
TOR_CONFIG = Path("/etc/tor/torrc")
F2B_CONFIG = Path("/etc/fail2ban/jail.local")
BEGIN_MARKER = "# BEGIN SSH_OVER_TOR_MANAGED"
END_MARKER = "# END SSH_OVER_TOR_MANAGED"


def run_command(args):
    return subprocess.run(args, capture_output=True, text=True, check=False)


def service_exists(unit_name):
    result = run_command(["systemctl", "status", unit_name])
    return result.returncode != 4


def detect_service_unit(candidates):
    for candidate in candidates:
        if service_exists(candidate):
            return candidate
    return candidates[0]


def get_service_state(unit_name):
    result = run_command(["systemctl", "is-active", unit_name])
    if result.returncode == 0:
        return "active"
    state = result.stdout.strip() or result.stderr.strip() or "unknown"
    return state


def parse_key_value_file(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def parse_sshd_config(path):
    config = {
        "port": "22",
        "password_auth": "yes",
        "max_startups": "10",
        "max_auth_tries": "3",
        "login_grace_time": "30",
    }
    if not path.exists():
        return config

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        key = parts[0].lower()
        value = " ".join(parts[1:])
        if key == "port":
            config["port"] = value
        elif key == "passwordauthentication":
            config["password_auth"] = value.lower()
        elif key == "maxstartups":
            config["max_startups"] = value.split(":", 1)[0]
        elif key == "maxauthtries":
            config["max_auth_tries"] = value
        elif key == "logingracetime":
            config["login_grace_time"] = value
    return config


def parse_hidden_service_dir(path):
    hidden_dir = "/var/lib/tor/ssh_service"
    if not path.exists():
        return hidden_dir
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "hiddenservicedir":
            hidden_dir = parts[1].rstrip("/")
    return hidden_dir


def read_onion_hostname(hidden_dir):
    hostname_path = Path(hidden_dir) / "hostname"
    if hostname_path.exists():
        return hostname_path.read_text(encoding="utf-8").strip()
    return ""


def write_managed_block(path, content):
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    filtered = []
    in_block = False
    for line in lines:
        if line == BEGIN_MARKER:
            in_block = True
            continue
        if line == END_MARKER:
            in_block = False
            continue
        if not in_block:
            filtered.append(line)

    if filtered and filtered[-1].strip():
        filtered.append("")
    filtered.append(BEGIN_MARKER)
    filtered.extend(content.strip().splitlines())
    filtered.append(END_MARKER)

    path.write_text("\n".join(filtered) + "\n", encoding="utf-8")


def ensure_backup(source_path):
    backup_path = source_path.with_suffix(source_path.suffix + ".bak")
    if source_path.exists() and not backup_path.exists():
        backup_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")


def save_state(values):
    lines = [
        "# Managed by ssh_tor_manager_gui.py",
        f"SSH_PORT={values['SSH_PORT']}",
        f"ALLOW_PASSWORD_AUTH={values['ALLOW_PASSWORD_AUTH']}",
        f"MAX_STARTUPS={values['MAX_STARTUPS']}",
        f"HIDDEN_SERVICE_DIR={values['HIDDEN_SERVICE_DIR']}",
        f"ONION_HOSTNAME={values['ONION_HOSTNAME']}",
        f"LAST_UPDATED_UTC={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
    ]
    STATE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(STATE_FILE, 0o600)


class ManagerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SSH over Tor Control Center")
        self.geometry("980x690")
        self.minsize(880, 620)
        self.configure(bg="#121317")

        self.ssh_unit = detect_service_unit(["ssh", "sshd"])
        self.tor_unit = detect_service_unit(["tor", "tor@default"])
        self.f2b_unit = "fail2ban"

        self.ssh_state = tk.StringVar(value="unknown")
        self.tor_state = tk.StringVar(value="unknown")
        self.f2b_state = tk.StringVar(value="unknown")

        self.port_var = tk.StringVar(value="22")
        self.password_auth_var = tk.BooleanVar(value=True)
        self.max_conn_var = tk.StringVar(value="10")
        self.max_auth_tries_var = tk.StringVar(value="3")
        self.login_grace_var = tk.StringVar(value="30")
        self.hidden_service_dir_var = tk.StringVar(value="/var/lib/tor/ssh_service")
        self.onion_var = tk.StringVar(value="(not available)")
        self.connect_cmd_var = tk.StringVar(value="torsocks ssh -p 22 <username>@<onion>.onion")
        self.speed_var = tk.StringVar(value="Pending first sample...")
        self.speed_quality_var = tk.StringVar(value="Quality: unknown")
        self.status_message_var = tk.StringVar(value="Ready.")

        self.style = ttk.Style(self)
        self._configure_style()
        self._build_layout()
        self.refresh_all()
        self.after(4000, self.sample_ssh_speed)

    def _configure_style(self):
        self.style.theme_use("clam")
        self.style.configure("App.TFrame", background="#121317")
        self.style.configure("Card.TFrame", background="#191b21")
        self.style.configure(
            "Header.TLabel",
            background="#121317",
            foreground="#e6edf3",
            font=("Segoe UI", 20, "bold"),
        )
        self.style.configure(
            "SubHeader.TLabel",
            background="#121317",
            foreground="#7d8590",
            font=("Segoe UI", 10),
        )
        self.style.configure(
            "CardTitle.TLabel",
            background="#191b21",
            foreground="#e6edf3",
            font=("Segoe UI", 12, "bold"),
        )
        self.style.configure(
            "Body.TLabel",
            background="#191b21",
            foreground="#c9d1d9",
            font=("Segoe UI", 10),
        )
        self.style.configure(
            "Value.TLabel",
            background="#191b21",
            foreground="#58a6ff",
            font=("Consolas", 11, "bold"),
        )
        self.style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        self.style.configure("TEntry", fieldbackground="#0d1117", foreground="#c9d1d9")
        self.style.configure("StatusBar.TLabel", background="#121317", foreground="#8b949e", font=("Segoe UI", 10))
        self.style.configure(
            "Switch.TCheckbutton",
            background="#191b21",
            foreground="#c9d1d9",
            font=("Segoe UI", 10),
        )
        self.style.map(
            "Switch.TCheckbutton",
            foreground=[("disabled", "#8b949e"), ("active", "#e6edf3"), ("!disabled", "#c9d1d9")],
            background=[("active", "#191b21"), ("!disabled", "#191b21")],
        )
        self.style.configure(
            "Speed.Horizontal.TProgressbar",
            troughcolor="#0d1117",
            background="#2ea043",
            bordercolor="#0d1117",
            lightcolor="#2ea043",
            darkcolor="#2ea043",
        )

    def _service_row(self, parent, name, state_var, on_toggle, on_restart):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill=tk.X, pady=5)
        ttk.Label(row, text=f"{name}:", style="Body.TLabel", width=11).pack(side=tk.LEFT, padx=(0, 4))
        state_label = ttk.Label(row, textvariable=state_var, style="Value.TLabel", width=12)
        state_label.pack(side=tk.LEFT)
        ttk.Button(row, text="Toggle ON/OFF", command=on_toggle, style="Accent.TButton").pack(side=tk.LEFT, padx=6)
        ttk.Button(row, text="Restart", command=on_restart).pack(side=tk.LEFT)
        return state_label

    def _build_layout(self):
        root = ttk.Frame(self, style="App.TFrame", padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="SSH over Tor Control Center", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(
            root,
            text="Manage services, SSH settings, and live SSH response speed.",
            style="SubHeader.TLabel",
        ).pack(anchor=tk.W, pady=(0, 12))

        top = ttk.Frame(root, style="App.TFrame")
        top.pack(fill=tk.X)

        services = ttk.Frame(top, style="Card.TFrame", padding=12)
        services.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(services, text="Service Controls", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))
        self.ssh_state_label = self._service_row(
            services,
            "SSH",
            self.ssh_state,
            lambda: self.toggle_service(self.ssh_unit),
            lambda: self.restart_service(self.ssh_unit),
        )
        self.tor_state_label = self._service_row(
            services,
            "Tor",
            self.tor_state,
            lambda: self.toggle_service(self.tor_unit),
            lambda: self.restart_service(self.tor_unit),
        )
        self.f2b_state_label = self._service_row(
            services,
            "Fail2Ban",
            self.f2b_state,
            lambda: self.toggle_service(self.f2b_unit),
            lambda: self.restart_service(self.f2b_unit),
        )
        ttk.Button(services, text="Refresh Status", command=self.refresh_service_status, style="Accent.TButton").pack(
            anchor=tk.W, pady=(10, 0)
        )

        speed = ttk.Frame(top, style="Card.TFrame", padding=12)
        speed.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        ttk.Label(speed, text="SSH Speed Monitor", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))
        ttk.Label(speed, text="Local SSH handshake latency:", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(speed, textvariable=self.speed_var, style="Value.TLabel").pack(anchor=tk.W, pady=(2, 8))
        self.speed_meter = ttk.Progressbar(speed, style="Speed.Horizontal.TProgressbar", maximum=100)
        self.speed_meter.pack(fill=tk.X)
        ttk.Label(speed, textvariable=self.speed_quality_var, style="Body.TLabel").pack(anchor=tk.W, pady=(8, 0))
        ttk.Button(speed, text="Measure Now", command=self.sample_ssh_speed, style="Accent.TButton").pack(
            anchor=tk.W, pady=(12, 0)
        )

        bottom = ttk.Frame(root, style="App.TFrame")
        bottom.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        config = ttk.Frame(bottom, style="Card.TFrame", padding=12)
        config.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(config, text="Configuration", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        ttk.Label(config, text="SSH Port:", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(config, textvariable=self.port_var, width=20).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(config, text="Max Connections:", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(config, textvariable=self.max_conn_var, width=20).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(config, text="Max Auth Tries:", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Label(config, textvariable=self.max_auth_tries_var, style="Value.TLabel").grid(
            row=3, column=1, sticky="w", pady=4
        )

        ttk.Label(config, text="Login Grace Time:", style="Body.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Label(config, textvariable=self.login_grace_var, style="Value.TLabel").grid(
            row=4, column=1, sticky="w", pady=4
        )

        ttk.Checkbutton(
            config,
            text="Enable Password Authentication",
            variable=self.password_auth_var,
            style="Switch.TCheckbutton",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 4))

        ttk.Button(config, text="Apply SSH/Tor Settings", command=self.apply_settings, style="Accent.TButton").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )

        info = ttk.Frame(bottom, style="Card.TFrame", padding=12)
        info.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        ttk.Label(info, text="Current Endpoint", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))
        ttk.Label(info, text="Hidden Service Directory", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(info, textvariable=self.hidden_service_dir_var, style="Value.TLabel").pack(anchor=tk.W, pady=(2, 10))
        ttk.Label(info, text="Onion Address", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(info, textvariable=self.onion_var, style="Value.TLabel").pack(anchor=tk.W, pady=(2, 10))
        ttk.Label(info, text="Connection Command", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(info, textvariable=self.connect_cmd_var, style="Value.TLabel", wraplength=430, justify=tk.LEFT).pack(
            anchor=tk.W, pady=(2, 10)
        )

        actions = ttk.Frame(info, style="Card.TFrame")
        actions.pack(anchor=tk.W, pady=(6, 0))
        ttk.Button(actions, text="Refresh All", command=self.refresh_all, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(actions, text="Exit", command=self.destroy).pack(side=tk.LEFT, padx=8)

        ttk.Label(root, textvariable=self.status_message_var, style="StatusBar.TLabel").pack(
            fill=tk.X, side=tk.BOTTOM, pady=(10, 0)
        )

    def set_status_message(self, message):
        self.status_message_var.set(message)

    def _state_color(self, state_value):
        state = state_value.strip().lower()
        if state == "active":
            return "#2ea043"
        if state in {"inactive", "failed"}:
            return "#f85149"
        return "#d29922"

    def _update_service_indicator_colors(self):
        self.ssh_state_label.configure(foreground=self._state_color(self.ssh_state.get()))
        self.tor_state_label.configure(foreground=self._state_color(self.tor_state.get()))
        self.f2b_state_label.configure(foreground=self._state_color(self.f2b_state.get()))

    def refresh_service_status(self):
        self.ssh_state.set(get_service_state(self.ssh_unit))
        self.tor_state.set(get_service_state(self.tor_unit))
        self.f2b_state.set(get_service_state(self.f2b_unit))
        self._update_service_indicator_colors()

    def toggle_service(self, unit_name):
        current = get_service_state(unit_name)
        action = "stop" if current == "active" else "start"
        result = run_command(["systemctl", action, unit_name])
        if result.returncode != 0:
            messagebox.showerror("Service Error", result.stderr.strip() or result.stdout.strip() or "Unknown error")
            return
        self.set_status_message(f"{unit_name}: {action} command executed.")
        self.refresh_service_status()

    def restart_service(self, unit_name):
        result = run_command(["systemctl", "restart", unit_name])
        if result.returncode != 0:
            messagebox.showerror("Service Error", result.stderr.strip() or result.stdout.strip() or "Unknown error")
            return
        self.set_status_message(f"{unit_name}: restarted.")
        self.refresh_service_status()

    def refresh_config(self):
        state_values = parse_key_value_file(STATE_FILE)
        ssh_values = parse_sshd_config(SSH_CONFIG)
        hidden_dir = parse_hidden_service_dir(TOR_CONFIG)
        onion = read_onion_hostname(hidden_dir)

        port = state_values.get("SSH_PORT", ssh_values["port"])
        max_conn = state_values.get("MAX_STARTUPS", ssh_values["max_startups"])
        pw_auth = state_values.get("ALLOW_PASSWORD_AUTH", ssh_values["password_auth"]).lower() == "yes"

        self.port_var.set(port)
        self.max_conn_var.set(max_conn)
        self.password_auth_var.set(pw_auth)
        self.max_auth_tries_var.set(ssh_values["max_auth_tries"])
        self.login_grace_var.set(ssh_values["login_grace_time"])
        self.hidden_service_dir_var.set(hidden_dir)

        if onion:
            self.onion_var.set(onion)
            self.connect_cmd_var.set(f"torsocks ssh -p {port} <username>@{onion}")
        else:
            self.onion_var.set("(not available yet)")
            self.connect_cmd_var.set(f"torsocks ssh -p {port} <username>@<onion>.onion")

    def refresh_all(self):
        self.refresh_service_status()
        self.refresh_config()
        self.sample_ssh_speed()
        self.set_status_message("Status refreshed.")

    def _validate_settings(self):
        if not self.port_var.get().isdigit():
            raise ValueError("SSH port must be numeric.")
        port = int(self.port_var.get())
        if port < 1 or port > 65535:
            raise ValueError("SSH port must be between 1 and 65535.")

        if not self.max_conn_var.get().isdigit() or int(self.max_conn_var.get()) < 1:
            raise ValueError("Max connections must be a positive integer.")
        return port, int(self.max_conn_var.get())

    def apply_settings(self):
        try:
            port, max_conn = self._validate_settings()
        except ValueError as exc:
            messagebox.showerror("Invalid Configuration", str(exc))
            return

        pw_auth = "yes" if self.password_auth_var.get() else "no"
        hidden_dir = parse_hidden_service_dir(TOR_CONFIG)

        ensure_backup(SSH_CONFIG)
        ensure_backup(TOR_CONFIG)
        ensure_backup(F2B_CONFIG)

        ssh_block = "\n".join(
            [
                f"Port {port}",
                "ListenAddress 127.0.0.1",
                "PermitRootLogin no",
                f"PasswordAuthentication {pw_auth}",
                "PubkeyAuthentication yes",
                "UseDNS no",
                "GSSAPIAuthentication no",
                "MaxAuthTries 3",
                "LoginGraceTime 30",
                f"MaxStartups {max_conn}",
                f"MaxSessions {max_conn}",
            ]
        )
        tor_block = "\n".join(
            [
                "# SSH Hidden Service",
                f"HiddenServiceDir {hidden_dir.rstrip('/')}/",
                f"HiddenServicePort {port} 127.0.0.1:{port}",
            ]
        )
        f2b_block = "\n".join(
            [
                "[DEFAULT]",
                "bantime  = 3600",
                "findtime = 600",
                "maxretry = 3",
                "",
                "[sshd]",
                "enabled  = true",
                f"port     = {port}",
                "filter   = sshd",
                "logpath  = /var/log/auth.log",
                "maxretry = 3",
            ]
        )

        try:
            write_managed_block(SSH_CONFIG, ssh_block)
            write_managed_block(TOR_CONFIG, tor_block)
            write_managed_block(F2B_CONFIG, f2b_block)
        except OSError as exc:
            messagebox.showerror("Write Error", f"Failed writing config files:\n{exc}")
            return

        test_sshd = run_command(["sshd", "-t"])
        if test_sshd.returncode != 0:
            messagebox.showerror("Validation Error", test_sshd.stderr.strip() or "sshd -t failed.")
            return

        for unit in [self.ssh_unit, self.tor_unit, self.f2b_unit]:
            run_command(["systemctl", "restart", unit])
            run_command(["systemctl", "enable", unit])

        onion = read_onion_hostname(hidden_dir)
        save_state(
            {
                "SSH_PORT": str(port),
                "ALLOW_PASSWORD_AUTH": pw_auth,
                "MAX_STARTUPS": str(max_conn),
                "HIDDEN_SERVICE_DIR": hidden_dir,
                "ONION_HOSTNAME": onion,
            }
        )
        self.refresh_all()
        self.set_status_message("Settings applied successfully.")
        messagebox.showinfo("Success", "SSH/Tor settings updated and services restarted.")

    def sample_ssh_speed(self):
        # "Speed" here is measured as localhost SSH handshake latency.
        try:
            port = int(self.port_var.get())
        except ValueError:
            self.speed_var.set("Invalid port")
            self.speed_quality_var.set("Quality: unknown")
            self.speed_meter["value"] = 0
            self.after(5000, self.sample_ssh_speed)
            return

        start = time.perf_counter()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                sock.settimeout(2)
                _ = sock.recv(64)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.speed_var.set(f"{elapsed_ms:.1f} ms")

            if elapsed_ms <= 80:
                quality = "excellent"
                score = 100
            elif elapsed_ms <= 160:
                quality = "good"
                score = 75
            elif elapsed_ms <= 350:
                quality = "fair"
                score = 50
            else:
                quality = "slow"
                score = 25
            self.speed_quality_var.set(f"Quality: {quality}")
            self.speed_meter["value"] = score
        except OSError:
            self.speed_var.set("unreachable")
            self.speed_quality_var.set("Quality: no SSH response")
            self.speed_meter["value"] = 0

        self.after(5000, self.sample_ssh_speed)


def main():
    if os.geteuid() != 0:
        print("Run as root so the GUI can manage services and write system config files.")
        print("Example: sudo python3 ssh_tor_manager_gui.py")
        raise SystemExit(1)

    # Ensure required commands exist.
    for command in ["systemctl", "sshd"]:
        if shutil.which(command) is None:
            print(f"Missing required command: {command}")
            raise SystemExit(1)

    app = ManagerGUI()
    app.mainloop()

if __name__ == "__main__":
    main()
