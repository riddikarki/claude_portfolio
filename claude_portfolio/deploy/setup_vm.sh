#!/usr/bin/env bash
# One-time VM setup for the Nepal Portfolio Claude agent server.
# Tested on Debian 12/13 (the GCP default image). Run from the project root:
#   cd ~/claude_portfolio && bash deploy/setup_vm.sh
set -euo pipefail

echo "== 1/5 System packages =="
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git curl ca-certificates

echo "== 2/5 Node.js 22 (required by the Claude Agent SDK runtime) =="
if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -c2-3)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
  sudo apt-get install -y nodejs
fi
node -v

echo "== 3/5 Swap (1 GB — the e2-micro has 1 GB RAM) =="
if ! sudo swapon --show | grep -q swapfile; then
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo "== 4/5 Python environment =="
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo "== 5/5 systemd service =="
sed "s/__USER__/$USER/g" deploy/portfolio-claude.service | sudo tee /etc/systemd/system/portfolio-claude.service >/dev/null
sudo systemctl daemon-reload

cat <<'EOF'

Setup done. Before starting the service, make sure these files exist here:
  .env              (copy .env.example, fill in CLAUDE_CODE_OAUTH_TOKEN
                     and PORTFOLIO_UI_KEY)
  credentials.json  (copied from your laptop's adk_portfolio folder)
  token.json        (copied from your laptop's adk_portfolio folder)

Then:
  sudo systemctl enable --now portfolio-claude
  journalctl -u portfolio-claude -f     # watch the logs

Console: http://EXTERNAL_IP:8080  (open port 8080 in the GCP firewall first)
EOF
