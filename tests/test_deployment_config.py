"""Checks the deployment files agree with each other and with the app.

Everything here fails only on Hugging Face, minutes after a push, with an error
message that does not say what is wrong -- a port typo shows up as a Space
stuck on "Building", and a stale cache warmer shows up as nothing at all
except a slow page. Both are miserable to diagnose from a build log, and both
are trivially checkable here.

Nothing in this file builds a Docker image or talks to Hugging Face. The files
are read as text, which is enough: the mistakes worth catching are
disagreements BETWEEN files, not anything that needs a container to notice.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DOCKERFILE = (ROOT / "Dockerfile").read_text()
README = (ROOT / "README.md").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text()


def readme_frontmatter():
    """Pull the Hugging Face settings out of the top of README.md.

    A Space is configured by a block of `key: value` lines fenced by `---` at
    the very start of its README. This reads that block without needing a YAML
    library, since every value in it is a plain string or number.

    Steps:
        1. Match the opening `---`, everything up to the next `---`, and stop.
        2. Split what is between them into lines.
        3. Split each line at its first colon into a key and a value.

    Returns:
        dict: Setting names to values, both as strings, such as
            `{"sdk": "docker", "app_port": "7860"}`.

    Raises:
        AssertionError: If README.md has no frontmatter block at all, which
            would mean Hugging Face ignores every setting in it.
    """
    match = re.match(r"^---\n(.*?)\n---\n", README, re.DOTALL)
    assert match, "README.md has no --- frontmatter block; HF cannot configure the Space"

    settings = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            settings[key.strip()] = value.strip()
    return settings


# ---------------------------------------------------------------------------
# The README and the Dockerfile have to agree
# ---------------------------------------------------------------------------
# Hugging Face routes public traffic to whatever `app_port` says, and the app
# listens on whatever the Dockerfile's CMD says. Nothing checks that those
# match: if they disagree the container runs perfectly and is simply
# unreachable, which the Space reports as a startup timeout half an hour later.


def test_the_space_is_configured_as_a_docker_space():
    settings = readme_frontmatter()

    assert settings.get("sdk") == "docker", (
        "sdk must be 'docker' -- this app is not a Gradio or static Space")


def test_the_advertised_port_is_the_port_the_app_listens_on():
    settings = readme_frontmatter()
    advertised = settings.get("app_port")

    assert advertised, "README frontmatter is missing app_port"

    # `--server.port=7860` in the CMD line.
    served = re.search(r"--server\.port=(\d+)", DOCKERFILE)
    assert served, "Dockerfile CMD does not set --server.port"

    assert served.group(1) == advertised, (
        f"README says app_port {advertised} but the Dockerfile serves on "
        f"{served.group(1)}; the Space would be unreachable")

    exposed = re.search(r"EXPOSE\s+(\d+)", DOCKERFILE)
    assert exposed and exposed.group(1) == advertised, (
        "EXPOSE does not match app_port")


def test_the_container_listens_on_every_interface():
    # Streamlit defaults to localhost, which inside a container means only the
    # container itself can connect -- so the Space builds, starts, passes its
    # own health check from within, and is unreachable from outside.
    assert "--server.address=0.0.0.0" in DOCKERFILE, (
        "without --server.address=0.0.0.0 nothing outside the container can connect")


def test_the_dockerfile_runs_the_real_entry_point():
    match = re.search(r'"streamlit", "run", "([^"]+)"', DOCKERFILE)
    assert match, "could not find the streamlit run command in the Dockerfile"

    assert (ROOT / match.group(1)).is_file(), (
        f"Dockerfile runs {match.group(1)}, which does not exist")


# ---------------------------------------------------------------------------
# The cache warmer has to keep up with the repositories
# ---------------------------------------------------------------------------
# scripts/warm_nflverse_cache.py pre-downloads NFL data at image build time,
# because Hugging Face has no disk that survives a restart. It names each
# download explicitly, so adding a new `load_*` call to a repository silently
# leaves that one cold -- the app still works, it is just slow in a way nobody
# would connect back to this file. This test is the connection.


def test_every_nflverse_download_the_app_makes_is_warmed():
    warmer = (ROOT / "scripts" / "warm_nflverse_cache.py").read_text()

    # Both repositories call these two ways: `nfl.load_x(...)` directly, and
    # `self._loader.load_x(...)` through the injected loader the tests fake.
    # Matching on the leading dot covers both without listing either.
    used = set()
    for path in (ROOT / "repositories").glob("*.py"):
        used.update(re.findall(r"\.(load_[a-z_]+)\(", path.read_text()))

    assert used, "found no nflreadpy load_ calls -- has the repository layer moved?"

    missing = sorted(name for name in used if name not in warmer)
    assert not missing, (
        f"these downloads are not pre-warmed, so the first page load pays for "
        f"them: {missing}. Add them to scripts/warm_nflverse_cache.py.")


def test_the_warmer_runs_the_way_the_dockerfile_runs_it():
    # This one caught a real bug. Running `python scripts/warm_nflverse_cache.py`
    # puts scripts/ on the import path rather than the project root, so
    # `import repositories` fails -- but only when invoked that way. Importing
    # the module from a test passes happily, so nothing noticed until a Docker
    # build died on it.
    #
    # `--list` prints the planned downloads and exits without touching the
    # network, so this reproduces the exact invocation for free.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "scripts/warm_nflverse_cache.py", "--list"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, (
        f"the cache warmer fails when run as the Dockerfile runs it:\n"
        f"{result.stderr}")
    assert result.stdout.strip(), "--list printed nothing"


def test_the_dockerfile_and_the_warmer_agree_on_the_cache_directory():
    # The warm-up writes wherever NFLREADPY_CACHE_DIR points during the BUILD,
    # and the running app reads wherever it points at RUN time. One ENV line
    # sets both, so the failure mode is subtle: drop the line and the build
    # still succeeds, still logs "cache warm complete", and the image is cold.
    assert "NFLREADPY_CACHE_DIR" in DOCKERFILE, (
        "Dockerfile must set NFLREADPY_CACHE_DIR, or the baked cache lands "
        "somewhere the app will not look for it")


def test_the_warmer_asks_for_the_same_slices_the_app_does():
    # Several loaders take a `stat_type`, and each value is a SEPARATE
    # download. Warming load_ff_opportunity with the wrong one leaves the file
    # the app actually reads uncached, while looking warmed in the build log.
    warmer = (ROOT / "scripts" / "warm_nflverse_cache.py").read_text()
    repo = (ROOT / "repositories" / "dfs_read_repo.py").read_text()

    requested = set(re.findall(r'stat_type\s*=\s*"([a-z]+)"', repo))
    for stat_type in requested:
        assert f'"{stat_type}"' in warmer, (
            f'dfs_read_repo.py requests stat_type="{stat_type}" but the cache '
            f"warmer never downloads it")


# ---------------------------------------------------------------------------
# The image has to contain what the app imports
# ---------------------------------------------------------------------------


def test_dockerignore_does_not_exclude_imported_packages():
    # The tempting mistake: the draft pages are hidden on a public deploy, so
    # draft_model/ looks like dead weight. But app_context.py imports
    # DraftSimService at module level, so leaving it out means the app cannot
    # start at all -- an ImportError long before any page-gating runs.
    imported = ("adapters", "draft_model", "presentation", "pages",
                "repositories", "services", "db", "data")

    lines = [line.strip().rstrip("/") for line in DOCKERIGNORE.splitlines()
             if line.strip() and not line.startswith("#")]

    for package in imported:
        assert package not in lines, (
            f"{package}/ is excluded from the image but the app imports it")


def test_the_simulation_artifacts_stay_out_of_the_image():
    # ~81 MB of .npz files that only the gated Sim Viewer reads. Already
    # untracked by git; this keeps a local `docker build` from copying them in.
    assert "data/sim/" in DOCKERIGNORE
