# Deploying DataHuddle — on a rented Linux server

Hosting the app publicly, on a rented Linux server, so other people can reach the
Daily Fantasy pages.

Written for someone who has not deployed anything before — every term is
explained at first use.

> **This is the alternative, not the plan.** The app deploys to Hugging Face
> instead — see [HUGGINGFACE.md](HUGGINGFACE.md), which is the one to follow.
> Keep this for the day a Space stops being enough: a rented server gives you
> persistent disk, a fixed IP to allowlist in Atlas, and no sleeping.
>
> Two items below are **wrong for Hugging Face** specifically:
> - **§2, gating the navigation** — done in code now, in
>   [`deployment.py`](../deployment.py).
> - **§4, the cache directory** — Hugging Face discontinued persistent storage,
>   so there is nowhere to point `NFLREADPY_CACHE_DIR`. The cache is baked into
>   the Docker image at build time instead.
>
> The sizing analysis directly below also does not constrain a Space: free
> hardware there is 16 GB of RAM, so the 1,050 MB peak that rules out Streamlit
> Community Cloud is irrelevant.

---

## What the app actually needs

Measured on the real app, because these numbers pick the server:

| | |
|---|---|
| **Peak memory during warm-up** (2 seasons of DFS) | **~1,050 MB** |
| Data retained after warm-up | ~60 MB (24 MB player-weeks, 37 MB play-by-play) |
| Each extra contest a visitor selects | +24 MB |
| Warm-up time | ~12s warm, **~2 min cold** |
| Repo + data files | 16 MB |
| Database | already on MongoDB Atlas |

The peak is **transient** — it is the play-by-play join, paid once per process —
but you have to size for it. That one number rules out Streamlit Community
Cloud, which caps at 1 GB and will run out of memory during warm-up.

**You need about 2 GB of RAM, always on.** A Hetzner CX22 (2 vCPU, 4 GB, ~€4/mo)
has comfortable headroom; the walkthrough below uses one.

---

## Before you deploy

Five things, in rough order of importance. None are done yet.

### 1. A read-only database user

The most important one. In Atlas: **Database Access → Add New Database User**,
give it the built-in role **Only read any database**, and use *that* connection
string on the server.

Hiding pages from the navigation is not a security boundary — a read-only
credential is. It means a mistake in page-gating cannot cost you your drafts.

### 2. Gate the navigation by environment

**Done in code** — `deployment.py` holds one function, `pre_draft_enabled()`,
and `streamlit_app.py` uses it to decide whether the whole **Pre-Draft** group
goes into `st.navigation`. Nothing to configure on a server: the gate is
**opt-in**, so a machine where you have set nothing shows the public app.

| `DATAHUDDLE_PRE_DRAFT` | Sidebar |
|---|---|
| unset (every deployment) | Home + the four DFS pages |
| `1` (your laptop) | the above, plus all ten Pre-Draft pages |

To get your own tools back locally, put this beside the `MONGODB_URI` export in
`~/.zshrc` and open a new terminal:

```bash
export DATAHUDDLE_PRE_DRAFT=1
```

The direction is deliberate. A variable that *hid* the pages would mean one
forgotten setting publishes your draft tools; this way a forgotten setting only
costs you a page you can see locally anyway. `"0"`, `"false"` and `"no"` all
count as off, which is worth knowing because every non-empty string is truthy
in Python — a naive check would have read `DATAHUDDLE_PRE_DRAFT=0` as a yes.

This is more than hiding links: `st.navigation` refuses to route to a page it
was not handed, so a gated page has **no URL** on a deployment. It is still not
the security boundary — that is the read-only user in step 1.

**Why the entire group, when only five pages write:**

| Page | Writes |
|---|---|
| `draft_manager.py` | creates, updates and deletes drafts |
| `draft_plan.py` | saves your plan |
| `draft_runner.py` | saves the session and markings |
| `player_profile.py` | saves markings |
| `team_profile.py` | saves markings and team notes |

The other five (`adp_comparison`, `adp_analysis`, `team_comparison`,
`sim_viewer`, `path_to_wr1`) only read — but `sim_viewer` and `path_to_wr1`
need a draft selected in the sidebar, and that picker lives with the pre-draft
tools. The group travels together.

`tests/test_navigation.py` reads the navigation back out of `streamlit_app.py`
and fails if any of the five writing pages ever lands in the public set, so
this cannot quietly regress.

### 3. `DFS_SEASONS` must not name an unpublished season

`streamlit_state.py` currently pins `[2023, 2024, 2025]`. Moving it to "current
and last" is the plan, but **setting it to `[2025, 2026]` today throws**:

```
ConnectionError: Failed to download stats_player_week_2026.parquet: 404
```

nflverse publishes a season's file only once games are played, so the DFS pages
would be down until week 1. The list needs to skip seasons that are not there
yet rather than being hard-coded forward.

### 4. A cache directory that survives restarts

**Done in code** — `repositories/nflreadpy_setup.py` turns on filesystem caching
at import, so there is no longer an environment variable to forget. It also
retries a failed download (429 and 5xx, four attempts) rather than letting a
transient nflverse 503 crash a page.

What still matters for deployment is *where* that cache lands. It defaults to
the OS cache directory, which on a container platform is wiped on every rebuild
— costing the full ~2 minute cold start each time. Point
`NFLREADPY_CACHE_DIR` at persistent storage (step 5 of the walkthrough).

### 5. Ship `data/sim/`

The `.npz` simulation artifacts are produced offline by `run_draft_sim.py` and
read at runtime. At 6.8 MB they belong in the repository — do not plan to run the
simulator on the server. They only matter if you publish the pre-draft pages.

---

## The walkthrough

### The mental model

You are renting a computer in a data centre that is always on, and running the
app on it the same way you run it locally — except nobody is sitting in front of
it. So you need three extra things: a way to **reach** it, a way to keep the app
**running**, and a way to make the address **pretty and secure**.

### Vocabulary

- **VPS / instance / server / box** — the rented computer. Hetzner calls one a
  "Cloud Server".
- **SSH** — "secure shell", a way to type commands into that computer from
  yours.
- **SSH key** — a pair of files: a *public* one you upload to the server and a
  *private* one that never leaves your laptop. Better than a password because it
  cannot be guessed.
- **root** — the all-powerful admin account. Convention is to avoid it and use a
  normal account plus `sudo` ("do this one command as admin").
- **IP address** — the server's numeric address, like `5.75.130.42`.
- **DNS / A record** — the phone book mapping `dfs.yoursite.com` to that number.
  An "A record" is one such entry.
- **Port** — a numbered door on the machine. Websites use 80 (http) and 443
  (https). Streamlit defaults to **8501**.
- **Firewall** — decides which doors are open to the internet.
- **Reverse proxy** — a program sitting in front of your app, taking public
  traffic on 443 and forwarding it to Streamlit on 8501. **Caddy** is one, and it
  gets HTTPS certificates automatically.
- **TLS / HTTPS / certificate** — the padlock. Free certificates come from
  **Let's Encrypt**; Caddy handles that for you.
- **systemd / service** — Linux's way of running a program in the background
  forever, restarting it if it crashes and again after a reboot.
- **venv** — a private Python environment for one project, so its packages do not
  collide with the system's.
- **Environment variable** — a setting passed to a program from outside, like
  `MONGODB_URI`. How secrets stay out of the code.
- **Swap** — disk space used as emergency overflow when RAM runs out. Slow, but
  it prevents a crash.

---

### Step 1 — Create the server

At hetzner.com/cloud: create a project, then a **CX22** (2 vCPU, 4 GB RAM) with
**Ubuntu 24.04**, in the region nearest your users. Add your SSH key during
creation — the console will help you generate one if you have none. Note the IP
address.

*New accounts sometimes need ID verification; billing is in EUR.*

### Step 2 — Get in, and make a normal user

```bash
ssh root@YOUR_IP

adduser huddle                    # a normal account, set a password
usermod -aG sudo huddle           # let it use sudo
rsync --archive --chown=huddle:huddle ~/.ssh /home/huddle
exit
```

From now on: `ssh huddle@YOUR_IP`.

### Step 3 — Install what is needed

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git
```

### Step 4 — Get the code and its packages

```bash
cd ~
git clone YOUR_REPO_URL datahuddle
cd datahuddle

python3 -m venv .venv             # create the private environment
source .venv/bin/activate         # start using it
pip install -r requirements.txt
```

`source .venv/bin/activate` makes `python` mean *this project's* Python. You will
see `(.venv)` appear in your prompt.

### Step 5 — Secrets and settings

```bash
mkdir -p /home/huddle/nflreadpy-cache
nano ~/datahuddle/.env
```

```
MONGODB_URI=mongodb+srv://READONLY_USER:PASSWORD@YOUR_CLUSTER.mongodb.net/
NFLREADPY_CACHE_DIR=/home/huddle/nflreadpy-cache
```

The cluster address is deliberately not written down here. It is not a
password, but this repository is public on the Space, and there is no reason to
publish the exact host to connect to alongside a note saying the IP allowlist is
wide open. Copy the real string from Atlas: **Database → Connect → Drivers**.

Filesystem caching itself is switched on in code, so only the *location* needs
setting here — somewhere that survives a restart.

Use the **read-only** Atlas user from the pre-deployment checklist.

Then, in Atlas: **Network Access → Add IP Address → your server's IP**. Atlas
blocks unknown addresses by default, so nothing will connect until you do.

### Step 6 — Prove it runs

```bash
cd ~/datahuddle && source .venv/bin/activate
set -a && source .env && set +a          # load the variables into this shell
streamlit run streamlit_app.py --server.port=8501 --server.address=0.0.0.0
```

Visit `http://YOUR_IP:8501`. The first load takes a couple of minutes while it
downloads NFL data. Once it works, `Ctrl+C`.

### Step 7 — Keep it running forever

```bash
sudo nano /etc/systemd/system/datahuddle.service
```

```ini
[Unit]
Description=DataHuddle Streamlit app
After=network.target

[Service]
User=huddle
WorkingDirectory=/home/huddle/datahuddle
EnvironmentFile=/home/huddle/datahuddle/.env
ExecStart=/home/huddle/datahuddle/.venv/bin/streamlit run streamlit_app.py \
  --server.port=8501 --server.address=127.0.0.1 --server.headless=true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Two flags matter:

- **`--server.address=127.0.0.1`** — accept connections only from the machine
  itself. Caddy becomes the only thing that can reach Streamlit.
- **`--server.headless=true`** — stop Streamlit trying to open a browser and
  asking for an email on first run.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now datahuddle
sudo systemctl status datahuddle          # should say "active (running)"
journalctl -u datahuddle -f               # live logs; Ctrl+C to stop watching
```

### Step 8 — Domain and HTTPS

At your registrar, add an **A record** for `dfs` pointing at `YOUR_IP`. Then:

```bash
sudo apt install -y caddy
sudo nano /etc/caddy/Caddyfile
```

```
dfs.yoursite.com {
    reverse_proxy 127.0.0.1:8501
}
```

```bash
sudo systemctl restart caddy
```

Caddy fetches a certificate automatically. `https://dfs.yoursite.com` now works.

### Step 9 — Close the doors you are not using

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

Port 8501 is deliberately **not** opened — only Caddy reaches it, from inside.

### Step 10 — A little insurance

4 GB is comfortable for a ~1 GB peak, but swap costs nothing and turns a rare
out-of-memory crash into a brief slowdown:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## Updating the deployment

```bash
ssh huddle@YOUR_IP
cd ~/datahuddle
git pull
source .venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart datahuddle
```

The data-refresh scripts in [UPDATING_DATA.md](UPDATING_DATA.md) still run **on
your laptop**: they write to Atlas, the server reads from Atlas, so the site
picks up new data on its own. The exception is `data/sim/*.npz`, which are files
— those reach the server through `git pull`.

---

## What goes wrong first

| Symptom | Usual cause |
|---|---|
| Blank page / connection refused | `sudo systemctl status datahuddle`, then `journalctl -u datahuddle -n 50` |
| Loads, but no data | Atlas IP allowlist, or a typo in `MONGODB_URI` |
| Slow on *every* visit, not just the first | `NFLREADPY_CACHE_DIR` not reaching the process, or not writable — check `EnvironmentFile`, then `ls -la` the directory |
| `ConnectionError: Failed to download ... 503` | nflverse is genuinely down; the four built-in retries were all refused. Check [nflverse-data/releases](https://github.com/nflverse/nflverse-data/releases) |
| No certificate | DNS has not propagated. Wait, then check `dig dfs.yoursite.com` returns your IP |
| Killed / restarting repeatedly | Out of memory. Confirm swap is on with `free -h` |

---

## Alternatives considered

| | ~Cost/mo | Notes |
|---|---|---|
| **Hetzner CX22** (2 vCPU / 4 GB) | ~€4 | Best value; this walkthrough |
| **Fly.io** (2 GB) | ~$10 | Least ops — Dockerfile, volume, TLS handled. Do not scale to zero: waking costs the 2-minute cold start |
| **DigitalOcean / Linode** (2 GB) | ~$12 | Same shape, ~3× the price |
| **Railway** (2 GB) | ~$10–15 | Easy, usage-billed |
| **Render Standard** (2 GB) | ~$25 | Good DX, poor value here |
| **Oracle Cloud Always Free** (ARM, 24 GB) | $0 | Genuinely free and ample. Capacity is often unavailable, and needs arm64 wheels — polars and pyarrow both ship them |
| Streamlit Community Cloud | $0 | **Will not fit.** 1 GB cap, no persistent disk |

Prices drift; check current rates before committing.
