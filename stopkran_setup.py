#!/usr/bin/env python3
"""
Stopkran setup wizard.

Interactive script that:
1. Asks for the Telegram bot token (from BotFather)
2. Creates ~/.config/stopkran/config.json
3. Adds the hook to ~/.claude/settings.json (preserving existing hooks)
4. Optionally installs a launchd plist for macOS autostart
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


def step_launchd():
    print("\n── Step 4: Autostart (macOS launchd) ──")
    if sys.platform != "darwin":
        print("Not macOS — skipping launchd setup.")
        return

    if not ask_yn("Install launchd plist for autostart?"):
        return

    plist_template = SCRIPT_DIR / f"{PLIST_NAME}.plist"
    plist_dest = LAUNCHD_DIR / f"{PLIST_NAME}.plist"

    if not plist_template.exists():
        print(f"⚠️  Plist template not found at {plist_template}")
        return

    LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)

    # Read template and fill in paths
    with open(plist_template) as f:
        content = f.read()

    import shutil
    uv_path = shutil.which("uv") or "uv"
    project_path = str(SCRIPT_DIR)
    daemon_path = str(SCRIPT_DIR / "stopkran_daemon.py")
    log_path = str(Path.home() / ".local" / "log" / "stopkran.log")
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    content = content.replace("{{UV}}", uv_path)
    content = content.replace("{{PROJECT}}", project_path)
    content = content.replace("{{DAEMON}}", daemon_path)
    content = content.replace("{{LOG}}", log_path)

    with open(plist_dest, "w") as f:
        f.write(content)

    # Unload if already loaded, then load
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


def main():
    print("🔐 Stopkran Setup Wizard")
    print("=" * 40)

    token = step_token()
    step_config(token)
    step_hook()
    step_launchd()

    print("\n" + "=" * 40)
    print("🎉 Setup complete!")
    print()
    print("Next steps:")
    print("  1. Start the daemon:  uv run python stopkran_daemon.py")
    print("  2. Send /start to your bot in Telegram")
    print("  3. Claude Code will now forward permission requests to Telegram")


if __name__ == "__main__":
    main()
