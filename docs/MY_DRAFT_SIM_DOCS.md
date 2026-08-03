# Overview
Seth's written overview of how draft simulation works!

## Overall Flow
`scripts/run_draft_sim.py`
- `ctx` Get app context  
- get `drafts` from db (`draft_service.list_drafts()`)
    - (use `--list` to list info about existing drafts)  
- `run_one()` for each draft selected in argument  
    - **Build Draft Config:** Set `config` from `DraftConfig.from_draft_doc()` for an existing draft in the db.
        - IF EXISTS, skips and returns
    - **Build Model Table:** make `table` with `draft_sim_service.build_model_table()`  
    - **Calibrate Draft Sim:** get `mu, sd, trace` from calibration run `calibrate_sampler()`
    - **Full Sim Run:**: Run full run with `monte_carlo_sim()`  
    - **Validate Sims:** run validation with `validate_sim()`  
    - **Save Simulation Results:** save with `save_picks_matrix()`  

## Data
### Sources
*ADP:* ESPN / Yahoo / Sleeper  
*Projections:* Fantasy Footballers  
*Draft Standard Deviation:* Fantasy Football Calculator  

## Model Table
`data_model/table.py`
**Overview:**

**Functions:**
- `blend_adp(sources, weights)`: Combine ADP from multiple sources into one consensus number.
- `apply_platform_shift(ffc_adp, platform_adp, weight)`: Nudge FFC ADP toward the platform you actually draft on.
- `fill_missing_stdev(df, adp_column, n_neighbors)`: Give every player a usable width - estimate with 1) high-low or 2) median stdev of adp neighbors with same position.
- `build_table(config, ffc, platform_adp, enrichments, platform_weight, pool_multiplier)`: Assemble the flat table the entire model runs on.
    - 1) Validate required FFC columns exist
    - 2) Set `table['adp_target'] to FFC ADP nudged towards platform ADP
    - 3) Fill any missing stdev's using `fill_missing_stdev()`
    - 4) Remove any players drafted too high to be considered in this draft sim.
    - 5) Add on enrichments as columns (here, FFB projections)
    - 6) Set `mu`, `sd` as `adp_target` and `stdev_target`, meaningless until `calibrate_sampler()` overwrites them
    - 7) Sort and return in order of `adp_target`

## Draft Config
`draft_model/config.py`
**Overview:** League configuration and the model's tunable constants.

**Constants:**
- UNDRAFTED: 'Sentinel' for 'never selected'
- RHO: How much simulated managers agree with each other within ONE draft
- ALPHA: Damping on calibration update
- NEED_BONUS: 'Board value units' a manager will reach past to fill an empty starter slot.
- BLOCK: Added value of a player at a position the manager has already filled
- HARD_LIMIT: Max of each position to select in a draft
- POSITIONS: Canonical position order, list of "QB", "RB", ...
- STARTER_DEADLINE: Pick num past which a manager holding ZERO players at a pos starts reaching for one.
- PLATFORM_WEIGHT: How far to shift FFC's ADP towards your platform
- POOL_MULTIPLIER: Drop players with ADP beyond total_picks * this
- MIN_STDEV: Hard floor on width - applied last
- DEFAULT_STARTING_SLOTS: Fallback lineup for drafts saved before starting_slots existed

### `DraftConfig` (Class)
**Properties:**
- year
- num_teams
- num_rounds
- draft_position
- scoring_format
- platform
- starting_slots
- keepers
- roster_size
- third_round_reversal
- random_seed

**Methods:**
- `__post_init__(self)`: Reject impossible league configuration at construction time
- `fingerprint(self)`: Creates a short string from hash based on draft config
- `from_draft_doc(cls, doc, year, **overrides)`: Build a config from a saved draft document.

## Draft Sim Service
`services/draft_sim_service.py`
**Overview:** Wires the draft model to the app's data and to saved simulation runs.

**Constants:**
- BASE_PLATFORM_WEIGHTS
- DRAFTING_PLATFORM_WEIGHT

### `DraftBoard` (class)
**Properties:**
- config
- table
- artifact
- vorp
- replacement
- stale

**Methods:**
- `availability(self, target_picks)`: Returns a Df where for each player exists a row: [name, position, team, adp_target, projection, vorp, cost_of_waiting, and a P@Pick column for each of your target picks]
- `positional_costs(self, at_pick, next_pick)`: Returns a Df with columns [position, best_available_vorp, cost] for the current pick (at_pick)
- `tier_survival(self, player_names, target_pick)`: Returns the probability at least one of a chosen group lasts to a pick.

### `DraftSimService` (class)
**Methods:**
- `platform_blend(self, fmt, platform)`: Creates one consensus ADP per player from ESPN, Yahoo and Sleeper
- `build_model_table(self, config)`: Assemble table the simulation runs on from live app data [ffc_player_id, canonical_id, name, position, team, adp_target_stdev_target, times_drafted, mu, sd, projection]
    - 1) Grab FFC Data with `_ffc_service.with_canonical_id(config.scoring_format)`
    - 2) Grab fantasy projections with `_projections_service.get_own_projections()`
    - 3) Return result of `build_table(config, ffc, platform_adp, enrichments, platform_weight)`
        - `enrichments`: FFB projections as "projections"
- `artifact_form(self, draft_id, config)`: Where this draft's sim lives given current settings.
- `has_simulation(self, draft_id, config)`: Whether a sim exists for these exact settings.
`load_board(self, draft_doc, year)`: Loads everything the Draft Plan page needs for one draft - DraftBoard with table, artifact, VORP and replacement level aligned.

## Calibration
`data_model/calibrate.py`
**Overview:**

**Functions:**
- `simulated_mean_pick(picks)`: Compute average pick number per player, ignoring drafts where he went undrafted.
- `simulated_stdev_pick(picks)`: Spread of pick per player, ignoring undrafted outcomes.
- `prob_undrafted(picks):` Fraction of simulations in which each player went undrafted.
- `draft_rate(picks)`: Fraction of simulations in which each player was drafted.
- `calibrate_sampler(adp_target, stdev_target, pos_index, config, n_iterations, n_sims, sd_clip, alpha, reliability, rho, verbose)`: Solve for the sampler parameters that make the simulation reproduce the ADP and spread it was given.
    - 1) For each iteration
        - 1) Get `picks` from `monte_carlo_sim(mu, sd, pos_index, config, n_sims, rho)`
        - 2) Obtain simulated adp (`simulated_mean_picks()`), sd (`simulated_stdev_pick()`) for all picks
        - 3) Quantify `reliable` as if the `draft_rate(picks)` >= `reliability`
        - 4) Calculate `adp_error` and `sd_error`
        - 5) Make some sorts of adjustments to `mu` and `sd`
    - 2) Return adjusted `mu`, `sd`, and finalized `trace`
- `validate_sim(picks, adp_target, stdev_target, config, adp_tolerance, reliability, checkpoints, raise_on_failure)`: Assert a completed run is internally consistent and properly calibrated.
    - 1) Check error for `reliable` and `core` grouped players vs. `adp_tolerance`
    - 2) Ensure every sim drafts exactly the right number of players
    - 3) Ensure no pick number is reused inside one simulation
    - 4) Count identity - ensure k - 1 players picked by pick at each of `checkpoints`
    - 5) Check snake order is structurally sound

## Engine
`draft_model/engine.py`
**Overview:**
The simulator: plays out thousands of fake drafts and records where everyone went.

**Functions:**
- `position_index(positions)`: Convert position strings into the small integers the simulator uses.
- `position_limit_arrays()`: Repackage the per-position constants as arrays the loop can index.
- `draw_boards(mu, sd, num_teams, rng, n_sims, rho)`: Draw each simulated manager's private valuation of every player. (seemingly unused currently)
- `sim_batch(boards, pos_index, config, *, start_pick, end_pick, already_drafted, roster_counts)`: Run a whole batch of drafts at once and record where each player went.
    - 1) Prep stuff
    - 2) Loop over start_pick to end_pick
        - 1) Grab team currently drafting with `snake_order()`
        - 2) Take that team's opinions of every player as `values`
        - 3) Apply positional need and block player maxes
        - 4) Mask out players already gone
        - 5) Choose best available player on board and set this in picks
    - 3) Return `picks`
- `draw_boards_for_sims(mu, sd, num_teams, seed, sim_indices)`: Draw the boards for specific simulations, reproducibly and independently of how those simulations happen to be grouped into batches.
    - Not really exactly sure... I think it somehow draws draft boards per player per draft
- `monte_carlo_sim(mu, sd, pos_index, config, n_sims, rng, batch_size, rho, **kwargs)`: Run many independent drafts and stack the results into one matrix.
    - 1) For each sim in sims by `batch_size` (batching to enable storage)
        - 1) Set `boards` to `draw_boards_for_sims(mu, sd, config.num_teams, seed, range(start, start + size), rho)`
        - 2) Set `picks[start:start + size]` to `sim_batch(boards, pos_index, config, **kwargs)`
    - 2) Return `picks`
- `sim_one_draft_reference(board, pos_index, config, start_pick, end_pick)`: Simulate ONE draft the slow, obvious way, for tests to check against.

## Artifacts
`draft_model/artifacts.py`
**Overview:**
Saving and loading simulation results.

### `SimArtifact` (class)
**Properties:**
- picks
- player_ids
- config
- mu
- sd
- metadata

**Methods:**
- `column_for(self, player_id)`: Find the matrix column belonging to a player.
- `artifact_path(directory, draft_id, config)`: Where a run for this draft and these settings belongs on disk.
- `save_picks_matrix(path, picks, config, player_ids, *, mu, sd, metadata)`: Write a simulation run plus everything needed to interpret it.
- `load_picks_matrix(path)`: Read a saved run back.
- `matches_table(artifact, table)`: Does a saved run still describe the table in front of you?

## Mechanics
**Overview:**
Draft mechanics: which pick is it, and how does need distort a manager's board?

**Functions:**
- `snake_order(pick_num, num_teams, third_round_reversal)`: Work out which team owns a given pick.
- `picks_for_slot(draft_position, num_teams, num_rounds, third_round_reversal)`: Every absolute pick number belonging to one draft slot.
- `effective_value(base_value, position, roster_counts, pick_num)`: Adjust one manager's private value for a player, given what that other manager has already rostered.

## Queries
**Overview:**
Turning picks matrix into numbers that answer draft day questions

**Functions:**
- `prob_available_at_pick(picks, player_idx, target_pick)`: Probability a player is still on the board at a given pick.
- `availability_matrix(picks, target_picks)`: Availability for every player at every pick you own, in one pass.
- `prob_any_available(picks, player_idxs, target_pick)`: Probability AT LEAST ONE of a group is still available at a pick.
- `prob_all_available(picks, player_idxs, target_pick)`: Probability EVERY member of a group is still available at pick.
- `simulated_pick_distribution(picks, player_idx)`: Every pick number a player went at, across simulations.
- `replacement_value(projections, positions, starting_slots, num_teams, available_mask)`: Projected points of the last startable player at each position.
- `compute_vorp(projections, positions, replacement)`: Convert projections into value over replacement.
- `expected_best_at_pick(picks, columns, vorp_values, target_pick)`: Expected VORP of the best player from a group who survives to a pick.
- `cost_of_waiting(picks, player_idx, my_next_pick, vorp, positions, available_mask)`: Expected value surrendered by not taking THIS player now.
- `positional_cost_of_waiting(picks, position, at_pick, my_next_pick, vorp, positions, available_mask)`: Expected value surrendered by not addressing a POSITION until your next turn.