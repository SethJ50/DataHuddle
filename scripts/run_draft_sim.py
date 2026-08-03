"""Build the model table, calibrate, run the full simulation, save the artifact.

This is the offline half of the design (DESIGN.md 8). Streamlit reruns the whole
script on every interaction, so the expensive work happens here and the app only
ever loads the result.

Usage:
    python scripts/run_draft_sim.py --list
    python scripts/run_draft_sim.py --dry-run
    python scripts/run_draft_sim.py
    python scripts/run_draft_sim.py --draft-id abc123 --n-sims 20000
    python scripts/run_draft_sim.py --all               # every saved draft
    python scripts/run_draft_sim.py --all --skip-existing   # only what's missing
    python scripts/run_draft_sim.py --no-calibrate      # compare against raw ADP
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from app_context import AppContext
from draft_model.artifacts import artifact_path, save_picks_matrix
from draft_model.calibrate import calibrate_sampler, validate_sim
from draft_model.config import PLATFORM_WEIGHT, RHO, DraftConfig
from draft_model.engine import monte_carlo_sim, position_index

SEASONS = [2024, 2025]

def run_one(ctx, draft, args) -> bool:
    """
    Purpose: Build, calibrate, simulate and save one draft's availability model.

    Parameters:
        ctx (AppContext): Shared data services.
        draft (dict): One draft document from DraftService.
        args: Parsed command-line arguments.

    Returns:
        bool: True if an artifact was written (or would have been, under
        --dry-run). False if it was skipped or failed validation.

    Notes:
        Returns rather than raises so that --all keeps going after one draft
        fails. A single bad league should not stop the others being refreshed.
    """
    config = DraftConfig.from_draft_doc(draft, year=args.year)
    path = artifact_path(PROJECT_ROOT / "data" / "sim", draft["draft_id"], config)

    print(f"\n{'=' * 70}")
    print(f"draft   : {draft['name']}")
    print(f"league  : {config.num_teams} teams, pick {config.draft_position}, "
          f"{config.num_rounds} rounds, {config.scoring_format.value}, "
          f"drafting on {config.platform}")
    print(f"picks   : {config.my_picks}")
    if config.keepers:
        print(f"keepers : {len(config.keepers)}")

    if args.skip_existing and path.exists():
        print(f"  already current ({path.name}) -- skipping")
        return False

    # --- table ---------------------------------------------------------
    table = ctx.draft_sim_service.build_model_table(config)
    pos_index = position_index(table["position"])
    adp_target = table["adp_target"].to_numpy()
    stdev_target = table["stdev_target"].to_numpy()
    print(f"pool    : {len(table)} players for {config.total_picks} picks")

    # Keepers are stored as canonical_ids; the simulation works in table rows.
    already_drafted = None
    if config.keepers:
        kept = table["canonical_id"].isin(config.keepers).to_numpy()
        already_drafted = kept
        print(f"          {int(kept.sum())} keepers removed from the pool")

    sim_kwargs = {} if already_drafted is None else {"already_drafted": already_drafted}

    # --- calibration ---------------------------------------------------
    trace = []
    if args.no_calibrate:
        print("\nskipping calibration (--no-calibrate): using raw ADP and stdev")
        mu, sd = adp_target.copy(), stdev_target.copy()
    else:
        print(f"\ncalibrating ({args.iterations} passes x {args.calibration_sims} sims):")
        mu, sd, trace = calibrate_sampler(
            adp_target, stdev_target, pos_index, config,
            n_iterations=args.iterations, n_sims=args.calibration_sims,
        )

    # --- full run ------------------------------------------------------
    print(f"\nsimulating {args.n_sims:,} drafts...")
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=args.n_sims, **sim_kwargs)

    # --- validation ----------------------------------------------------
    print("\nvalidation:")
    results = validate_sim(picks, adp_target, stdev_target, config,
                           raise_on_failure=False)
    for name, outcome in results.items():
        mark = "PASS" if outcome["passed"] else "FAIL"
        print(f"  [{mark}] {name}: {outcome['detail']}")

    failed = [n for n, o in results.items() if not o["passed"]]
    if failed:
        print(f"\n  {len(failed)} check(s) failed: {', '.join(failed)}")
        if not args.dry_run:
            print("  refusing to save an artifact that fails validation")
            return False

    # --- save ----------------------------------------------------------
    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return True

    save_picks_matrix(
        path, picks, config, table["ffc_player_id"], mu=mu, sd=sd,
        metadata={
            "rho": RHO,
            "platform_weight": PLATFORM_WEIGHT,
            "drafting_platform": config.platform,
            "calibrated": not args.no_calibrate,
            "calibration_trace": trace,
            "n_pool": len(table),
        },
    )
    print(f"\nsaved {path.name} ({path.stat().st_size / 1e6:.2f} MB)")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft-id", help="which saved draft (default: the only/first one)")
    parser.add_argument("--all", action="store_true",
                        help="run every saved draft, not just one")
    parser.add_argument("--skip-existing", action="store_true",
                        help="leave drafts alone if a current artifact already exists; "
                             "pair with --all to top up only what's missing")
    parser.add_argument("--list", action="store_true", help="list saved drafts and exit")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--calibration-sims", type=int, default=2_000)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--no-calibrate", action="store_true",
                        help="run on raw ADP/stdev; useful for measuring what calibration buys")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and calibrate, but write no artifact")
    args = parser.parse_args()

    ctx = AppContext(SEASONS)
    drafts = ctx.draft_service.list_drafts()
    if not drafts:
        raise SystemExit("no saved drafts. Create one on the Draft Manager page.")

    if args.list:
        service = ctx.draft_sim_service
        for d in drafts:
            config = DraftConfig.from_draft_doc(d, year=args.year)
            current = service.has_simulation(d["draft_id"], config)
            print(f"  {d['draft_id']}  {d['name']}")
            print(f"      {d['num_teams']} teams, pick {d['draft_position']}, "
                  f"{d['num_rounds']} rounds, {d['scoring_format']}, on {d['platform']}")
            print(f"      simulation: {'current' if current else 'MISSING or out of date'}")
        return

    if args.all:
        targets = drafts
    elif args.draft_id:
        targets = [d for d in drafts if d["draft_id"] == args.draft_id]
        if not targets:
            raise SystemExit(f"no draft with id {args.draft_id}. Try --list.")
    else:
        targets = [drafts[0]]

    written = sum(run_one(ctx, draft, args) for draft in targets)

    if len(targets) > 1:
        print(f"\n{'=' * 70}")
        print(f"{written} of {len(targets)} drafts written.")


if __name__ == "__main__":
    main()
