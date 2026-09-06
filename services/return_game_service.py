"""
    Projects how many fantasy points a kick or punt returner is worth this week.

    Return yardage is one of the few fantasy categories driven almost entirely by
    VOLUME rather than skill. A returner's own yards-per-return barely repeats week
    to week, but how many returns his team gets, and what share of them are his,
    both persist. So this service projects the volume and then simulates the yards.

    The simulation exists because this league's scoring has JUMPS in it -- crossing
    100 yards is suddenly worth 5 extra points. An average cannot represent a jump,
    so we play the game 10,000 times and count how often each outcome happens.
"""

import numpy as np
import pandas as pd

# ---------------
# League Scoring
# ---------------
RETURN_YARDS_PER_POINT = 10.0
RETURN_YARD_BONUSES = (100, 150, 200)
RETURN_BONUS_POINTS = 5.0
RETURN_TD_POINTS = 6.0
MATCHUP_SHRINK = 0.5
CURRENT_RULES_SEASONS = (2025,)

def return_plays(repo, seasons=None):
    """
        Pull out every punt & kickoff of play-by-play, label who returned it.
    """

    # Grab pbp, get only punts and kickoffs
    pbp = repo.pbp()
    st = pbp[pbp["play_type"].isin(["punt", "kickoff"])].copy()

    if seasons is not None:
        st = st[st["season"].isin(list(seasons))]

    # Set overall returner id and name
    st["returner_id"] = st["punt_returner_player_id"].fillna(
        st["kickoff_returner_player_id"])
    st["returner_name"] = st["punt_returner_player_name"].fillna(
        st["kickoff_returner_player_name"])

    # Set Return Team and Kick Team
    is_kickoff = st["play_type"].eq("kickoff")
    st["return_team"] = st["posteam"].where(is_kickoff, st["defteam"])
    st["kick_team"] = st["defteam"].where(is_kickoff, st["posteam"])

    # Mark whether there was a return
    fair_caught = st["punt_fair_catch"].fillna(0).eq(1)
    st["is_return"] = st["returner_id"].notna() & ~fair_caught

    return st[["game_id", "season", "week", "play_type", "kick_team",
               "return_team", "returner_id", "returner_name", "is_return",
               "return_yards", "touchdown"]]

def score_return_line(yards, tds):
    """
        Turn return yards and tds into league's fantasy points.
    """
    points = yards / RETURN_YARDS_PER_POINT + RETURN_TD_POINTS * tds
    for threshold in RETURN_YARD_BONUSES:
        points = points + RETURN_BONUS_POINTS * (yards >= threshold)
    return points

def returner_usage(plays, season, through_week=None):
    """
        Work out how much return work each player gets, punts and kickoffs together.
    """
    d = plays[plays["season"] == season]
    if through_week is not None:
        d = d[d["week"] <= through_week]

    # For both kickoffs and punts
    frames = []
    for kind, tag in (("kickoff", "ko"), ("punt", "pr")):
        sub = d[d["play_type"] == kind]

        # Calculate Team Returns Per Game
        games = sub.groupby("return_team")["game_id"].nunique()
        team_returns = sub.groupby("return_team")["is_return"].sum()
        team_rate = team_returns / games

        # Calculate Player Usage in Returns
        returned = sub[sub["is_return"]]
        usage = (returned.groupby(["returner_id", "returner_name", "return_team"])
                 .size().rename("n").reset_index())

        # Calculate Return Share, Expected Returns
        usage[f"{tag}_share"] = usage["n"] / usage["return_team"].map(team_returns)
        usage[f"exp_{tag}_returns"] = (usage["return_team"].map(team_rate)
                                       * usage[f"{tag}_share"])

        frames.append(usage.drop(columns="n").set_index(
            ["returner_id", "returner_name", "return_team"]))

    # outer join so a pure punt returner is not dropped by the kickoff frame.
    out = frames[0].join(frames[1], how="outer").fillna(0.0).reset_index()
    out["exp_total_returns"] = out["exp_ko_returns"] + out["exp_pr_returns"]
    return out.sort_values("exp_total_returns", ascending=False).reset_index(drop=True)

def yardage_pools(plays, seasons=CURRENT_RULES_SEASONS):
    """
        Collect real returns to draw from, keep separate by kick type
    """
    returned = plays[plays["is_return"]
                     & plays["season"].isin(list(seasons))
                     & plays["return_yards"].notna()]

    return {kind: (group["return_yards"].to_numpy(float),
                   group["touchdown"].fillna(0).to_numpy(float))
            for kind, group in returned.groupby("play_type")}

def opponent_multipliers(plays, season, through_week=None, shrink=MATCHUP_SHRINK):
    """
        Return a mapping of team abbreviation to a multiplier representative
                of how many kickoff returns they handoff. This comes from obtaining a ratio
                of how many kickoff returns are allowed by the team per game against league
                average, and shrinks the ratio (shrink)% of the way towards 1.0 (league average).
    """

    d = plays[(plays["season"] == season) & plays["play_type"].eq("kickoff")]
    if through_week is not None:
        d = d[d["week"] <= through_week]

    allowed = (d.groupby(["kick_team", "game_id"])["is_return"].sum()
               .groupby("kick_team").mean())
    return 1.0 + shrink * (allowed / allowed.mean() - 1.0)

def simulate_returner(exp_ko_returns, exp_pr_returns, pools,
                      ko_multiplier=1.0, n_sims=10_000, seed=0):
    """
        Play one player's game 10,000 times and see how fantasy scoring lands.
        
                Averages wouldn't accomodate Fantasy Scoring jumps, so we utilize simulation.
    """

    # Generate Random Number, Establish Storage
    rng = np.random.default_rng(seed)
    yards = np.zeros(n_sims)
    tds = np.zeros(n_sims)

    # Scale expected ko returns by ko multiplier (opponent matchup)
    rates = {"kickoff": exp_ko_returns * ko_multiplier, "punt": exp_pr_returns}

    for kind, rate in rates.items():
        if rate <= 0 or kind not in pools:
            continue

        # Draw how many returns he gets per fake game
            #   Uses Poisson sample - standard way to randomize an event count
        pool_yards, pool_tds = pools[kind]
        n_returns = rng.poisson(rate, size=n_sims)
        total = int(n_returns.sum())
        if total == 0:
            continue

        # Select that many returns (as integers to grab from the pool)
        picks = rng.integers(0, len(pool_yards), size=total)
        # Records which fake game each drawn return belongs to.
        sim_id = np.repeat(np.arange(n_sims), n_returns)

        # Efficiennt way of summing values that share sim_id, faster than looping
            # Fill in yards/tds, for each sim, with the sum of the picked returns
        yards += np.bincount(sim_id, weights=pool_yards[picks], minlength=n_sims)
        tds += np.bincount(sim_id, weights=pool_tds[picks], minlength=n_sims)

    # Calculate fantasy points across sims
    points = score_return_line(yards, tds)

    # Return Sim Summary
    return {
        "exp_points": float(points.mean()),
        "median_points": float(np.median(points)),
        "p10_points": float(np.percentile(points, 10)),
        "p90_points": float(np.percentile(points, 90)),
        "prob_100": float((yards >= 100).mean()),
        "prob_td": float((tds > 0).mean()),
    }

def leaderboard(plays, season, through_week=None, opponents=None,
                min_returns=1.0, n_sims=10_000):
    """
        Builds Leaderboard of Return Expectation by Player
    """
    usage = returner_usage(plays, season, through_week)

    # REmove players expected for less than 1 return
    qualified = usage[usage["exp_total_returns"] >= min_returns].copy()

    pools = yardage_pools(plays)

    if opponents:
        multipliers = opponent_multipliers(plays, season, through_week)
        qualified["opponent"] = qualified["return_team"].map(opponents)
        qualified["ko_multiplier"] = qualified["opponent"].map(multipliers).fillna(1.0)
    else:
        qualified["opponent"] = pd.NA
        qualified["ko_multiplier"] = 1.0

    # Simulate Returner for each player in usage
    results = [
        simulate_returner(row.exp_ko_returns, row.exp_pr_returns, pools,
                          ko_multiplier=row.ko_multiplier,
                          n_sims=n_sims, seed=i)
        for i, row in enumerate(qualified.itertuples())
    ]

    out = pd.concat([qualified.reset_index(drop=True), pd.DataFrame(results)],
                    axis=1)
    return out.sort_values("exp_points", ascending=False).reset_index(drop=True)

def week_opponents(repo, season, week):
    """
        Build a lookup of who each team plays in a given week
    """
    sched = repo.schedules()
    games = sched[(sched["season"] == season) & (sched["week"] == week)]

    return {**dict(zip(games["home_team"], games["away_team"])),
            **dict(zip(games["away_team"], games["home_team"]))}