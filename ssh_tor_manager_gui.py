#!/usr/bin/env python3
"""
SSH over Tor Control Center v2 (Polished GUI)

Features:
- Polished ON/OFF toggle switches for SSH, Tor, Fail2Ban
- Copy-to-clipboard buttons for onion and SSH command
- Embedded logs panel (journalctl output inside GUI)
- Light/Dark theme toggle
- Minimize-to-tray-like behavior (background helper window)
- SSH speed monitor (local SSH handshake latency)
- Install-or-skip prompts for missing SSH/Tor/Fail2Ban
"""

import os
import shutil
import socket
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


STATE_FILE = Path("/etc/ssh-over-tor-manager.env")
SSH_CONFIG = Path("/etc/ssh/sshd_config")
TOR_CONFIG = Path("/etc/tor/torrc")
F2B_CONFIG = Path("/etc/fail2ban/jail.local")
BEGIN_MARKER = "# BEGIN SSH_OVER_TOR_MANAGED"
END_MARKER = "# END SSH_OVER_TOR_MANAGED"

THEMES = {
    "dark": {
        "bg": "#111318",
        "card": "#1a1f29",
        "fg": "#e6edf3",
        "muted": "#8b949e",
        "accent": "#58a6ff",
        "good": "#2ea043",
        "bad": "#f85149",
        "warn": "#d29922",
        "entry_bg": "#0d1117",
        "entry_fg": "#c9d1d9",
        "log_bg": "#0d1117",
        "log_fg": "#c9d1d9",
    },
    "light": {
        "bg": "#f5f7fb",
        "card": "#ffffff",
        "fg": "#1f2328",
        "muted": "#59636e",
        "accent": "#0969da",
        "good": "#1a7f37",
        "bad": "#cf222e",
        "warn": "#9a6700",
        "entry_bg": "#ffffff",
        "entry_fg": "#1f2328",
        "log_bg": "#ffffff",
        "log_fg": "#1f2328",
    },
}


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
    return result.stdout.strip() or result.stderr.strip() or "unknown"


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


def ensure_backup(path):
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists() and not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def save_state(values):
    lines = [
        "# Managed by ssh_tor_manager_gui_v2.py",
        f"SSH_PORT={values['SSH_PORT']}",
        f"ALLOW_PASSWORD_AUTH={values['ALLOW_PASSWORD_AUTH']}",
        f"MAX_STARTUPS={values['MAX_STARTUPS']}",
        f"HIDDEN_SERVICE_DIR={values['HIDDEN_SERVICE_DIR']}",
        f"ONION_HOSTNAME={values['ONION_HOSTNAME']}",
        f"LAST_UPDATED_UTC={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
    ]
    STATE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(STATE_FILE, 0o600)


def clear_cached_onion_state():
    values = parse_key_value_file(STATE_FILE)
    values["ONION_HOSTNAME"] = ""
    if "HIDDEN_SERVICE_DIR" not in values or not values["HIDDEN_SERVICE_DIR"]:
        values["HIDDEN_SERVICE_DIR"] = "/var/lib/tor/ssh_service"
    if "SSH_PORT" not in values or not values["SSH_PORT"]:
        values["SSH_PORT"] = "22"
    if "ALLOW_PASSWORD_AUTH" not in values or not values["ALLOW_PASSWORD_AUTH"]:
        values["ALLOW_PASSWORD_AUTH"] = "yes"
    if "MAX_STARTUPS" not in values or not values["MAX_STARTUPS"]:
        values["MAX_STARTUPS"] = "10"
    save_state(
        {
            "SSH_PORT": values["SSH_PORT"],
            "ALLOW_PASSWORD_AUTH": values["ALLOW_PASSWORD_AUTH"],
            "MAX_STARTUPS": values["MAX_STARTUPS"],
            "HIDDEN_SERVICE_DIR": values["HIDDEN_SERVICE_DIR"],
            "ONION_HOSTNAME": "",
        }
    )


def remove_tor_hidden_service_data():
    values = parse_key_value_file(STATE_FILE)
    hidden_dir = values.get("HIDDEN_SERVICE_DIR", "/var/lib/tor/ssh_service").strip() or "/var/lib/tor/ssh_service"
    run_command(["rm", "-rf", hidden_dir])


def fetch_journal_logs(unit_name, lines):
    result = run_command(["journalctl", "-u", unit_name, "-n", str(lines), "--no-pager"])
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or "Unknown journalctl error."
        return False, err
    return True, result.stdout


class ManagerGUIV2(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SSH over Tor Control Center v2")
        self.geometry("1120x760")
        self.minsize(980, 680)

        self.ssh_unit = detect_service_unit(["ssh", "sshd"])
        self.tor_unit = detect_service_unit(["tor", "tor@default"])
        self.f2b_unit = "fail2ban"
        self.service_map = {
            "SSH": self.ssh_unit,
            "Tor": self.tor_unit,
            "Fail2Ban": self.f2b_unit,
        }
        self.component_packages = {
            "ssh": "openssh-server",
            "tor": "tor",
            "fail2ban": "fail2ban",
        }
        self.component_titles = {
            "ssh": "SSH (OpenSSH server)",
            "tor": "Tor",
            "fail2ban": "Fail2Ban",
        }
        self.component_status = {
            "ssh": False,
            "tor": False,
            "fail2ban": False,
        }
        self.skipped_components = set()
        self.apt_cache_updated = False

        self.theme_name = "dark"
        self.palette = THEMES[self.theme_name]
        self.style = ttk.Style(self)

        self.status_message_var = tk.StringVar(value="Ready.")

        self.port_var = tk.StringVar(value="22")
        self.max_conn_var = tk.StringVar(value="10")
        self.password_auth_var = tk.BooleanVar(value=True)
        self.max_auth_tries_var = tk.StringVar(value="3")
        self.login_grace_var = tk.StringVar(value="30")
        self.hidden_service_dir_var = tk.StringVar(value="/var/lib/tor/ssh_service")
        self.onion_var = tk.StringVar(value="(not available yet)")
        self.connect_cmd_var = tk.StringVar(value="torsocks ssh -p 22 <username>@<onion>.onion")
        self.endpoint_mode_var = tk.StringVar(value="Password authentication enabled")
        self.setup_install_update_var = tk.StringVar(value="sudo apt update")
        self.setup_install_tools_var = tk.StringVar(value="sudo apt install -y openssh-client torsocks")
        self.setup_keygen_var = tk.StringVar(value="ssh-keygen -t ed25519 -a 100 -f ~/.ssh/id_ed25519_tor")
        self.setup_copyid_var = tk.StringVar(
            value="torsocks ssh-copy-id -i ~/.ssh/id_ed25519_tor.pub -p 22 <USER>@<ONION>.onion"
        )
        self.setup_test_var = tk.StringVar(
            value="torsocks ssh -i ~/.ssh/id_ed25519_tor -p 22 <USER>@<ONION>.onion"
        )

        self.ssh_state = tk.StringVar(value="unknown")
        self.tor_state = tk.StringVar(value="unknown")
        self.f2b_state = tk.StringVar(value="unknown")

        self.ssh_toggle_var = tk.BooleanVar(value=False)
        self.tor_toggle_var = tk.BooleanVar(value=False)
        self.f2b_toggle_var = tk.BooleanVar(value=False)
        self.ssh_toggle_text = tk.StringVar(value="OFF")
        self.tor_toggle_text = tk.StringVar(value="OFF")
        self.f2b_toggle_text = tk.StringVar(value="OFF")
        self._syncing_toggles = False

        self.speed_var = tk.StringVar(value="Pending first sample...")
        self.speed_quality_var = tk.StringVar(value="Quality: unknown")

        self.log_service_var = tk.StringVar(value="SSH")
        self.log_lines_var = tk.StringVar(value="120")
        self.auto_logs_var = tk.BooleanVar(value=True)

        self.minimize_to_tray_var = tk.BooleanVar(value=True)
        self.tray_window = None

        self._configure_style()
        self._build_layout()
        self.apply_theme(self.theme_name)
        self.port_var.trace_add("write", lambda *_: self.update_endpoint_preview())
        self.password_auth_var.trace_add("write", lambda *_: self.update_endpoint_preview())
        self.refresh_all()

        self.after(5000, self._speed_tick)
        self.after(8000, self._logs_tick)

        self.bind("<Unmap>", self._on_unmap)
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)

    def _configure_style(self):
        p = self.palette
        self.style.theme_use("clam")
        self.style.configure("App.TFrame", background=p["bg"])
        self.style.configure("Card.TFrame", background=p["card"])
        self.style.configure("Header.TLabel", background=p["bg"], foreground=p["fg"], font=("Segoe UI", 21, "bold"))
        self.style.configure("SubHeader.TLabel", background=p["bg"], foreground=p["muted"], font=("Segoe UI", 10))
        self.style.configure("CardTitle.TLabel", background=p["card"], foreground=p["fg"], font=("Segoe UI", 12, "bold"))
        self.style.configure("Body.TLabel", background=p["card"], foreground=p["fg"], font=("Segoe UI", 10))
        self.style.configure(
            "Muted.TLabel", background=p["card"], foreground=p["muted"], font=("Segoe UI", 9)
        )
        self.style.configure(
            "Value.TLabel", background=p["card"], foreground=p["accent"], font=("Consolas", 11, "bold")
        )
        self.style.configure(
            "StatusBar.TLabel", background=p["bg"], foreground=p["muted"], font=("Segoe UI", 10)
        )
        self.style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        self.style.configure("TEntry", fieldbackground=p["entry_bg"], foreground=p["entry_fg"])
        self.style.configure("TCombobox", fieldbackground=p["entry_bg"], foreground=p["entry_fg"])
        self.style.configure(
            "Speed.Horizontal.TProgressbar",
            troughcolor=p["entry_bg"],
            background=p["good"],
            bordercolor=p["entry_bg"],
            lightcolor=p["good"],
            darkcolor=p["good"],
        )
        self.style.configure("Modern.TNotebook", background=p["bg"])
        self.style.configure("Modern.TNotebook.Tab", padding=(10, 6), font=("Segoe UI", 10))
        self.style.configure("ToggleOpt.TCheckbutton", background=p["bg"], foreground=p["muted"], font=("Segoe UI", 10))

    def _build_layout(self):
        root = ttk.Frame(self, style="App.TFrame", padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        header_row = ttk.Frame(root, style="App.TFrame")
        header_row.pack(fill=tk.X)

        header_left = ttk.Frame(header_row, style="App.TFrame")
        header_left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(header_left, text="SSH over Tor Control Center", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header_left,
            text="v2 polished UI: services, logs, speed, and theme controls.",
            style="SubHeader.TLabel",
        ).pack(anchor=tk.W, pady=(0, 8))

        header_right = ttk.Frame(header_row, style="App.TFrame")
        header_right.pack(side=tk.RIGHT, anchor=tk.NE)
        ttk.Button(header_right, text="Toggle Theme", command=self.toggle_theme, style="Accent.TButton").pack(
            side=tk.RIGHT, padx=(8, 0)
        )
        ttk.Checkbutton(
            header_right,
            text="Minimize to tray mode",
            variable=self.minimize_to_tray_var,
            style="ToggleOpt.TCheckbutton",
        ).pack(side=tk.RIGHT)

        top = ttk.Frame(root, style="App.TFrame")
        top.pack(fill=tk.X, pady=(2, 8))

        self.services_card = ttk.Frame(top, style="Card.TFrame", padding=12)
        self.services_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(self.services_card, text="Service Controls", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))

        self.service_status_labels = {}
        self.toggle_widgets = {}
        self.restart_buttons = {}
        self.delete_buttons = {}
        self.service_controls = {}
        self._build_service_row(
            "SSH",
            self.ssh_state,
            self.ssh_toggle_var,
            self.ssh_toggle_text,
            self.ssh_unit,
            "ssh",
        )
        self._build_service_row(
            "Tor",
            self.tor_state,
            self.tor_toggle_var,
            self.tor_toggle_text,
            self.tor_unit,
            "tor",
        )
        self._build_service_row(
            "Fail2Ban",
            self.f2b_state,
            self.f2b_toggle_var,
            self.f2b_toggle_text,
            self.f2b_unit,
            "fail2ban",
        )

        ttk.Button(
            self.services_card, text="Refresh Services", command=self.refresh_service_status, style="Accent.TButton"
        ).pack(anchor=tk.W, pady=(10, 0))
        ttk.Label(
            self.services_card,
            text="To reinstall missing services/packages, click Apply Settings.",
            style="Muted.TLabel",
            wraplength=420,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 0))

        self.speed_card = ttk.Frame(top, style="Card.TFrame", padding=12)
        self.speed_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        ttk.Label(self.speed_card, text="SSH Speed Monitor", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))
        ttk.Label(
            self.speed_card,
            text="Local SSH handshake latency (lower is better):",
            style="Body.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(self.speed_card, textvariable=self.speed_var, style="Value.TLabel").pack(anchor=tk.W, pady=(3, 7))
        self.speed_meter = ttk.Progressbar(self.speed_card, style="Speed.Horizontal.TProgressbar", maximum=100)
        self.speed_meter.pack(fill=tk.X)
        ttk.Label(self.speed_card, textvariable=self.speed_quality_var, style="Body.TLabel").pack(anchor=tk.W, pady=(7, 0))
        ttk.Button(self.speed_card, text="Measure Now", command=self.sample_ssh_speed, style="Accent.TButton").pack(
            anchor=tk.W, pady=(10, 0)
        )

        bottom = ttk.Frame(root, style="App.TFrame")
        bottom.pack(fill=tk.BOTH, expand=True)

        self.config_card = ttk.Frame(bottom, style="Card.TFrame", padding=12)
        self.config_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        ttk.Label(self.config_card, text="Configuration", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )

        ttk.Label(self.config_card, text="SSH Port:", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(self.config_card, textvariable=self.port_var, width=16).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(self.config_card, text="Max Connections:", style="Body.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(self.config_card, textvariable=self.max_conn_var, width=16).grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(self.config_card, text="Max Auth Tries:", style="Body.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Label(self.config_card, textvariable=self.max_auth_tries_var, style="Value.TLabel").grid(
            row=3, column=1, sticky="w", pady=4
        )

        ttk.Label(self.config_card, text="Login Grace Time:", style="Body.TLabel").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Label(self.config_card, textvariable=self.login_grace_var, style="Value.TLabel").grid(
            row=4, column=1, sticky="w", pady=4
        )

        ttk.Checkbutton(
            self.config_card,
            text="Enable Password Authentication",
            variable=self.password_auth_var,
            style="ToggleOpt.TCheckbutton",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 2))

        btns = ttk.Frame(self.config_card, style="Card.TFrame")
        btns.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Button(btns, text="Apply Settings", command=self.apply_settings, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(btns, text="Refresh Config", command=self.refresh_config).pack(side=tk.LEFT, padx=8)

        self.tabs = ttk.Notebook(bottom, style="Modern.TNotebook")
        self.tabs.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        endpoint_tab = ttk.Frame(self.tabs, style="Card.TFrame", padding=0)
        logs_tab = ttk.Frame(self.tabs, style="Card.TFrame", padding=12)
        self.tabs.add(endpoint_tab, text="Endpoint")
        self.tabs.add(logs_tab, text="Logs")

        self.endpoint_canvas = tk.Canvas(
            endpoint_tab,
            bg=self.palette["card"],
            highlightthickness=0,
            bd=0,
        )
        self.endpoint_scrollbar = ttk.Scrollbar(endpoint_tab, orient=tk.VERTICAL, command=self.endpoint_canvas.yview)
        self.endpoint_hscrollbar = ttk.Scrollbar(endpoint_tab, orient=tk.HORIZONTAL, command=self.endpoint_canvas.xview)
        self.endpoint_canvas.configure(
            yscrollcommand=self.endpoint_scrollbar.set,
            xscrollcommand=self.endpoint_hscrollbar.set,
        )

        self.endpoint_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.endpoint_hscrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.endpoint_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.endpoint_content = ttk.Frame(self.endpoint_canvas, style="Card.TFrame", padding=12)
        self.endpoint_canvas_window = self.endpoint_canvas.create_window(
            (0, 0),
            window=self.endpoint_content,
            anchor="nw",
        )

        self.endpoint_content.bind(
            "<Configure>",
            lambda _e: self.endpoint_canvas.configure(scrollregion=self.endpoint_canvas.bbox("all")),
        )
        # Keep natural content width for horizontal scrolling while still allowing resize.
        self.endpoint_canvas.bind(
            "<Configure>",
            lambda _e: self.endpoint_canvas.configure(scrollregion=self.endpoint_canvas.bbox("all")),
        )

        ttk.Label(self.endpoint_content, text="Current Endpoint", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(self.endpoint_content, text="Hidden Service Directory", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(self.endpoint_content, textvariable=self.hidden_service_dir_var, style="Value.TLabel").pack(anchor=tk.W, pady=(2, 8))
        ttk.Button(
            self.endpoint_content,
            text="Copy Hidden Service Directory",
            command=lambda: self.copy_to_clipboard(self.hidden_service_dir_var.get(), "Hidden service directory copied."),
        ).pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(self.endpoint_content, text="Onion Address", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(self.endpoint_content, textvariable=self.onion_var, style="Value.TLabel").pack(anchor=tk.W, pady=(2, 8))
        ttk.Button(
            self.endpoint_content,
            text="Copy Onion Address",
            command=lambda: self.copy_to_clipboard(self.onion_var.get(), "Onion address copied."),
        ).pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(self.endpoint_content, text="Endpoint Mode", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(self.endpoint_content, textvariable=self.endpoint_mode_var, style="Value.TLabel").pack(anchor=tk.W, pady=(2, 10))

        ttk.Label(self.endpoint_content, text="Connection Command", style="Body.TLabel").pack(anchor=tk.W)
        ttk.Label(
            self.endpoint_content,
            textvariable=self.connect_cmd_var,
            style="Value.TLabel",
            wraplength=500,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 8))
        ttk.Button(
            self.endpoint_content,
            text="Copy Connection Command",
            command=lambda: self.copy_to_clipboard(self.connect_cmd_var.get(), "Connection command copied."),
        ).pack(anchor=tk.W, pady=(0, 12))

        ttk.Label(self.endpoint_content, text="Client Setup Commands", style="Body.TLabel").pack(anchor=tk.W, pady=(0, 6))
        ttk.Label(
            self.endpoint_content,
            text=(
                "Use these commands on the client endpoint. "
                "If tools already exist, skip install step."
            ),
            style="Muted.TLabel",
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        self.endpoint_steps_card = ttk.Frame(self.endpoint_content, style="Card.TFrame")
        self.endpoint_steps_card.pack(fill=tk.X)
        self.endpoint_steps_container = ttk.Frame(self.endpoint_steps_card, style="Card.TFrame")
        self.endpoint_steps_container.pack(fill=tk.X)
        self.endpoint_cmd_vars = []

        ttk.Button(
            self.endpoint_content,
            text="Copy All Setup Commands",
            command=self.copy_all_endpoint_setup,
        ).pack(anchor=tk.W, pady=(8, 0))

        ttk.Label(logs_tab, text="Logs Viewer (journalctl)", style="CardTitle.TLabel").pack(anchor=tk.W, pady=(0, 10))
        logs_controls = ttk.Frame(logs_tab, style="Card.TFrame")
        logs_controls.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(logs_controls, text="Service:", style="Body.TLabel").pack(side=tk.LEFT)
        self.log_service_combo = ttk.Combobox(
            logs_controls,
            textvariable=self.log_service_var,
            state="readonly",
            values=list(self.service_map.keys()),
            width=12,
        )
        self.log_service_combo.pack(side=tk.LEFT, padx=(6, 10))

        ttk.Label(logs_controls, text="Lines:", style="Body.TLabel").pack(side=tk.LEFT)
        ttk.Spinbox(logs_controls, from_=20, to=500, increment=10, textvariable=self.log_lines_var, width=6).pack(
            side=tk.LEFT, padx=(6, 10)
        )

        ttk.Checkbutton(logs_controls, text="Auto refresh", variable=self.auto_logs_var, style="ToggleOpt.TCheckbutton").pack(
            side=tk.LEFT, padx=(0, 10)
        )
        ttk.Button(logs_controls, text="Refresh Logs", command=self.refresh_logs, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(logs_controls, text="Clear", command=self.clear_logs).pack(side=tk.LEFT, padx=8)

        self.log_text = ScrolledText(logs_tab, wrap=tk.NONE, height=18, font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

        ttk.Label(root, textvariable=self.status_message_var, style="StatusBar.TLabel").pack(
            fill=tk.X, side=tk.BOTTOM, pady=(10, 0)
        )

    def _build_service_row(self, label, state_var, toggle_var, toggle_text_var, unit_name, component_key):
        row = tk.Frame(self.services_card, bg=self.palette["card"])
        row.pack(fill=tk.X, pady=4)

        ttk.Label(row, text=f"{label}:", style="Body.TLabel", width=11).pack(side=tk.LEFT, padx=(0, 6))
        status_label = ttk.Label(row, textvariable=state_var, style="Value.TLabel", width=12)
        status_label.pack(side=tk.LEFT)
        self.service_status_labels[unit_name] = status_label

        toggle = tk.Checkbutton(
            row,
            textvariable=toggle_text_var,
            variable=toggle_var,
            indicatoron=False,
            width=7,
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            command=lambda u=unit_name, v=toggle_var: self.toggle_service(u, v),
            font=("Segoe UI", 9, "bold"),
            highlightthickness=0,
        )
        toggle.pack(side=tk.LEFT, padx=6)
        self.toggle_widgets[unit_name] = (toggle, toggle_text_var, toggle_var)

        restart_button = ttk.Button(row, text="Restart", command=lambda u=unit_name: self.restart_service(u))
        restart_button.pack(side=tk.LEFT)
        self.restart_buttons[unit_name] = restart_button

        delete_button = None
        if component_key in {"tor", "fail2ban"}:
            delete_button = ttk.Button(
                row,
                text="🗑 Delete",
                command=lambda c=component_key: self.delete_component(c),
            )
            delete_button.pack(side=tk.LEFT, padx=(6, 0))
            self.delete_buttons[unit_name] = delete_button

        self.service_controls[component_key] = {
            "unit": unit_name,
            "state_var": state_var,
            "toggle_var": toggle_var,
            "toggle_text_var": toggle_text_var,
            "toggle_widget": toggle,
            "restart_widget": restart_button,
            "delete_widget": delete_button,
        }

    def _append_log(self, text):
        if not text:
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text if text.endswith("\n") else f"{text}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.update_idletasks()

    def _run_stream_command(self, args, env=None):
        self._append_log(f"$ {' '.join(args)}")
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self._append_log(line.rstrip("\n"))
        return process.wait()

    def _detect_component_status(self, component_key):
        if component_key == "ssh":
            return shutil.which("sshd") is not None and SSH_CONFIG.parent.exists()
        if component_key == "tor":
            return shutil.which("tor") is not None and TOR_CONFIG.parent.exists()
        if component_key == "fail2ban":
            return F2B_CONFIG.parent.exists() and (
                shutil.which("fail2ban-client") is not None or service_exists("fail2ban")
            )
        return False

    def _update_component_status(self):
        for component_key in self.component_status:
            self.component_status[component_key] = self._detect_component_status(component_key)

    def _sync_service_control_states(self):
        for component_key, control in self.service_controls.items():
            available = self.component_status.get(component_key, False)
            widget_state = tk.NORMAL if available else tk.DISABLED
            control["toggle_widget"].configure(state=widget_state)
            control["restart_widget"].configure(state=widget_state)
            if control.get("delete_widget") is not None:
                control["delete_widget"].configure(state=widget_state)
            if not available:
                control["state_var"].set("not installed")
                control["toggle_var"].set(False)
                self._update_toggle_visual(control["unit"])
                self._update_status_label_color(control["unit"])

    def _install_component(self, component_key):
        package = self.component_packages[component_key]
        title = self.component_titles[component_key]
        if shutil.which("apt-get") is None:
            messagebox.showerror("Install Error", "apt-get is required to install missing components.")
            self._append_log(f"[install] Cannot install {title}: apt-get not found.")
            return False

        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        self._append_log(f"[install] Installing {title} package: {package}")
        self.set_status(f"Installing {title}...")

        if not self.apt_cache_updated:
            update_rc = self._run_stream_command(["apt-get", "update"], env=env)
            if update_rc != 0:
                self._append_log(f"[install] apt-get update failed with code {update_rc}")
                messagebox.showerror("Install Error", f"apt-get update failed while installing {title}.")
                return False
            self.apt_cache_updated = True

        install_rc = self._run_stream_command(["apt-get", "install", "-y", package], env=env)
        if install_rc != 0:
            self._append_log(f"[install] apt-get install failed with code {install_rc}")
            messagebox.showerror("Install Error", f"Failed installing {title}. See logs panel for details.")
            return False

        # Refresh component detection after successful install.
        self._update_component_status()
        if not self.component_status.get(component_key, False):
            self._append_log(f"[install] {title} install command succeeded, but component still not detected.")
            messagebox.showwarning(
                "Install Check",
                f"{title} was installed, but could not be fully verified. You may need to restart the app.",
            )
            return False

        self.skipped_components.discard(component_key)
        self._append_log(f"[install] {title} installed successfully.")
        self.set_status(f"{title} installed successfully.")
        return True

    def _remove_managed_block(self, path):
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
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
        path.write_text("\n".join(filtered).rstrip() + "\n", encoding="utf-8")

    def _run_remove_package(self, package_names):
        env = os.environ.copy()
        env["DEBIAN_FRONTEND"] = "noninteractive"
        if not self.apt_cache_updated:
            update_rc = self._run_stream_command(["apt-get", "update"], env=env)
            if update_rc != 0:
                self._append_log(f"[remove] apt-get update failed with code {update_rc}")
                return False
            self.apt_cache_updated = True
        remove_rc = self._run_stream_command(["apt-get", "remove", "-y", *package_names], env=env)
        if remove_rc != 0:
            self._append_log(f"[remove] apt-get remove failed with code {remove_rc}")
            return False
        self._run_stream_command(["apt-get", "autoremove", "-y"], env=env)
        return True

    def delete_component(self, component_key):
        if component_key not in {"tor", "fail2ban"}:
            return
        title = self.component_titles[component_key]
        response = messagebox.askyesno(
            f"Delete {title}",
            (
                f"This will remove {title} package(s), stop/disable service, and "
                "remove managed configuration blocks created by this app.\n\n"
                "Do you want to continue?"
            ),
            default=messagebox.NO,
        )
        if not response:
            return

        if component_key == "tor":
            self._append_log("[remove] Removing Tor and torsocks...")
            run_command(["systemctl", "stop", self.tor_unit])
            run_command(["systemctl", "stop", "tor@default"])
            run_command(["systemctl", "disable", self.tor_unit])
            run_command(["systemctl", "disable", "tor@default"])
            previous_tor_hidden_dir = parse_hidden_service_dir(TOR_CONFIG)
            self._remove_managed_block(TOR_CONFIG)
            if self._run_remove_package(["tor", "torsocks"]):
                # Remove all known hidden-service directories so stale hostname files do not remain.
                state_values = parse_key_value_file(STATE_FILE)
                candidate_dirs = {
                    "/var/lib/tor/ssh_service",
                    previous_tor_hidden_dir,
                    self.hidden_service_dir_var.get().strip(),
                    state_values.get("HIDDEN_SERVICE_DIR", "").strip(),
                }
                for hidden_dir in sorted(d for d in candidate_dirs if d):
                    run_command(["rm", "-rf", hidden_dir])
                    self._append_log(f"[remove] Removed Tor hidden-service data: {hidden_dir}")

                clear_cached_onion_state()
                self._append_log("[remove] Cleared cached onion state from manager state file.")
                self.hidden_service_dir_var.set("/var/lib/tor/ssh_service")
                self.onion_var.set("(not available yet)")
                self.connect_cmd_var.set("torsocks ssh -p 22 <username>@<onion>.onion")
                self.set_status("Tor removed.")
            else:
                messagebox.showerror("Delete Error", "Failed to remove Tor. See logs for details.")
        else:
            self._append_log("[remove] Removing Fail2Ban...")
            run_command(["systemctl", "stop", self.f2b_unit])
            run_command(["systemctl", "disable", self.f2b_unit])
            self._remove_managed_block(F2B_CONFIG)
            if self._run_remove_package(["fail2ban"]):
                self.set_status("Fail2Ban removed.")
            else:
                messagebox.showerror("Delete Error", "Failed to remove Fail2Ban. See logs for details.")

        self._update_component_status()
        self.refresh_service_status()
        self.refresh_config()
        self.refresh_logs()

    def _prompt_install_or_skip(self, component_key, required=False):
        title = self.component_titles[component_key]
        if self.component_status.get(component_key, False):
            return True

        response = messagebox.askyesno(
            "Component Missing",
            (
                f"{title} is not installed or missing required files.\n\n"
                "Do you want to install it now?\n\n"
                "Yes = Install now\n"
                "No = Skip"
            ),
            default=messagebox.YES,
        )
        if response:
            return self._install_component(component_key)

        self.skipped_components.add(component_key)
        self._append_log(f"[install] {title}: skipped by user.")
        self.set_status(f"{title} skipped.")
        if required:
            messagebox.showerror(
                "Missing Required Component",
                f"{title} is required for this action. Install it to continue.",
            )
        return False

    def _ensure_components(self, prompt_required=False):
        self._update_component_status()
        for component_key in ("ssh", "tor", "fail2ban"):
            if self.component_status[component_key]:
                continue
            if component_key in self.skipped_components and not prompt_required:
                continue
            required = prompt_required and component_key in {"ssh", "tor"}
            ok = self._prompt_install_or_skip(component_key, required=required)
            if required and not ok:
                return False
        self._update_component_status()
        self._sync_service_control_states()
        return True

    def apply_theme(self, theme_name):
        if theme_name not in THEMES:
            return
        self.theme_name = theme_name
        self.palette = THEMES[theme_name]
        self.configure(bg=self.palette["bg"])
        self._configure_style()

        if hasattr(self, "log_text"):
            self.log_text.configure(
                bg=self.palette["log_bg"],
                fg=self.palette["log_fg"],
                insertbackground=self.palette["fg"],
                selectbackground=self.palette["accent"],
            )

        for unit_name in self.toggle_widgets:
            self._update_toggle_visual(unit_name)
            self._update_status_label_color(unit_name)

        if self.tray_window is not None and self.tray_window.winfo_exists():
            self.tray_window.configure(bg=self.palette["card"])

    def toggle_theme(self):
        next_theme = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme(next_theme)
        self.set_status(f"Theme switched to {next_theme}.")

    def set_status(self, message):
        self.status_message_var.set(message)

    def _status_color(self, state):
        s = state.strip().lower()
        if s == "active":
            return self.palette["good"]
        if s in {"inactive", "failed"}:
            return self.palette["bad"]
        return self.palette["warn"]

    def _update_status_label_color(self, unit_name):
        label = self.service_status_labels.get(unit_name)
        if label is None:
            return
        state = get_service_state(unit_name)
        label.configure(foreground=self._status_color(state))

    def _update_toggle_visual(self, unit_name):
        widget_tuple = self.toggle_widgets.get(unit_name)
        if widget_tuple is None:
            return
        toggle, text_var, var = widget_tuple
        is_on = bool(var.get())
        text_var.set("ON" if is_on else "OFF")
        if is_on:
            toggle.configure(
                bg=self.palette["good"],
                fg="#ffffff",
                activebackground=self.palette["good"],
                activeforeground="#ffffff",
                selectcolor=self.palette["good"],
            )
        else:
            toggle.configure(
                bg=self.palette["bad"],
                fg="#ffffff",
                activebackground=self.palette["bad"],
                activeforeground="#ffffff",
                selectcolor=self.palette["bad"],
            )

    def refresh_service_status(self):
        self._update_component_status()
        ssh_state = get_service_state(self.ssh_unit)
        tor_state = get_service_state(self.tor_unit)
        f2b_state = get_service_state(self.f2b_unit)

        self.ssh_state.set(ssh_state if self.component_status["ssh"] else "not installed")
        self.tor_state.set(tor_state if self.component_status["tor"] else "not installed")
        self.f2b_state.set(f2b_state if self.component_status["fail2ban"] else "not installed")

        self._syncing_toggles = True
        self.ssh_toggle_var.set(self.component_status["ssh"] and ssh_state == "active")
        self.tor_toggle_var.set(self.component_status["tor"] and tor_state == "active")
        self.f2b_toggle_var.set(self.component_status["fail2ban"] and f2b_state == "active")
        self._syncing_toggles = False

        for unit_name in [self.ssh_unit, self.tor_unit, self.f2b_unit]:
            self._update_toggle_visual(unit_name)
            self._update_status_label_color(unit_name)
        self._sync_service_control_states()

    def toggle_service(self, unit_name, var):
        if self._syncing_toggles:
            return
        component_key = "ssh" if unit_name == self.ssh_unit else "tor" if unit_name == self.tor_unit else "fail2ban"
        if not self.component_status.get(component_key, False):
            var.set(False)
            self._prompt_install_or_skip(component_key, required=False)
            self.refresh_service_status()
            return
        desired_on = bool(var.get())
        current = get_service_state(unit_name)
        if desired_on and current != "active":
            action = "start"
        elif not desired_on and current == "active":
            action = "stop"
        else:
            self._update_toggle_visual(unit_name)
            return

        result = run_command(["systemctl", action, unit_name])
        if result.returncode != 0:
            messagebox.showerror("Service Error", result.stderr.strip() or result.stdout.strip() or "Unknown error")
            self.refresh_service_status()
            return
        self.refresh_service_status()
        self.set_status(f"{unit_name}: {action} executed.")

    def restart_service(self, unit_name):
        component_key = "ssh" if unit_name == self.ssh_unit else "tor" if unit_name == self.tor_unit else "fail2ban"
        if not self.component_status.get(component_key, False):
            self._prompt_install_or_skip(component_key, required=False)
            self.refresh_service_status()
            return
        result = run_command(["systemctl", "restart", unit_name])
        if result.returncode != 0:
            messagebox.showerror("Service Error", result.stderr.strip() or result.stdout.strip() or "Unknown error")
            return
        self.refresh_service_status()
        self.set_status(f"{unit_name}: restarted.")

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
        else:
            self.onion_var.set("(not available yet)")
        self.update_endpoint_preview()

    def refresh_all(self):
        self._ensure_components(prompt_required=False)
        self.refresh_service_status()
        self.refresh_config()
        self.sample_ssh_speed()
        self.refresh_logs()
        self.set_status("Status refreshed.")

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
        if not self._ensure_components(prompt_required=True):
            return
        try:
            port, max_conn = self._validate_settings()
        except ValueError as exc:
            messagebox.showerror("Invalid Configuration", str(exc))
            return

        pw_auth = "yes" if self.password_auth_var.get() else "no"
        hidden_dir = parse_hidden_service_dir(TOR_CONFIG)

        ensure_backup(SSH_CONFIG)
        ensure_backup(TOR_CONFIG)
        if self.component_status["fail2ban"]:
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
            if self.component_status["fail2ban"]:
                write_managed_block(F2B_CONFIG, f2b_block)
            else:
                self._append_log("[config] Fail2Ban skipped (not installed).")
        except OSError as exc:
            messagebox.showerror("Write Error", f"Failed writing config files:\n{exc}")
            return

        # Some distros require this runtime directory before sshd validation/start.
        if not Path("/run/sshd").exists():
            try:
                Path("/run/sshd").mkdir(parents=True, exist_ok=True)
                os.chmod("/run/sshd", 0o755)
                self._append_log("[config] Created missing runtime directory: /run/sshd")
            except OSError as exc:
                messagebox.showerror(
                    "Runtime Directory Error",
                    f"Failed creating /run/sshd:\n{exc}",
                )
                return

        test_sshd = run_command(["sshd", "-t"])
        if test_sshd.returncode != 0:
            messagebox.showerror("Validation Error", test_sshd.stderr.strip() or "sshd -t failed.")
            return

        # Preserve runtime ON/OFF choices: apply config to running services,
        # but do not force-start services that are currently stopped.
        unit_states_before_apply = {}
        units_managed = [self.ssh_unit, self.tor_unit]
        if self.component_status["fail2ban"]:
            units_managed.append(self.f2b_unit)
        for unit in units_managed:
            unit_states_before_apply[unit] = get_service_state(unit)

        for unit in units_managed:
            if unit_states_before_apply.get(unit) == "active":
                run_command(["systemctl", "restart", unit])
            else:
                self._append_log(f"[service] {unit} is not active; preserving OFF state (not starting).")

        onion = ""
        for _ in range(15):
            onion = read_onion_hostname(hidden_dir)
            if onion:
                break
            time.sleep(1)

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
        self.set_status("Settings applied successfully.")
        messagebox.showinfo("Success", "Settings updated and services restarted.")

    def sample_ssh_speed(self):
        try:
            port = int(self.port_var.get())
        except ValueError:
            self.speed_var.set("Invalid port")
            self.speed_quality_var.set("Quality: unknown")
            self.speed_meter["value"] = 0
            return

        start = time.perf_counter()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
                sock.settimeout(2)
                sock.recv(64)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.speed_var.set(f"{elapsed_ms:.1f} ms")

            if elapsed_ms <= 80:
                quality, score = "excellent", 100
            elif elapsed_ms <= 160:
                quality, score = "good", 75
            elif elapsed_ms <= 350:
                quality, score = "fair", 50
            else:
                quality, score = "slow", 25
            self.speed_quality_var.set(f"Quality: {quality}")
            self.speed_meter["value"] = score
        except OSError:
            self.speed_var.set("unreachable")
            self.speed_quality_var.set("Quality: no SSH response")
            self.speed_meter["value"] = 0

    def _speed_tick(self):
        self.sample_ssh_speed()
        self.after(5000, self._speed_tick)

    def refresh_logs(self):
        service = self.log_service_var.get()
        unit = self.service_map.get(service, self.ssh_unit)
        component_key = service.lower() if service.lower() in self.component_status else "ssh"
        if service == "Fail2Ban":
            component_key = "fail2ban"
        try:
            lines = int(self.log_lines_var.get())
        except ValueError:
            lines = 120
            self.log_lines_var.set("120")

        if not self.component_status.get(component_key, False):
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.delete("1.0", tk.END)
            self.log_text.insert(
                tk.END,
                f"{service} is not installed.\nUse the service toggle to choose Install or Skip.",
            )
            self.log_text.configure(state=tk.DISABLED)
            self.log_text.yview_moveto(1.0)
            self.set_status(f"{service} not installed.")
            return

        ok, output = fetch_journal_logs(unit, lines)
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        if ok:
            self.log_text.insert(tk.END, output or "(No logs)")
            self.set_status(f"Logs refreshed: {unit}")
        else:
            self.log_text.insert(tk.END, f"Failed to load logs for {unit}:\n{output}")
            self.set_status(f"Log refresh failed for {unit}")
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.yview_moveto(1.0)

    def clear_logs(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.set_status("Logs cleared.")

    def _logs_tick(self):
        if self.auto_logs_var.get():
            self.refresh_logs()
        self.after(8000, self._logs_tick)

    def _clear_endpoint_steps(self):
        if not hasattr(self, "endpoint_steps_container"):
            return
        for child in self.endpoint_steps_container.winfo_children():
            child.destroy()

    def _add_endpoint_step(self, title, command_text):
        row = ttk.Frame(self.endpoint_steps_container, style="Card.TFrame")
        row.pack(fill=tk.X, pady=3)

        ttk.Label(row, text=title, style="Body.TLabel", width=40).pack(side=tk.LEFT, padx=(0, 8))
        cmd_var = tk.StringVar(value=command_text)
        self.endpoint_cmd_vars.append(cmd_var)
        ttk.Label(
            row,
            textvariable=cmd_var,
            style="Value.TLabel",
            wraplength=360,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(
            row,
            text="Copy",
            command=lambda v=cmd_var: self.copy_to_clipboard(v.get(), "Command copied."),
        ).pack(side=tk.LEFT, padx=(8, 0))

    def _set_endpoint_steps(self, steps):
        self._clear_endpoint_steps()
        self.endpoint_cmd_vars = []
        for title, cmd in steps:
            self._add_endpoint_step(title, cmd)

    def copy_all_endpoint_setup(self):
        if not self.endpoint_cmd_vars:
            messagebox.showwarning("Copy", "No setup commands available yet.")
            return
        combined = "\n".join(var.get() for var in self.endpoint_cmd_vars if var.get().strip())
        self.copy_to_clipboard(combined, "All setup commands copied.")

    def update_endpoint_preview(self):
        try:
            port = int(self.port_var.get())
            if port < 1 or port > 65535:
                raise ValueError
        except (ValueError, TypeError):
            port = 22

        onion = self.onion_var.get().strip()
        target_onion = onion if onion and not onion.startswith("(") else "<ONION>.onion"
        password_auth_enabled = bool(self.password_auth_var.get())

        install_update = "sudo apt update"
        install_tools = "sudo apt install -y openssh-client torsocks"
        keygen_cmd = "ssh-keygen -t ed25519 -a 100 -f ~/.ssh/id_ed25519_tor"
        copyid_cmd = f"torsocks ssh-copy-id -i ~/.ssh/id_ed25519_tor.pub -p {port} <USER>@{target_onion}"
        test_cmd = f"torsocks ssh -i ~/.ssh/id_ed25519_tor -p {port} <USER>@{target_onion}"
        password_connect_cmd = f"torsocks ssh -p {port} <USER>@{target_onion}"

        self.setup_install_update_var.set(install_update)
        self.setup_install_tools_var.set(install_tools)
        self.setup_keygen_var.set(keygen_cmd)
        self.setup_copyid_var.set(copyid_cmd)
        self.setup_test_var.set(test_cmd)

        if password_auth_enabled:
            self.endpoint_mode_var.set("Password authentication enabled")
            self.connect_cmd_var.set(password_connect_cmd)
            steps = [
                ("Install tools (skip if already installed on client endpoint)", self.setup_install_update_var.get()),
                ("Install tools (skip if already installed on client endpoint)", self.setup_install_tools_var.get()),
                ("Connection command", password_connect_cmd),
            ]
        else:
            self.endpoint_mode_var.set("Password authentication disabled (passwordless key required)")
            self.connect_cmd_var.set(test_cmd)
            steps = [
                ("Install tools (skip if already installed on client endpoint)", self.setup_install_update_var.get()),
                ("Install tools (skip if already installed on client endpoint)", self.setup_install_tools_var.get()),
                ("Generate SSH key", self.setup_keygen_var.get()),
                ("Copy key to your onion server", self.setup_copyid_var.get()),
                ("Connection command", self.setup_test_var.get()),
            ]

        self._set_endpoint_steps(steps)

    def copy_to_clipboard(self, text, status_message):
        if not text or text.startswith("("):
            messagebox.showwarning("Copy", "Nothing useful to copy yet.")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.set_status(status_message)

    def _on_unmap(self, event):
        if event.widget is not self:
            return
        if self.minimize_to_tray_var.get() and self.state() == "iconic":
            self.after(80, self.hide_to_tray)

    def _on_close_request(self):
        if self.minimize_to_tray_var.get():
            self.hide_to_tray()
        else:
            self.quit_app()

    def hide_to_tray(self):
        if self.tray_window is not None and self.tray_window.winfo_exists():
            return
        self.withdraw()
        self.tray_window = tk.Toplevel()
        self.tray_window.title("SSH over Tor (Background)")
        self.tray_window.geometry("320x120+20+20")
        self.tray_window.resizable(False, False)
        self.tray_window.attributes("-topmost", True)
        self.tray_window.configure(bg=self.palette["card"])

        label = tk.Label(
            self.tray_window,
            text="App is running in background.",
            bg=self.palette["card"],
            fg=self.palette["fg"],
            font=("Segoe UI", 10, "bold"),
        )
        label.pack(pady=(15, 8))

        button_row = tk.Frame(self.tray_window, bg=self.palette["card"])
        button_row.pack()
        tk.Button(
            button_row,
            text="Restore",
            command=self.restore_from_tray,
            bg=self.palette["good"],
            fg="#ffffff",
            relief="flat",
            padx=14,
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            button_row,
            text="Quit",
            command=self.quit_app,
            bg=self.palette["bad"],
            fg="#ffffff",
            relief="flat",
            padx=14,
        ).pack(side=tk.LEFT, padx=6)

        self.tray_window.protocol("WM_DELETE_WINDOW", self.restore_from_tray)
        self.set_status("Minimized to tray mode. Use Restore to reopen.")

    def restore_from_tray(self):
        if self.tray_window is not None and self.tray_window.winfo_exists():
            self.tray_window.destroy()
        self.tray_window = None
        self.deiconify()
        self.lift()
        self.focus_force()
        self.set_status("Restored from tray mode.")

    def quit_app(self):
        if self.tray_window is not None and self.tray_window.winfo_exists():
            self.tray_window.destroy()
        self.destroy()


def main():
    if os.geteuid() != 0:
        print("Run as root so the GUI can manage services and write system config files.")
        print("Example: sudo python3 ssh_tor_manager_gui.py")
        raise SystemExit(1)

    for command in ["systemctl", "journalctl"]:
        if shutil.which(command) is None:
            print(f"Missing required command: {command}")
            raise SystemExit(1)

    app = ManagerGUIV2()
    app.mainloop()


if __name__ == "__main__":
    main()
