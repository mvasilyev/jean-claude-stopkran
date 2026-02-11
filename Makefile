.PHONY: install setup start stop restart status logs uninstall hook-install hook-uninstall pause unpause

PLIST_NAME := com.stopkran.daemon
PLIST_SRC  := $(CURDIR)/$(PLIST_NAME).plist
PLIST_DEST := $(HOME)/Library/LaunchAgents/$(PLIST_NAME).plist
CONFIG     := $(HOME)/.config/stopkran/config.json
LOG        := $(HOME)/.local/log/stopkran.log
SOCKET     := /tmp/stopkran.sock
SETTINGS   := $(HOME)/.claude/settings.json

# ── Dependencies ──────────────────────────────────────────────

install: ## Install Python dependencies via uv
	uv sync

# ── Setup ─────────────────────────────────────────────────────

setup: install ## Interactive setup wizard (token, config, hook, launchd)
	uv run python stopkran_setup.py

# ── Daemon control ────────────────────────────────────────────

start: ## Start the daemon (foreground)
	uv run python stopkran_daemon.py

start-bg: install ## Install and start daemon via launchd
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

restart: stop start-bg ## Restart the daemon via launchd

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

# ── Cleanup ───────────────────────────────────────────────────

uninstall: stop hook-uninstall ## Full uninstall: stop daemon, remove hook, config, plist
	rm -f $(PLIST_DEST)
	rm -rf $(HOME)/.config/stopkran
	@echo "✅ Uninstalled"

# ── Help ──────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
