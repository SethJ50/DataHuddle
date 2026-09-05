# The recipe Hugging Face follows to build DataHuddle into a running container.
#
# A "container" is a small, self-contained Linux machine holding the app and
# everything it needs. Hugging Face rebuilds this from scratch on every push to
# the Space's `main` branch, so anything not written down here does not exist
# on the server.
#
# Read it top to bottom: each instruction adds one layer to the image, and
# Docker reuses layers it has already built. That is why requirements are
# installed BEFORE the source is copied -- editing a page then costs a
# ten-second rebuild instead of reinstalling every package.
#
# See docs/HUGGINGFACE.md for the deploy itself.

# Slim = Debian without the parts a Python app never uses; about 120 MB rather
# than 1 GB. Pinned to 3.12 rather than "latest" so a new Python release cannot
# silently break the build months from now, and rather than 3.13 (what this is
# developed on) because 3.12 has the widest supply of prebuilt wheels for
# polars, pyarrow and numpy -- without a wheel, pip compiles from source, which
# turns a two-minute build into a twenty-minute one.
FROM python:3.12-slim

# Hugging Face runs the container as user ID 1000, and files copied in as root
# would be unwritable by it. Creating a matching user up front, before anything
# is copied, is the fix HF documents.
RUN useradd -m -u 1000 user
USER user

ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# Where nflreadpy keeps its downloaded parquet files. Set here so it is the
# same directory at build time (warmed below) and at run time (read by the
# app) -- if these disagreed, the warm-up would be silently wasted.
ENV NFLREADPY_CACHE_DIR=$HOME/.cache/nflreadpy

# Python housekeeping, both worth setting in any container:
#   PYTHONUNBUFFERED   -- print immediately instead of holding output in a
#                         buffer, so the HF log viewer shows a crash as it
#                         happens rather than after the process dies.
#   PYTHONDONTWRITEBYTECODE -- skip .pyc files. They would only be written once
#                         and never reused, since the container is thrown away.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# --- Dependencies -----------------------------------------------------------
# Copied on its own, before the source, so Docker can cache this layer. Change
# a page and this step is skipped entirely; change requirements.txt and it
# reruns. --chown=user because everything under this user must belong to it.
COPY --chown=user requirements.txt .

# --no-cache-dir stops pip keeping its own copy of every downloaded package,
# which would roughly double the image size for no benefit -- nothing will ever
# install a package in here again.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# --- Application ------------------------------------------------------------
COPY --chown=user . .

# --- Pre-download the NFL data ----------------------------------------------
# Hugging Face has no disk that survives a restart, so the only place a cache
# can live is inside the image. This bakes one in. See the module docstring in
# scripts/warm_nflverse_cache.py for the full reasoning.
#
# The script swallows its own errors and always exits 0: nflverse downloads
# through GitHub, which intermittently 503s, and a failed optimisation must not
# fail a deploy. A build where this went wrong still ships -- it is just slower
# on the first page load. Check the build log to see which it was.
RUN python scripts/warm_nflverse_cache.py

# --- Serving ----------------------------------------------------------------
# 7860 is the port Hugging Face expects, and it must match `app_port` in
# README.md. tests/test_deployment_config.py fails if those two ever drift.
EXPOSE 7860

# Four flags, all necessary:
#   --server.port=7860        the port HF routes public traffic to.
#   --server.address=0.0.0.0  listen on every network interface. The default,
#                             localhost, would only accept connections from
#                             inside the container -- meaning nobody.
#   --server.headless=true    do not try to open a browser or ask for an email
#                             on first run. There is no browser in here.
#   --browser.gatherUsageStats=false
#                             skip Streamlit's telemetry ping.
CMD ["streamlit", "run", "streamlit_app.py", \
     "--server.port=7860", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
