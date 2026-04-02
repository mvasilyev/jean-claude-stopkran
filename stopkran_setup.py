#!/usr/bin/env python3
"""
Stopkran setup wizard.

Interactive script that:
1. Asks for the Telegram bot token (from BotFather)
2. Creates ~/.config/stopkran/config.json
3. Adds the hook to ~/.claude/settings.json (preserving existing hooks)
4. Installs autostart service (launchd on macOS, systemd on Linux)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "stopkran"
CONFIG_PATH = CONFIG_DIR / "config.json"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PLIST_NAME = "com.stopkran.daemon"
LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"

# Resolve the directory where this script lives (= project dir)
SCRIPT_DIR = Path(__file__).resolve().parent


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def ask_yn(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    value = input(f"{prompt} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value.startswith("y")


def step_token() -> str:
    print("\n── Step 1: Telegram Bot Token ──")
    print("Create a bot via @BotFather and paste the token here.")
    token = ask("Bot token")
    if not token:
        print("Token is required. Exiting.")
        sys.exit(1)
    return token


def step_config(token: str) -> dict:
    print("\n── Step 2: Configuration ──")
    timeout = int(ask("Auto-deny timeout in seconds", "300"))

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = {
        "token": token,
        "chat_id": None,
        "timeout": timeout,
    }
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)
    os.chmod(CONFIG_PATH, 0o600)
    print(f"✅ Config saved to {CONFIG_PATH}")
    return cfg


def step_hook():
    print("\n── Step 3: Claude Code Hook ──")
    hook_script = SCRIPT_DIR / "stopkran_hook.py"

    if not hook_script.exists():
        print(f"⚠️  Hook script not found at {hook_script}")
        print("Skipping hook installation.")
        return

    # Hook is stdlib-only, no need for uv run — just use python3 directly
    hook_command = f"python3 {hook_script}"

    # Hook timeout should be longer than daemon timeout to avoid
    # Claude Code killing the hook before auto-deny kicks in
    hook_timeout = 330000  # milliseconds

    new_hook_entry = {
        "matcher": "*",
        "hooks": [
            {
                "type": "command",
                "command": hook_command,
                "timeout": hook_timeout,
            }
        ],
    }

    # Load existing settings
    settings = {}
    if CLAUDE_SETTINGS_PATH.exists():
        with open(CLAUDE_SETTINGS_PATH) as f:
            settings = json.load(f)

    hooks = settings.setdefault("hooks", {})
    perm_hooks = hooks.setdefault("PermissionRequest", [])

    # Check if already installed (search in nested hooks arrays)
    for entry in perm_hooks:
        for h in entry.get("hooks", []):
            if "stopkran_hook" in h.get("command", ""):
                print("Hook already present in settings.json — skipping.")
                return

    perm_hooks.append(new_hook_entry)

    CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CLAUDE_SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)

    print(f"✅ Hook added to {CLAUDE_SETTINGS_PATH}")


def _resolve_template_vars() -> dict[str, str]:
    """Resolve common template variables for service files."""
    import shutil
    uv_path = shutil.which("uv") or "uv"
    project_path = str(SCRIPT_DIR)
    daemon_path = str(SCRIPT_DIR / "stopkran_daemon.py")
    log_path = str(Path.home() / ".local" / "log" / "stopkran.log")
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    return {
        "{{UV}}": uv_path,
        "{{PROJECT}}": project_path,
        "{{DAEMON}}": daemon_path,
        "{{LOG}}": log_path,
    }


def _render_template(template_path: Path, replacements: dict[str, str]) -> str:
    """Read a template file and apply replacements."""
    with open(template_path) as f:
        content = f.read()
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


def step_launchd():
    """Install launchd plist (macOS)."""
    if not ask_yn("Install launchd plist for autostart?"):
        return

    plist_template = SCRIPT_DIR / f"{PLIST_NAME}.plist"
    plist_dest = LAUNCHD_DIR / f"{PLIST_NAME}.plist"

    if not plist_template.exists():
        print(f"⚠️  Plist template not found at {plist_template}")
        return

    LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)

    content = _render_template(plist_template, _resolve_template_vars())

    with open(plist_dest, "w") as f:
        f.write(content)

    subprocess.run(
        ["launchctl", "unload", str(plist_dest)],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(plist_dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"✅ Plist installed and loaded: {plist_dest}")
    else:
        print(f"⚠️  launchctl load failed: {result.stderr}")
        print(f"Plist saved to {plist_dest} — load it manually.")


def step_systemd():
    """Install systemd user service (Linux)."""
    if not ask_yn("Install systemd user service for autostart?"):
        return

    service_template = SCRIPT_DIR / "stopkran.service"
    if not service_template.exists():
        print(f"⚠️  Service template not found at {service_template}")
        return

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dest = service_dir / "stopkran.service"
    service_dir.mkdir(parents=True, exist_ok=True)

    content = _render_template(service_template, _resolve_template_vars())

    # Inject proxy environment variables into the [Service] section
    env_lines = []
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                "http_proxy", "https_proxy", "no_proxy"):
        val = os.environ.get(var)
        if val:
            env_lines.append(f"Environment={var}={val}")
    if env_lines:
        content = content.replace(
            "[Install]",
            "\n".join(env_lines) + "\n\n[Install]",
        )

    with open(service_dest, "w") as f:
        f.write(content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "stopkran"],
        capture_output=True,
    )
    result = subprocess.run(
        ["systemctl", "--user", "restart", "stopkran"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(f"✅ systemd service installed and started: {service_dest}")
    else:
        print(f"⚠️  systemctl restart failed: {result.stderr}")
        print(f"Service saved to {service_dest} — start it manually:")
        print(f"  systemctl --user start stopkran")


def step_autostart():
    """Install autostart service (OS-dependent)."""
    print("\n── Step 4: Autostart ──")
    if sys.platform == "darwin":
        step_launchd()
    elif sys.platform == "linux":
        step_systemd()
    else:
        print(f"Unsupported platform ({sys.platform}) — skipping autostart setup.")
        print("Start the daemon manually: uv run python stopkran_daemon.py")


def main():
    print("🔐 Stopkran Setup Wizard")
    print("=" * 40)

    token = step_token()
    step_config(token)
    step_hook()
    step_autostart()

    print("\n" + "=" * 40)
    print("🎉 Setup complete!")
    print()
    print("Next steps:")
    print("  1. Start the daemon:  make start-bg  (or: uv run python stopkran_daemon.py)")
    print("  2. Send /start to your bot in Telegram")
    print("  3. Claude Code will now forward permission requests to Telegram")


if __name__ == "__main__":
    main()
