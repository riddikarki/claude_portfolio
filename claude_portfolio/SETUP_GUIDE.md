# Setup Guide — Portfolio Agents on a Free Google VM

From zero to your 8 agents running at `http://YOUR_VM_IP:8080`, on the
always-free e2-micro, powered by your Claude Max plan. Allow 1–2 hours the
first time. Steps you do in a browser are marked **[browser]** — you can ask
Claude in Chrome to walk through those with you. Steps in the VM's terminal
are marked **[VM]**, and on your laptop **[laptop]**.

---

## Part A — Create the VM (~15 min)

**A1. [browser]** Go to https://console.cloud.google.com and sign in with
riddi.karki@gmail.com. Top bar → project picker → **New project** → name it
`portfolio-agents` → Create → make sure it is selected.

**A2. [browser]** Menu ☰ → **Compute Engine → VM instances** → click
**Enable** if it asks to enable the API (takes a minute).

**A3. [browser]** Click **Create instance** and set EXACTLY these (they are
what makes it free):

- Name: `portfolio-vm`
- Region: **us-central1 (Iowa)** — must be us-west1, us-central1 or
  us-east1; any other region is billed
- Machine type: Series **E2** → **e2-micro (2 vCPU, 1 GB memory)**
- Boot disk → Change: OS **Debian** (default version), Size **30 GB**,
  Boot disk type → **Standard persistent disk** (NOT "Balanced" — Balanced
  is billed, Standard is the free one)
- Leave the rest at defaults → **Create**

When it shows a green tick, note the **External IP** (e.g. 34.68.x.x).

**A4. [browser]** Open port 8080. Menu ☰ → **VPC network → Firewall** →
**Create firewall rule**:

- Name: `allow-portfolio-8080`
- Targets: **All instances in the network**
- Source IPv4 ranges: `0.0.0.0/0`
- Protocols and ports: tick TCP, enter `8080`
- Create

(The console page is protected by your access key, so an open port is
acceptable to start. HTTPS with a domain can come later.)

**A5. [browser, optional but recommended]** Keep the IP permanent:
**VPC network → IP addresses** → next to portfolio-vm's external IP →
**Reserve static address**. A static IP attached to a running free-tier VM
costs nothing.

---

## Part B — Put the program on the VM (~15 min)

**B1. [browser]** On the VM instances page, click **SSH** next to
portfolio-vm. A terminal window opens in the browser — this is your VM.

**B2.** Get the `claude_portfolio` folder onto the VM. Two ways:

*Way 1 — GitHub (best, enables easy updates later):*

**[laptop]** in PowerShell (install Git for Windows first if `git` is not
found):

```powershell
cd C:\Users\Riddi\OneDrive\Desktop\google_sdkAgent\adk_portfolio\adk_portfolio\claude_portfolio
git init
git add .
git commit -m "Portfolio agents - Claude SDK version"
# create an empty PRIVATE repo named claude_portfolio on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/claude_portfolio.git
git push -u origin main
```

**[VM]** then:

```bash
git clone https://github.com/YOUR_USERNAME/claude_portfolio.git
cd claude_portfolio
```

(For a private repo, git asks for your GitHub username and a Personal Access
Token — create one at github.com → Settings → Developer settings → Tokens.)

*Way 2 — direct upload (no GitHub):* zip the `claude_portfolio` folder on
the laptop; in the browser SSH window click the **gear icon ⚙ → Upload
file**, upload the zip, then:

```bash
sudo apt-get install -y unzip && unzip claude_portfolio.zip && cd claude_portfolio
```

**B3. [VM]** Run the setup script (installs Python venv, Node.js, swap,
service — takes ~10 min on the small VM):

```bash
bash deploy/setup_vm.sh
```

---

## Part C — Keys and credentials (~15 min)

Three secret files go on the VM by hand. They are in `.gitignore`, so they
never travel through GitHub.

**C1. [laptop]** Get your Max plan token. In PowerShell:

```powershell
claude setup-token
```

A browser opens; log in with your Claude Max account; copy the
`sk-ant-oat01-...` token it prints. (If the `claude` command is missing,
install Claude Code first: `npm install -g @anthropic-ai/claude-code`.)
The token lasts about a year — set a reminder to redo this step next year.

**C2. [VM]** Create the .env:

```bash
cp .env.example .env
nano .env
```

Fill in `CLAUDE_CODE_OAUTH_TOKEN=` (paste the token) and
`PORTFOLIO_UI_KEY=` (invent a good password — it protects your console).
Save: Ctrl+O, Enter, Ctrl+X.

**C3. [browser SSH window]** Upload `credentials.json` and `token.json`
from the laptop's `adk_portfolio` folder (gear icon ⚙ → Upload file, twice),
then move them into place:

```bash
mv ~/credentials.json ~/token.json ~/claude_portfolio/
```

These are the same Google credentials the ADK version uses — the agents get
the identical Drive/Gmail/Calendar access, same boundaries.

---

## Part D — Start it (~5 min)

**D1. [VM]**

```bash
sudo systemctl enable --now portfolio-claude
journalctl -u portfolio-claude -f
```

Watch until you see `Uvicorn running on http://0.0.0.0:8080`. Ctrl+C exits
the log view (the service keeps running).

**D2. [browser]** Open `http://YOUR_EXTERNAL_IP:8080`. Enter your
PORTFOLIO_UI_KEY when asked. Ask something real:

> What is the landed cost position on Bumtum right now, and which numbers
> are still placeholders?

First reply is slow (cold start on a small VM) — one to two minutes for a
question that reads Drive files is normal.

**D3.** Bookmark it on your phone too — the console works from mobile.

---

## Part E — Changing agents later

Edit on the laptop → push → pull on the VM:

```powershell
# [laptop] after editing agents.py / house.py etc.
git add . ; git commit -m "what changed" ; git push
```

```bash
# [VM]
cd ~/claude_portfolio && git pull && sudo systemctl restart portfolio-claude
```

Or tell a Claude session what to change and let it edit the repo, then pull.

---

## If something goes wrong

- **Service will not start** → `journalctl -u portfolio-claude -n 50` shows
  why. Most common: a typo in `.env`, or missing token.json.
- **"Wrong access key"** in the console → the key saved in the browser does
  not match `.env`. Reload the page and re-enter it.
- **Google tool errors (403)** → the API (Gmail/Calendar/Tasks) is flagged
  on in `.env` but not enabled in Google Cloud Console for the project the
  credentials came from — same rule as the ADK version.
- **Claude limit reached** → the Max plan's 5-hour window or weekly cap is
  used up; the agents return an error until it resets. Your own Claude app
  usage shares this pool.
- **Slow / out of memory** → check swap is on: `free -h` should show 1 GB
  swap. The setup script creates it.

## Cost summary

- VM: Rs 0 forever (e2-micro, US region, 30 GB standard disk = free tier)
- Claude: Rs 0 extra (runs on your Max plan)
- Google APIs (Drive, Gmail, Calendar): Rs 0 (free quotas are far above
  this usage)
- Only real exposure: egress traffic beyond the free allowance — chat
  traffic is tiny, so in practice Rs 0.
