.PHONY: install setup start stop restart status logs uninstall hook-install hook-uninstall pause unpause

# ── Detect OS (Darwin = macOS, Linux = Linux) ───────────────
# Make evaluates `uname -s` at parse time and stores the result.
# The ifeq/else block below selects the matching set of targets.
UNAME := $(shell uname -s)

# ── macOS (launchd) ──────────────────────────────────────────
PLIST_NAME := com.stopkran.daemon
PLIST_SRC  := $(CURDIR)/$(PLIST_NAME).plist
PLIST_DEST := $(HOME)/Library/LaunchAgents/$(PLIST_NAME).plist

# ── Linux (systemd) ─────────────────────────────────────────
SERVICE_NAME := stopkran
SERVICE_SRC  := $(CURDIR)/$(SERVICE_NAME).service
SERVICE_DEST := $(HOME)/.config/systemd/user/$(SERVICE_NAME).service

# ── Common ───────────────────────────────────────────────────
CONFIG     := $(HOME)/.config/stopkran/config.json
LOG        := $(HOME)/.local/log/stopkran.log
SOCKET     := /tmp/stopkran.sock
SETTINGS   := $(HOME)/.claude/settings.json

# ── Dependencies ──────────────────────────────────────────────

install: ## Install Python dependencies via uv
	uv sync

# ── Setup ─────────────────────────────────────────────────────

setup: install ## Interactive setup wizard (token, config, hook, autostart)
	uv run python stopkran_setup.py

# ── Daemon control ────────────────────────────────────────────

start: ## Start the daemon (foreground)
	uv run python stopkran_daemon.py

# ─────────────────────────────────────────────────────────────
# Platform-specific targets: Make includes only one branch at
# parse time — macOS (launchd) or Linux (systemd).
# On Linux the launchctl targets don't exist, and vice versa.
# ─────────────────────────────────────────────────────────────
ifeq ($(UNAME),Darwin)
# ── macOS: autostart via launchd ────────────────────────────

start-bg: install ## Install and start daemon via launchd (macOS) or systemd (Linux)
	@mkdir -p $(HOME)/.local/log
	@sed -e 's|{{UV}}|$(shell which uv)|' \
	     -e 's|{{PROJECT}}|$(CURDIR)|' \
	     -e 's|{{DAEMON}}|$(CURDIR)/stopkran_daemon.py|' \
	     -e 's|{{LOG}}|$(LOG)|' \
	     $(PLIST_SRC) > $(PLIST_DEST)
	launchctl unload $(PLIST_DEST) 2>/dev/null || true
	launchctl load $(PLIST_DEST)
	@echo "✅ Daemon started (launchd)"

stop: ## Stop the daemon
	@if [ -f $(PLIST_DEST) ]; then \
		launchctl unload $(PLIST_DEST) 2>/dev/null || true; \
		echo "✅ Daemon stopped (launchd)"; \
	else \
		pkill -f stopkran_daemon 2>/dev/null || true; \
		echo "✅ Daemon stopped (kill)"; \
	fi
	@rm -f $(SOCKET)

restart: stop start-bg ## Restart the daemon

status: ## Show daemon status
	@echo "── Process ──"
	@pgrep -f stopkran_daemon > /dev/null && echo "Running (PID $$(pgrep -f stopkran_daemon | head -1))" || echo "Not running"
	@echo ""
	@echo "── Socket ──"
	@test -S $(SOCKET) && echo "$(SOCKET) exists" || echo "$(SOCKET) missing"
	@echo ""
	@echo "── Config ──"
	@test -f $(CONFIG) && echo "$(CONFIG) exists" || echo "$(CONFIG) missing"
	@echo ""
	@echo "── Hook ──"
	@grep -q stopkran_hook $(SETTINGS) 2>/dev/null && echo "Installed in $(SETTINGS)" || echo "Not installed"
	@echo ""
	@echo "── Service ──"
	@test -f $(PLIST_DEST) && echo "launchd plist installed" || echo "launchd plist not installed"

uninstall: stop hook-uninstall ## Full uninstall: stop daemon, remove hook, config, service
	rm -f $(PLIST_DEST)
	rm -rf $(HOME)/.config/stopkran
	@echo "✅ Uninstalled"

else
# ── Linux: autostart via systemd (user service) ─────────────

start-bg: install
	@mkdir -p $(HOME)/.local/log
	@mkdir -p $(HOME)/.config/systemd/user
	# Render .service from template, substituting actual paths
	@sed -e 's|{{UV}}|$(shell which uv)|' \
	     -e 's|{{PROJECT}}|$(CURDIR)|' \
	     -e 's|{{DAEMON}}|$(CURDIR)/stopkran_daemon.py|' \
	     -e 's|{{LOG}}|$(LOG)|' \
	     $(SERVICE_SRC) > $(SERVICE_DEST)
	# systemd runs services in a clean env without shell variables.
	# If a proxy is needed, inject HTTP_PROXY etc. into the .service
	# file as Environment= directives.
	@for var in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do \
		val=$$(printenv $$var 2>/dev/null || true); \
		if [ -n "$$val" ]; then \
			sed -i "/^\[Install\]/i Environment=$$var=$$val" $(SERVICE_DEST); \
		fi; \
	done
	# Reload unit files, enable autostart, (re)start the service
	systemctl --user daemon-reload
	systemctl --user enable $(SERVICE_NAME)
	systemctl --user restart $(SERVICE_NAME)
	@echo "✅ Daemon started (systemd)"

stop:
	# Stop via systemctl; fall back to killing the process directly
	@if systemctl --user is-active $(SERVICE_NAME) >/dev/null 2>&1; then \
		systemctl --user stop $(SERVICE_NAME); \
		echo "✅ Daemon stopped (systemd)"; \
	else \
		pkill -f stopkran_daemon 2>/dev/null || true; \
		echo "✅ Daemon stopped (kill)"; \
	fi
	@rm -f $(SOCKET)

restart:
	@if [ -f $(SERVICE_DEST) ]; then \
		systemctl --user restart $(SERVICE_NAME); \
		echo "✅ Daemon restarted (systemd)"; \
	else \
		$(MAKE) stop; \
		$(MAKE) start-bg; \
	fi

status:
	@echo "── Process ──"
	@pgrep -f stopkran_daemon > /dev/null && echo "Running (PID $$(pgrep -f stopkran_daemon | head -1))" || echo "Not running"
	@echo ""
	@echo "── Socket ──"
	@test -S $(SOCKET) && echo "$(SOCKET) exists" || echo "$(SOCKET) missing"
	@echo ""
	@echo "── Config ──"
	@test -f $(CONFIG) && echo "$(CONFIG) exists" || echo "$(CONFIG) missing"
	@echo ""
	@echo "── Hook ──"
	@grep -q stopkran_hook $(SETTINGS) 2>/dev/null && echo "Installed in $(SETTINGS)" || echo "Not installed"
	@echo ""
	@echo "── Service ──"
	@if [ -f $(SERVICE_DEST) ]; then \
		systemctl --user status $(SERVICE_NAME) --no-pager 2>/dev/null || echo "systemd unit installed but not queryable"; \
	else \
		echo "systemd unit not installed"; \
	fi

uninstall: stop hook-uninstall
	# Disable autostart, remove .service file and config
	@if [ -f $(SERVICE_DEST) ]; then \
		systemctl --user disable $(SERVICE_NAME) 2>/dev/null || true; \
		rm -f $(SERVICE_DEST); \
		systemctl --user daemon-reload; \
	fi
	rm -rf $(HOME)/.config/stopkran
	@echo "✅ Uninstalled"

endif
# ── End of platform-specific block ──────────────────────────

logs: ## Tail daemon logs
	@tail -f $(LOG)

# ── Hook management ───────────────────────────────────────────

hook-install: ## Add stopkran hook to Claude Code settings.json
	@uv run python -c "\
	import json; \
	from pathlib import Path; \
	p = Path('$(SETTINGS)'); \
	s = json.loads(p.read_text()) if p.exists() else {}; \
	h = s.setdefault('hooks', {}).setdefault('PermissionRequest', []); \
	cmd = 'python3 $(CURDIR)/stopkran_hook.py'; \
	any('stopkran_hook' in x.get('command','') for e in h for x in e.get('hooks',[])) and exit(0); \
	h.append({'matcher':'*','hooks':[{'type':'command','command':cmd,'timeout':330000}]}); \
	p.parent.mkdir(parents=True,exist_ok=True); \
	p.write_text(json.dumps(s,indent=2)); \
	print('✅ Hook installed')"

hook-uninstall: ## Remove stopkran hook from Claude Code settings.json
	@uv run python -c "\
	import json; \
	from pathlib import Path; \
	p = Path('$(SETTINGS)'); \
	s = json.loads(p.read_text()) if p.exists() else exit(0); \
	h = s.get('hooks',{}).get('PermissionRequest',[]); \
	s['hooks']['PermissionRequest'] = [e for e in h if not any('stopkran_hook' in x.get('command','') for x in e.get('hooks',[]))]; \
	p.write_text(json.dumps(s,indent=2)); \
	print('✅ Hook removed')"

# ── Pause mode ────────────────────────────────────────────────

pause: ## Pause — stop forwarding to Telegram, use native UI
	@mkdir -p $(HOME)/.config/stopkran
	@touch $(HOME)/.config/stopkran/paused
	@echo "⏸ Paused"

unpause: ## Resume — forward requests to Telegram again
	@rm -f $(HOME)/.config/stopkran/paused
	@echo "▶️ Resumed"

# ── Help ──────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
