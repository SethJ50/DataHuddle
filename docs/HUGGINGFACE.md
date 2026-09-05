# Deploying DataHuddle to Hugging Face

One Space, two apps, swapped when the season turns: DataHuddle from about
August to February, DataCaddie (the R/Shiny golf app) the rest of the year.

Written for someone who has not deployed anything before. See
[DEPLOYMENT.md](DEPLOYMENT.md) for the rented-Linux-server alternative, which is
more work but has none of the constraints below.

---

## The shape of it

**A "Space" is a git repository that Hugging Face builds and runs.** Push to it
and it rebuilds automatically. Yours is
`huggingface.co/spaces/SethJ50/data-caddie-shiny`, and it already runs the golf
app.

The Space holds **three branches**:

| Branch | Holds | Rewritten? |
|---|---|---|
| `main` | whatever is live right now | yes, on every switch |
| `nfl` | DataHuddle — a copy of this repo's `main` | no |
| `pga` | DataCaddie — the golf app | no |

Switching seasons means force-pushing `nfl` or `pga` onto `main`.
`scripts/switch_space.sh` does it in one command.

**Why it has to work this way.** Hugging Face always builds the file named
`Dockerfile` at the root of `main`. There is no setting to point it somewhere
else — the [Spaces config reference](https://huggingface.co/docs/hub/spaces-config-reference)
has no `dockerfile_path` field. So the only way to change which app runs is to
change what sits at that one path, and branches are the tidy way to do that.

**Why not two Spaces?** That would be simpler — pause one, unpause the other,
no force-pushing. But creating a *new* Docker Space now requires a PRO
subscription. Your Space predates that rule and is grandfathered in; a second
one is not available on a free account.

---

## Read this before the first push

Four things, in order of how much they would cost you.

### 1. The golf app exists in exactly one place

`~/Desktop/DataCaddie/data-caddie-shiny` has one git remote, and it is the
Space. There is no GitHub copy. **Right now, overwriting the Space's `main`
would destroy the golf app.**

Step 1 of the walkthrough fixes this, and it is not optional. Do it before
anything else.

### 2. MongoDB Atlas needs a read-only user *and* an open IP list

Two separate settings, and the second is what makes the first essential.

Atlas blocks connections from unknown IP addresses. Your existing
[DEPLOYMENT.md](DEPLOYMENT.md) says to allowlist the server's IP — **that does
not work here.** Hugging Face gives a Space no fixed outbound address, so there
is no single IP to allow. The only option is `0.0.0.0/0`, "allow from
anywhere."

That is exactly as wide open as it sounds, which is why the read-only user
matters so much more on Hugging Face than it would on a rented server. With the
IP list off the table, **your database password is the only thing standing
between the internet and your data.** A read-only user means the worst case is
somebody reading your draft notes, not deleting your drafts.

In Atlas: **Database Access → Add New Database User**, role **Only read any
database**, a long generated password. Then **Network Access → Add IP Address →
Allow access from anywhere**.

### 3. A public Space publishes your source code — and your data files

Space visibility is set in Settings. A **public** Space means anyone can read
the repository: every `.py` file, and everything in `data/`.

Two consequences worth deciding on deliberately:

- **`data/ffb/` holds paid subscription content.** The UDK rankings and the
  Ultimate Draft Kit projections are products somebody sells. Publishing them
  in a public repository is redistributing them. Worth a look at what you agreed
  to before this goes up.
- Your Mongo connection string is not in the repository (it lives in
  `~/.zshrc`), so it will not leak this way. It goes in Space *secrets*, step 3.

If either is a problem, the options are **protected** visibility (source
private, app still public — PRO only) or moving those CSVs into MongoDB where
the rest of your data already lives.

### 4. What Hugging Face does not give you

| | |
|---|---|
| **RAM** | 16 GB, so the ~1,050 MB warm-up peak is a non-issue |
| **CPU** | 2 vCPU |
| **Disk** | 50 GB, **wiped on every restart** |
| **Persistent storage** | discontinued — the setting is ignored |
| **Sleep** | free Spaces sleep after inactivity and rebuild on the next visit |
| **Build timeout** | 30 minutes by default |

The wiped disk is the interesting one. It kills the plan in
[DEPLOYMENT.md](DEPLOYMENT.md) §4 of pointing `NFLREADPY_CACHE_DIR` at storage
that survives a restart — there is no such storage any more.

The replacement is better: the `Dockerfile` runs
`scripts/warm_nflverse_cache.py` during the **build**, downloading all fourteen
nflverse files into the image itself. Images do survive restarts. A Space
waking from sleep already has its data.

This is insurance, not a guarantee — nflreadpy treats a cached file as stale
after 24 hours, so a Space that wakes up two days later downloads again. It
removes the two-minute wait in the common case.

---

## The walkthrough

### Step 1 — Back up the golf app ✅ done

Done on 2026-09-05: `pga` was created at `ce9e694`, matching what was live.
Recorded here because it only ever needs doing once, and because the next
person to read this should know the safety net already exists.

What was run:

```bash
cd ~/Desktop/DataCaddie/data-caddie-shiny
git fetch origin
git push origin origin/main:refs/heads/pga
```

`origin/main:refs/heads/pga` means "take the commit the Space's `main` is on,
and put a branch called `pga` there." Note it reads from `origin/main`, not
from the local `main` — those are usually the same, but if you have ever edited
a file through the Hugging Face web editor, the Space is ahead of your laptop
and only `origin/main` is the thing actually running. Back up what is live.

To verify, compare the two commit IDs; they should be identical:

```bash
git ls-remote --heads origin
```

> Still worth doing: give the golf app a GitHub repository too. `pga` is a
> branch on the same server as `main`, so it survives this process but not a
> bad day at Hugging Face. One remote for a codebase you care about is one
> remote too few.

### Step 2 — Commit everything here

The switch script refuses to deploy a dirty working tree, because code that
only exists on your laptop is code nobody can reproduce.

```bash
cd ~/Desktop/DataHuddle
git status              # expect a long list -- 17 new files and many edits
git add -A
git commit -m "Add Hugging Face deployment"
git push origin main    # GitHub, so this exists somewhere other than the Space
```

### Step 3 — Add the secret

In the Space: **Settings → Variables and secrets → New secret**.

- Name: `MONGODB_URI`
- Value: the connection string for the **read-only** user from step 2 of the
  checklist above.

**Secret, not variable.** Variables are publicly readable and get copied into
anyone's duplicate of your Space; secrets are neither. Hugging Face injects
both into the container as environment variables at runtime, which is exactly
where `db/connection.py` looks for it.

**Do not add `DATAHUDDLE_PRE_DRAFT`.** Its absence is what hides the ten
Pre-Draft pages — see [deployment.py](../deployment.py). Adding it here would
publish your draft tools.

### Step 4 — Go live

```bash
./scripts/switch_space.sh nfl
```

It adds the Space as a remote called `space` if it is not there, pushes this
repo's `main` up to the `nfl` branch, then force-pushes that onto `main`.

Hugging Face starts building immediately. Watch the **Logs** tab, **Build**
view. Expect **10–20 minutes on the first build**: installing polars and
pyarrow is slow, and the cache warm-up downloads several hundred megabytes.
Later builds are faster, because Docker reuses the dependency layer whenever
`requirements.txt` has not changed.

Then open the Space. You should see Home and the four DFS pages, and **no
Pre-Draft group**. If Pre-Draft is showing, `DATAHUDDLE_PRE_DRAFT` got set
somewhere it should not have been.

### Step 5 — Switching, from then on

```bash
./scripts/switch_space.sh status   # which app is live?
./scripts/switch_space.sh pga      # golf season
./scripts/switch_space.sh nfl      # football season
```

Run it from `~/Desktop/DataHuddle` in every case — it reaches the golf app's
branch through the Space, so it does not need the golf code locally.

**When you change the golf app during golf season,** push it to its own branch
first, then promote:

```bash
cd ~/Desktop/DataCaddie/data-caddie-shiny
git push origin main:pga
cd ~/Desktop/DataHuddle && ./scripts/switch_space.sh pga
```

Never commit to `main` directly — through the Hugging Face web editor, for
instance. `main` is overwritten by the next switch, so anything landing there
is on a timer. `switch_space.sh status` warns you if it finds a `main` matching
neither branch.

---

## Things that go wrong

**Space stuck on "Building" then times out.** Usually the port. `app_port` in
`README.md` and `--server.port` in the `Dockerfile` must match, and the app must
bind `0.0.0.0` rather than localhost. `tests/test_deployment_config.py` checks
all three, so run `pytest` before pushing.

**Pages load, then error on data.** `MONGODB_URI` missing or blocked. Nothing
touches the database until the first data page, so this shows up as a page
failing rather than the app failing to start. Check the secret exists, then
check Atlas Network Access allows `0.0.0.0/0`.

**Build log says `FAILED: play-by-play`.** nflverse serves through GitHub, which
returns a 503 often enough to matter. The warm-up deliberately swallows these
and exits successfully — a partly-warm image still works, it is just slower on
first load. Push again later if you want a fully warm one.

**First visit after a few days is slow.** Expected. The Space slept, the baked
cache aged past its 24-hour freshness window, and nflreadpy is re-downloading.

**The Space is named after golf.** It is — `data-caddie-shiny`, all year. The
URL is historical; `title: DataHuddle` in `README.md` is what makes the page
itself show the right name. Renaming the Space would change the URL for both
apps, so it stays.
