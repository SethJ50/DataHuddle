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
    """Build, calibrate, simulate, and save one draft's availability model.

    The whole offline pipeline for a single league, in the order it has to
    happen. This is the expensive work the app itself never does.

    Steps:
        1. Build the config from the saved draft and work out where its artifact
           belongs.
        2. Print the league's settings, so the output records what was run.
        3. If --skip-existing was passed and a current artifact is already there,
           stop here.
        4. Build the model table via the sim service, and pull out the position
           numbers and the ADP and spread targets.
        5. If the league has keepers, translate them into the picks they consume
           with `DraftSimService.keeper_columns`, so the simulator reserves those
           picks and holds the kept players off the board.
        6. Calibrate with `calibrate_sampler` from draft_model/calibrate.py,
           unless --no-calibrate was passed, in which case use the raw targets.
           The keeper picks go in too, so calibration tunes against the same
           draft the full run will produce.
        7. Run the full simulation with `monte_carlo_sim` from
           draft_model/engine.py.
        8. Check the result with `validate_sim`, printing every check rather than
           stopping at the first failure.
        9. Refuse to save anything that failed validation — a bad artifact would
           be served confidently by the app. The one exception is a keeper league
           whose ONLY failure is calibration, which is expected rather than
           broken; see the comment at that step.
       10. Save the matrix with `save_picks_matrix`, recording the settings used
           and the calibration trace alongside it.

    Args:
        ctx: The shared AppContext holding every data service.
        draft: One draft document from DraftService.
        args: The parsed command-line arguments.

    Returns:
        bool: True if an artifact was written, or would have been under
            --dry-run. False if it was skipped or failed validation.

    Note:
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
        print(f"keepers : {len(config.keepers)} "
              f"({config.total_picks - len(config.keepers)} real selections)")

    if args.skip_existing and path.exists():
        print(f"  already current ({path.name}) -- skipping")
        return False

    # --- table ---------------------------------------------------------
    table = ctx.draft_sim_service.build_model_table(config)
    pos_index = position_index(table["position"])
    adp_target = table["adp_target"].to_numpy()
    stdev_target = table["stdev_target"].to_numpy()
    print(f"pool    : {len(table)} players for {config.total_picks} picks")

    # Keepers name players by canonical_id and rounds by team; the simulator
    # works in matrix columns and absolute pick numbers. Translate once, here.
    keeper_picks = ctx.draft_sim_service.keeper_columns(config, table)
    if keeper_picks:
        print(f"          {len(keeper_picks)} keepers occupy picks "
              f"{sorted(keeper_picks)}")

    sim_kwargs = {"keeper_picks": keeper_picks} if keeper_picks else {}

    # --- calibration ---------------------------------------------------
    # Calibration must simulate the SAME draft the full run will, keepers and
    # all. Without the keeper picks it would tune against a draft that makes
    # more selections than the real one, and every mu would come out slightly
    # early.
    trace = []
    if args.no_calibrate:
        print("\nskipping calibration (--no-calibrate): using raw ADP and stdev")
        mu, sd = adp_target.copy(), stdev_target.copy()
    else:
        print(f"\ncalibrating ({args.iterations} passes x {args.calibration_sims} sims):")
        mu, sd, trace = calibrate_sampler(
            adp_target, stdev_target, pos_index, config,
            n_iterations=args.iterations, n_sims=args.calibration_sims,
            keeper_picks=keeper_picks,
        )

    # --- full run ------------------------------------------------------
    print(f"\nsimulating {args.n_sims:,} drafts...")
    picks = monte_carlo_sim(mu, sd, pos_index, config, n_sims=args.n_sims, **sim_kwargs)

    # --- validation ----------------------------------------------------
    print("\nvalidation:")
    results = validate_sim(picks, adp_target, stdev_target, config,
                           raise_on_failure=False, keeper_picks=keeper_picks)
    for name, outcome in results.items():
        mark = "PASS" if outcome["passed"] else "FAIL"
        print(f"  [{mark}] {name}: {outcome['detail']}")

    failed = [n for n, o in results.items() if not o["passed"]]

    # A keeper league CANNOT reproduce vendor ADP, and that is a property of the
    # league rather than a fault in the model. Vendor ADP is measured in redraft
    # drafts; taking players out of the pool means everyone still in it genuinely
    # goes earlier than their redraft number says. Keeping ordinary players costs
    # a few tenths of a pick, but a league where every team keeps a first-rounder
    # can shift the board by ten picks or more, and no amount of calibration can
    # or should remove that.
    #
    # So calibration is downgraded to a warning HERE and only here, and only when
    # keepers exist. The other four checks are structural identities that hold
    # regardless of keepers, so they stay blocking -- they are what actually
    # catches a broken simulation.
    if keeper_picks and failed == ["calibration"]:
        print(f"\n  calibration is off by more than the tolerance, which is EXPECTED "
              f"with {len(keeper_picks)} keeper(s):")
        print(f"  removing kept players from the pool pulls everyone else earlier "
              f"than their redraft ADP.")
        print(f"  the bigger the keepers, the bigger the gap. Saving anyway; every "
              f"structural check passed.")
        failed = []

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
    """Parse the command line, choose which drafts to run, and run them.

    The entry point when this file is run from the command line.

    Steps:
        1. Define every command-line option, using this file's module docstring
           as the help text.
        2. Build the AppContext and load the saved drafts, exiting with a useful
           message if there are none.
        3. If --list was passed, print each draft with whether its simulation is
           current, then stop without running anything.
        4. Choose the targets: every draft with --all, one specific draft with
           --draft-id, or the first saved draft otherwise.
        5. Run each through `run_one` above, counting how many were written.
           Since `run_one` returns rather than raises, one bad league does not
           stop the rest.
        6. Print a summary when more than one draft was targeted.

    Returns:
        None: Progress is printed as it goes.

    Raises:
        SystemExit: If no drafts are saved, or if --draft-id names one that does
            not exist. Both print an actionable message first.
    """
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
