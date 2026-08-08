# nflreadpy — full column reference

Every column of every nflreadpy table this app can reach, grouped so you can find
things. Extracted from the 2024 season, so row counts are per season and the
column lists are exactly what the library returns.

**Companion to [BACKEND_REFERENCE.md](BACKEND_REFERENCE.md)** — that document
explains how these sources are joined and which service turns each into something
a page can draw. This one is the raw catalogue.

## How to read it

- Types are simplified: `int`, `num`, `text`, `bool`, `date`.
- In the play-by-play section, **bold** columns are the 44 the app currently keeps
  (`PBP_COLUMNS` in `repositories/dfs_read_repo.py`). Everything else is available
  but would need adding to that list — see the warning below.
- Column *meanings* are documented by nflverse itself; the dictionaries are linked
  at the top of each section. This is a catalogue of what exists, not a rewrite of
  their reference.

> ⚠️ **Play-by-play is pruned at load.** The raw table is 372 columns and 372 MB
> per season; the app selects 44 of them in Polars before converting to pandas,
> which is what gets it to 19 MB. Adding a column means adding it to `PBP_COLUMNS`
> and reloading — expected, not a failure.

## Contents

| Loader | Rows / season | Columns | In the app? |
|---|---|---|---|
| [`load_pbp`](#load_pbp) | 49,492 | 372 | yes — pruned to 44 |
| [`load_player_stats`](#load_player_stats) | 18,981 | 145 | yes |
| [`load_team_stats`](#load_team_stats) | 570 | 133 | yes |
| [`load_ff_opportunity`](#load_ff_opportunity) | 6,005 | 159 | yes |
| [`load_snap_counts`](#load_snap_counts) | 26,615 | 16 | yes |
| [`load_nextgen_stats.passing`](#load_nextgen_statspassing) | 614 | 29 | yes |
| [`load_nextgen_stats.rushing`](#load_nextgen_statsrushing) | 601 | 22 | yes |
| [`load_nextgen_stats.receiving`](#load_nextgen_statsreceiving) | 1,435 | 23 | yes |
| [`load_pfr_advstats.pass`](#load_pfr_advstatspass) | 697 | 24 | available |
| [`load_pfr_advstats.rush`](#load_pfr_advstatsrush) | 2,359 | 16 | yes |
| [`load_pfr_advstats.rec`](#load_pfr_advstatsrec) | 4,453 | 17 | yes |
| [`load_pfr_advstats.def`](#load_pfr_advstatsdef) | 7,992 | 29 | available |
| [`load_schedules`](#load_schedules) | 285 | 46 | yes |
| [`load_teams`](#load_teams) | 36 | 16 | yes |
| [`load_players`](#load_players) | 25,038 | 39 | yes |
| [`load_ff_playerids`](#load_ff_playerids) | 12,470 | 35 | yes — the PFR crosswalk |
| [`load_rosters_weekly`](#load_rosters_weekly) | 46,579 | 36 | available |
| [`load_injuries`](#load_injuries) | 6,215 | 16 | available |
| [`load_depth_charts`](#load_depth_charts) | 37,312 | 15 | available |

---

## `load_pbp`

**49,492 rows** (2024) · **372 columns**

One row per play. The biggest table by far, and the only one this app trims on the way in.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html)

### Game & identity (40)

`away_coach` · `away_score` · **`away_team`** · **`defteam`** · `div_game` · `game_date` ·
`game_half` · **`game_id`** · **`game_seconds_remaining`** · `game_stadium` ·
**`half_seconds_remaining`** · `home_coach` · `home_score` · **`home_team`** · `location` ·
`nfl_api_id` · `old_game_id` · `play_clock` · **`play_id`** · **`posteam`** · `posteam_type` ·
`quarter_end` · `quarter_seconds_remaining` · `result` · `roof` · **`season`** · `season_type` ·
`sp` · `spread_line` · `stadium` · `stadium_id` · `start_time` · `surface` · `temp` ·
`time_of_day` · `total` · `total_line` · `weather` · **`week`** · `wind`

### Drive & series (23)

**`drive`** · `drive_end_transition` · `drive_end_yard_line` · `drive_ended_with_score` ·
`drive_first_downs` · `drive_game_clock_end` · `drive_game_clock_start` · `drive_inside20` ·
`drive_play_count` · `drive_play_id_ended` · `drive_play_id_started` · `drive_quarter_end` ·
`drive_quarter_start` · `drive_real_start_time` · `drive_start_transition` ·
`drive_start_yard_line` · `drive_time_of_possession` · `drive_yards_penalized` · `fixed_drive` ·
`fixed_drive_result` · `series` · `series_result` · `series_success`

### Situation (31)

`aborted_play` · `away_timeouts_remaining` · `defteam_score` · `defteam_score_post` ·
`defteam_timeouts_remaining` · **`down`** · `end_clock_time` · `end_yard_line` ·
**`goal_to_go`** · `home_timeouts_remaining` · `order_sequence` · `play_deleted` ·
`play_type_nfl` · `posteam_score` · `posteam_score_post` · `posteam_timeouts_remaining` ·
**`qtr`** · **`score_differential`** · `score_differential_post` · `side_of_field` ·
`special_teams_play` · `st_play_type` · `time` · `timeout` · `timeout_team` · `total_away_score`
· `total_home_score` · **`yardline_100`** · `ydsnet` · **`ydstogo`** · `yrdln`

### Play call (34)

`defensive_extra_point_attempt` · `defensive_two_point_attempt` · `extra_point_attempt` ·
`field_goal_attempt` · **`first_down`** · `first_down_pass` · `first_down_penalty` ·
`first_down_rush` · `fourth_down_converted` · `fourth_down_failed` · `kickoff_attempt` ·
`no_huddle` · **`pass`** · `pass_attempt` · `pass_length` · `pass_location` · **`pass_oe`** ·
**`penalty`** · **`play_type`** · `punt_attempt` · **`qb_dropback`** · **`qb_kneel`** ·
`qb_scramble` · **`qb_spike`** · `run_gap` · `run_location` · **`rush`** · `rush_attempt` ·
`shotgun` · **`special`** · `third_down_converted` · `third_down_failed` ·
**`two_point_attempt`** · **`xpass`**

### Outcome — yards, turnovers, scores (66)

**`air_yards`** · **`complete_pass`** · `defensive_extra_point_conv` ·
`defensive_two_point_conv` · `forced_fumble_player_1_player_id` ·
`forced_fumble_player_1_player_name` · `forced_fumble_player_1_team` ·
`forced_fumble_player_2_player_id` · `forced_fumble_player_2_player_name` ·
`forced_fumble_player_2_team` · `fumble` · `fumble_forced` · **`fumble_lost`** ·
`fumble_not_forced` · `fumble_out_of_bounds` · `fumble_recovery_1_player_id` ·
`fumble_recovery_1_player_name` · `fumble_recovery_1_team` · `fumble_recovery_1_yards` ·
`fumble_recovery_2_player_id` · `fumble_recovery_2_player_name` · `fumble_recovery_2_team` ·
`fumble_recovery_2_yards` · `fumbled_1_player_id` · `fumbled_1_player_name` · `fumbled_1_team` ·
`fumbled_2_player_id` · `fumbled_2_player_name` · `fumbled_2_team` · `incomplete_pass` ·
**`interception`** · `lateral_interception_player_id` · `lateral_interception_player_name` ·
`lateral_kickoff_returner_player_id` · `lateral_kickoff_returner_player_name` ·
`lateral_punt_returner_player_id` · `lateral_punt_returner_player_name` ·
`lateral_receiver_player_id` · `lateral_receiver_player_name` · `lateral_receiving_yards` ·
`lateral_reception` · `lateral_recovery` · `lateral_return` · `lateral_rush` ·
`lateral_rusher_player_id` · `lateral_rusher_player_name` · `lateral_rushing_yards` ·
`lateral_sack_player_id` · `lateral_sack_player_name` · **`pass_touchdown`** · `passing_yards` ·
`penalty_yards` · `qb_hit` · `receiving_yards` · `return_touchdown` · `return_yards` ·
**`rush_touchdown`** · `rushing_yards` · `sack` · `safety` · `tackled_for_loss` · `td_team` ·
`touchback` · **`touchdown`** · **`yards_after_catch`** · **`yards_gained`**

### Scoring plays & special teams (28)

`extra_point_prob` · `extra_point_result` · `field_goal_result` · `kick_distance` ·
`kickoff_downed` · `kickoff_fair_catch` · `kickoff_in_endzone` · `kickoff_inside_twenty` ·
`kickoff_out_of_bounds` · `kickoff_returner_player_id` · `kickoff_returner_player_name` ·
`own_kickoff_recovery` · `own_kickoff_recovery_player_id` · `own_kickoff_recovery_player_name` ·
`own_kickoff_recovery_td` · `punt_blocked` · `punt_downed` · `punt_fair_catch` ·
`punt_in_endzone` · `punt_inside_twenty` · `punt_out_of_bounds` · `punt_returner_player_id` ·
`punt_returner_player_name` · `punter_player_id` · `punter_player_name` · `return_team` ·
`two_point_conv_result` · `two_point_conversion_prob`

### Tackles (31)

`assist_tackle` · `assist_tackle_1_player_id` · `assist_tackle_1_player_name` ·
`assist_tackle_1_team` · `assist_tackle_2_player_id` · `assist_tackle_2_player_name` ·
`assist_tackle_2_team` · `assist_tackle_3_player_id` · `assist_tackle_3_player_name` ·
`assist_tackle_3_team` · `assist_tackle_4_player_id` · `assist_tackle_4_player_name` ·
`assist_tackle_4_team` · `solo_tackle` · `solo_tackle_1_player_id` · `solo_tackle_1_player_name`
· `solo_tackle_1_team` · `solo_tackle_2_player_id` · `solo_tackle_2_player_name` ·
`solo_tackle_2_team` · `tackle_for_loss_1_player_id` · `tackle_for_loss_1_player_name` ·
`tackle_for_loss_2_player_id` · `tackle_for_loss_2_player_name` · `tackle_with_assist` ·
`tackle_with_assist_1_player_id` · `tackle_with_assist_1_player_name` ·
`tackle_with_assist_1_team` · `tackle_with_assist_2_player_id` ·
`tackle_with_assist_2_player_name` · `tackle_with_assist_2_team`

### Who was involved (41)

`blocked_player_id` · `blocked_player_name` · `fantasy_player_id` · `fantasy_player_name` ·
`half_sack_1_player_id` · `half_sack_1_player_name` · `half_sack_2_player_id` ·
`half_sack_2_player_name` · `interception_player_id` · `interception_player_name` ·
`jersey_number` · `kicker_player_id` · `kicker_player_name` · `pass_defense_1_player_id` ·
`pass_defense_1_player_name` · `pass_defense_2_player_id` · `pass_defense_2_player_name` ·
`passer` · `passer_jersey_number` · **`passer_player_id`** · `passer_player_name` ·
`penalty_player_id` · `penalty_player_name` · `qb_hit_1_player_id` · `qb_hit_1_player_name` ·
`qb_hit_2_player_id` · `qb_hit_2_player_name` · `receiver` · `receiver_jersey_number` ·
**`receiver_player_id`** · `receiver_player_name` · `rusher` · `rusher_jersey_number` ·
**`rusher_player_id`** · `rusher_player_name` · `sack_player_id` · `sack_player_name` ·
`safety_player_id` · `safety_player_name` · `td_player_id` · `td_player_name`

### Models — EPA, win probability, completion probability (63)

`air_epa` · `air_wpa` · `away_wp` · `away_wp_post` · `comp_air_epa` · `comp_air_wpa` ·
`comp_yac_epa` · `comp_yac_wpa` · `cp` · `cpoe` · `def_wp` · `ep` · **`epa`** · `fg_prob` ·
`home_wp` · `home_wp_post` · `no_score_prob` · `opp_fg_prob` · `opp_safety_prob` · `opp_td_prob`
· `qb_epa` · `safety_prob` · **`success`** · `td_prob` · `total_away_comp_air_epa` ·
`total_away_comp_air_wpa` · `total_away_comp_yac_epa` · `total_away_comp_yac_wpa` ·
`total_away_epa` · `total_away_pass_epa` · `total_away_pass_wpa` · `total_away_raw_air_epa` ·
`total_away_raw_air_wpa` · `total_away_raw_yac_epa` · `total_away_raw_yac_wpa` ·
`total_away_rush_epa` · `total_away_rush_wpa` · `total_home_comp_air_epa` ·
`total_home_comp_air_wpa` · `total_home_comp_yac_epa` · `total_home_comp_yac_wpa` ·
`total_home_epa` · `total_home_pass_epa` · `total_home_pass_wpa` · `total_home_raw_air_epa` ·
`total_home_raw_air_wpa` · `total_home_raw_yac_epa` · `total_home_raw_yac_wpa` ·
`total_home_rush_epa` · `total_home_rush_wpa` · `vegas_home_wp` · `vegas_home_wpa` · `vegas_wp`
· `vegas_wpa` · **`wp`** · `wpa` · `xyac_epa` · `xyac_fd` · `xyac_mean_yardage` ·
`xyac_median_yardage` · `xyac_success` · `yac_epa` · `yac_wpa`

### Penalties (4)

`penalty_team` · `penalty_type` · `replay_or_challenge` · `replay_or_challenge_result`

### Text (2)

`desc` · `play`

### Other (9)

`fantasy` · `fantasy_id` · `home_opening_kickoff` · `id` · `name` · `out_of_bounds` ·
`passer_id` · `receiver_id` · `rusher_id`

---

## `load_player_stats`

**18,981 rows** (2024) · **145 columns**

One row per player per week — the familiar box score, plus the share statistics that say how much of an offence runs through somebody.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_player_stats.html)

### Identity & game (12)

`game_id` · `headshot_url` · `opponent_team` · `player_display_name` · `player_id` ·
`player_name` · `position` · `position_group` · `season` · `season_type` · `team` · `week`

### Passing (20)

`attempts` · `completions` · `pacr` · `passing_10` · `passing_16` · `passing_20` ·
`passing_2pt_conversions` · `passing_40` · `passing_air_yards` · `passing_cpoe` · `passing_epa`
· `passing_first_downs` · `passing_interceptions` · `passing_tds` · `passing_yards` ·
`passing_yards_after_catch` · `sack_fumbles` · `sack_fumbles_lost` · `sack_yards_lost` ·
`sacks_suffered`

### Rushing (12)

`carries` · `rushing_10` · `rushing_12` · `rushing_20` · `rushing_2pt_conversions` ·
`rushing_40` · `rushing_epa` · `rushing_first_downs` · `rushing_fumbles` ·
`rushing_fumbles_lost` · `rushing_tds` · `rushing_yards`

### Receiving (19)

`air_yards_share` · `racr` · `receiving_10` · `receiving_16` · `receiving_20` ·
`receiving_2pt_conversions` · `receiving_40` · `receiving_air_yards` · `receiving_epa` ·
`receiving_first_downs` · `receiving_fumbles` · `receiving_fumbles_lost` · `receiving_tds` ·
`receiving_yards` · `receiving_yards_after_catch` · `receptions` · `target_share` · `targets` ·
`wopr`

### Kicking (34)

`fg_att` · `fg_blocked` · `fg_blocked_distance` · `fg_blocked_list` · `fg_long` · `fg_made` ·
`fg_made_0_19` · `fg_made_20_29` · `fg_made_30_39` · `fg_made_40_49` · `fg_made_50_59` ·
`fg_made_60_` · `fg_made_distance` · `fg_made_list` · `fg_missed` · `fg_missed_0_19` ·
`fg_missed_20_29` · `fg_missed_30_39` · `fg_missed_40_49` · `fg_missed_50_59` · `fg_missed_60_`
· `fg_missed_distance` · `fg_missed_list` · `fg_pct` · `gwfg_att` · `gwfg_blocked` ·
`gwfg_distance` · `gwfg_made` · `gwfg_missed` · `pat_att` · `pat_blocked` · `pat_made` ·
`pat_missed` · `pat_pct`

### Defence (20)

`def_fumbles` · `def_fumbles_forced` · `def_interception_yards` · `def_interceptions` ·
`def_pass_defended` · `def_qb_hits` · `def_sack_yards` · `def_sacks` · `def_safeties` ·
`def_tackle_assists` · `def_tackles_for_loss` · `def_tackles_for_loss_yards` ·
`def_tackles_solo` · `def_tackles_with_assist` · `def_tds` · `fumble_recovery_opp` ·
`fumble_recovery_own` · `fumble_recovery_tds` · `fumble_recovery_yards_opp` ·
`fumble_recovery_yards_own`

### Special teams (5)

`kickoff_return_yards` · `kickoff_returns` · `punt_return_yards` · `punt_returns` ·
`special_teams_tds`

### Fantasy (2)

`fantasy_points` · `fantasy_points_ppr`

### Fumbles (5)

`fumbles_forced_by_opp` · `fumbles_lost_total` · `fumbles_not_forced` · `fumbles_out_of_bounds`
· `fumbles_total`

### Punting (13)

`pt_att` · `pt_blocked` · `pt_downed` · `pt_fair_caught` · `pt_inside_20` · `pt_long` ·
`pt_net_yards` · `pt_out_of_bounds` · `pt_return_tds` · `pt_return_yards` · `pt_returned` ·
`pt_touchback` · `pt_yards`

### Misc (2)

`misc_yards` · `penalty_yards`

### Everything else (1)

`penalties`

---

## `load_team_stats`

**570 rows** (2024) · **133 columns**

The same shape for whole teams. **The only place the defensive counting stats live**, which is what `dfs_dst_service` scores a defence on.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_team_stats.html)

### Identity & game (6)

`game_id` · `opponent_team` · `season` · `season_type` · `team` · `week`

### Passing (19)

`attempts` · `completions` · `passing_10` · `passing_16` · `passing_20` ·
`passing_2pt_conversions` · `passing_40` · `passing_air_yards` · `passing_cpoe` · `passing_epa`
· `passing_first_downs` · `passing_interceptions` · `passing_tds` · `passing_yards` ·
`passing_yards_after_catch` · `sack_fumbles` · `sack_fumbles_lost` · `sack_yards_lost` ·
`sacks_suffered`

### Rushing (12)

`carries` · `rushing_10` · `rushing_12` · `rushing_20` · `rushing_2pt_conversions` ·
`rushing_40` · `rushing_epa` · `rushing_first_downs` · `rushing_fumbles` ·
`rushing_fumbles_lost` · `rushing_tds` · `rushing_yards`

### Receiving (15)

`receiving_10` · `receiving_16` · `receiving_20` · `receiving_2pt_conversions` · `receiving_40`
· `receiving_air_yards` · `receiving_epa` · `receiving_first_downs` · `receiving_fumbles` ·
`receiving_fumbles_lost` · `receiving_tds` · `receiving_yards` · `receiving_yards_after_catch` ·
`receptions` · `targets`

### Kicking (34)

`fg_att` · `fg_blocked` · `fg_blocked_distance` · `fg_blocked_list` · `fg_long` · `fg_made` ·
`fg_made_0_19` · `fg_made_20_29` · `fg_made_30_39` · `fg_made_40_49` · `fg_made_50_59` ·
`fg_made_60_` · `fg_made_distance` · `fg_made_list` · `fg_missed` · `fg_missed_0_19` ·
`fg_missed_20_29` · `fg_missed_30_39` · `fg_missed_40_49` · `fg_missed_50_59` · `fg_missed_60_`
· `fg_missed_distance` · `fg_missed_list` · `fg_pct` · `gwfg_att` · `gwfg_blocked` ·
`gwfg_distance` · `gwfg_made` · `gwfg_missed` · `pat_att` · `pat_blocked` · `pat_made` ·
`pat_missed` · `pat_pct`

### Defence (20)

`def_fumbles` · `def_fumbles_forced` · `def_interception_yards` · `def_interceptions` ·
`def_pass_defended` · `def_qb_hits` · `def_sack_yards` · `def_sacks` · `def_safeties` ·
`def_tackle_assists` · `def_tackles_for_loss` · `def_tackles_for_loss_yards` ·
`def_tackles_solo` · `def_tackles_with_assist` · `def_tds` · `fumble_recovery_opp` ·
`fumble_recovery_own` · `fumble_recovery_tds` · `fumble_recovery_yards_opp` ·
`fumble_recovery_yards_own`

### Special teams (5)

`kickoff_return_yards` · `kickoff_returns` · `punt_return_yards` · `punt_returns` ·
`special_teams_tds`

### Fumbles (5)

`fumbles_forced_by_opp` · `fumbles_lost_total` · `fumbles_not_forced` · `fumbles_out_of_bounds`
· `fumbles_total`

### Punting (13)

`pt_att` · `pt_blocked` · `pt_downed` · `pt_fair_caught` · `pt_inside_20` · `pt_long` ·
`pt_net_yards` · `pt_out_of_bounds` · `pt_return_tds` · `pt_return_yards` · `pt_returned` ·
`pt_touchback` · `pt_yards`

### Misc (3)

`misc_yards` · `penalty_yards` · `timeouts`

### Everything else (1)

`penalties`

---

## `load_ff_opportunity`

**6,005 rows** (2024) · **159 columns**

Expected fantasy points: what a player's opportunities were worth, regardless of how they turned out. Columns come in matched sets — the actual, the `_exp` expectation, and the `_diff` between them — plus `_team` versions for the whole offence.

⚠️ **These numbers are full PPR.** `services/dfs_scoring.py` converts them.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_ff_opportunity.html)

### Identity (7)

`full_name` · `game_id` · `player_id` · `position` · `posteam` · `season` · `week`

### Passing — actual, expected, difference (46)

`pass_air_yards` · `pass_air_yards_team` · `pass_attempt` · `pass_attempt_team` ·
`pass_completions` · `pass_completions_diff` · `pass_completions_diff_team` ·
`pass_completions_exp` · `pass_completions_exp_team` · `pass_completions_team` ·
`pass_fantasy_points` · `pass_fantasy_points_diff` · `pass_fantasy_points_diff_team` ·
`pass_fantasy_points_exp` · `pass_fantasy_points_exp_team` · `pass_fantasy_points_team` ·
`pass_first_down` · `pass_first_down_diff` · `pass_first_down_diff_team` · `pass_first_down_exp`
· `pass_first_down_exp_team` · `pass_first_down_team` · `pass_interception` ·
`pass_interception_diff` · `pass_interception_diff_team` · `pass_interception_exp` ·
`pass_interception_exp_team` · `pass_interception_team` · `pass_touchdown` ·
`pass_touchdown_diff` · `pass_touchdown_diff_team` · `pass_touchdown_exp` ·
`pass_touchdown_exp_team` · `pass_touchdown_team` · `pass_two_point_conv` ·
`pass_two_point_conv_diff` · `pass_two_point_conv_diff_team` · `pass_two_point_conv_exp` ·
`pass_two_point_conv_exp_team` · `pass_two_point_conv_team` · `pass_yards_gained` ·
`pass_yards_gained_diff` · `pass_yards_gained_diff_team` · `pass_yards_gained_exp` ·
`pass_yards_gained_exp_team` · `pass_yards_gained_team`

### Rushing (34)

`rush_attempt` · `rush_attempt_team` · `rush_fantasy_points` · `rush_fantasy_points_diff` ·
`rush_fantasy_points_diff_team` · `rush_fantasy_points_exp` · `rush_fantasy_points_exp_team` ·
`rush_fantasy_points_team` · `rush_first_down` · `rush_first_down_diff` ·
`rush_first_down_diff_team` · `rush_first_down_exp` · `rush_first_down_exp_team` ·
`rush_first_down_team` · `rush_fumble_lost` · `rush_fumble_lost_team` · `rush_touchdown` ·
`rush_touchdown_diff` · `rush_touchdown_diff_team` · `rush_touchdown_exp` ·
`rush_touchdown_exp_team` · `rush_touchdown_team` · `rush_two_point_conv` ·
`rush_two_point_conv_diff` · `rush_two_point_conv_diff_team` · `rush_two_point_conv_exp` ·
`rush_two_point_conv_exp_team` · `rush_two_point_conv_team` · `rush_yards_gained` ·
`rush_yards_gained_diff` · `rush_yards_gained_diff_team` · `rush_yards_gained_exp` ·
`rush_yards_gained_exp_team` · `rush_yards_gained_team`

### Receiving (48)

`rec_air_yards` · `rec_air_yards_team` · `rec_attempt` · `rec_attempt_team` ·
`rec_fantasy_points` · `rec_fantasy_points_diff` · `rec_fantasy_points_diff_team` ·
`rec_fantasy_points_exp` · `rec_fantasy_points_exp_team` · `rec_fantasy_points_team` ·
`rec_first_down` · `rec_first_down_diff` · `rec_first_down_diff_team` · `rec_first_down_exp` ·
`rec_first_down_exp_team` · `rec_first_down_team` · `rec_fumble_lost` · `rec_fumble_lost_team` ·
`rec_interception` · `rec_interception_diff` · `rec_interception_diff_team` ·
`rec_interception_exp` · `rec_interception_exp_team` · `rec_interception_team` · `rec_touchdown`
· `rec_touchdown_diff` · `rec_touchdown_diff_team` · `rec_touchdown_exp` ·
`rec_touchdown_exp_team` · `rec_touchdown_team` · `rec_two_point_conv` ·
`rec_two_point_conv_diff` · `rec_two_point_conv_diff_team` · `rec_two_point_conv_exp` ·
`rec_two_point_conv_exp_team` · `rec_two_point_conv_team` · `rec_yards_gained` ·
`rec_yards_gained_diff` · `rec_yards_gained_diff_team` · `rec_yards_gained_exp` ·
`rec_yards_gained_exp_team` · `rec_yards_gained_team` · `receptions` · `receptions_diff` ·
`receptions_diff_team` · `receptions_exp` · `receptions_exp_team` · `receptions_team`

### Totals (24)

`total_fantasy_points` · `total_fantasy_points_diff` · `total_fantasy_points_diff_team` ·
`total_fantasy_points_exp` · `total_fantasy_points_exp_team` · `total_fantasy_points_team` ·
`total_first_down` · `total_first_down_diff` · `total_first_down_diff_team` ·
`total_first_down_exp` · `total_first_down_exp_team` · `total_first_down_team` ·
`total_touchdown` · `total_touchdown_diff` · `total_touchdown_diff_team` · `total_touchdown_exp`
· `total_touchdown_exp_team` · `total_touchdown_team` · `total_yards_gained` ·
`total_yards_gained_diff` · `total_yards_gained_diff_team` · `total_yards_gained_exp` ·
`total_yards_gained_exp_team` · `total_yards_gained_team`

---

## `load_snap_counts`

**26,615 rows** (2024) · **16 columns**

How many snaps each player was on the field for. ⚠️ **Keyed by `pfr_player_id`**, not the app's `canonical_id` — join through `load_ff_playerids`.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_snap_counts.html)


`defense_pct` · `defense_snaps` · `game_id` · `game_type` · `offense_pct` · `offense_snaps` ·
`opponent` · `pfr_game_id` · `pfr_player_id` · `player` · `position` · `season` · `st_pct` ·
`st_snaps` · `team` · `week`

---

## `load_nextgen_stats.passing`

**614 rows** (2024) · **29 columns**

Tracking-camera data for quarterbacks. ⚠️ Includes a `week == 0` row per player holding SEASON totals — joining it in attaches a season's averages to one week.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_nextgen_stats.html)


`aggressiveness` · `attempts` · `avg_air_distance` · `avg_air_yards_differential` ·
`avg_air_yards_to_sticks` · `avg_completed_air_yards` · `avg_intended_air_yards` ·
`avg_time_to_throw` · `completion_percentage` · `completion_percentage_above_expectation` ·
`completions` · `expected_completion_percentage` · `interceptions` · `max_air_distance` ·
`max_completed_air_distance` · `pass_touchdowns` · `pass_yards` · `passer_rating` ·
`player_display_name` · `player_first_name` · `player_gsis_id` · `player_jersey_number` ·
`player_last_name` · `player_position` · `player_short_name` · `season` · `season_type` ·
`team_abbr` · `week`

---

## `load_nextgen_stats.rushing`

**601 rows** (2024) · **22 columns**

Tracking data for ball carriers. Same `week == 0` caveat.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_nextgen_stats.html)


`avg_rush_yards` · `avg_time_to_los` · `efficiency` · `expected_rush_yards` ·
`percent_attempts_gte_eight_defenders` · `player_display_name` · `player_first_name` ·
`player_gsis_id` · `player_jersey_number` · `player_last_name` · `player_position` ·
`player_short_name` · `rush_attempts` · `rush_pct_over_expected` · `rush_touchdowns` ·
`rush_yards` · `rush_yards_over_expected` · `rush_yards_over_expected_per_att` · `season` ·
`season_type` · `team_abbr` · `week`

---

## `load_nextgen_stats.receiving`

**1,435 rows** (2024) · **23 columns**

Tracking data for pass catchers — separation, cushion, depth of target. Same `week == 0` caveat.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_nextgen_stats.html)


`avg_cushion` · `avg_expected_yac` · `avg_intended_air_yards` · `avg_separation` · `avg_yac` ·
`avg_yac_above_expectation` · `catch_percentage` · `percent_share_of_intended_air_yards` ·
`player_display_name` · `player_first_name` · `player_gsis_id` · `player_jersey_number` ·
`player_last_name` · `player_position` · `player_short_name` · `rec_touchdowns` · `receptions` ·
`season` · `season_type` · `targets` · `team_abbr` · `week` · `yards`

---

## `load_pfr_advstats.pass`

**697 rows** (2024) · **24 columns**

Pro Football Reference's hand-charted passing detail. ⚠️ Keyed by `pfr_player_id`.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_pfr_passing.html)


`def_times_blitzed` · `def_times_hitqb` · `def_times_hurried` · `game_id` · `game_type` ·
`opponent` · `passing_bad_throw_pct` · `passing_bad_throws` · `passing_drop_pct` ·
`passing_drops` · `pfr_game_id` · `pfr_player_id` · `pfr_player_name` · `receiving_drop` ·
`receiving_drop_pct` · `season` · `team` · `times_blitzed` · `times_hit` · `times_hurried` ·
`times_pressured` · `times_pressured_pct` · `times_sacked` · `week`

---

## `load_pfr_advstats.rush`

**2,359 rows** (2024) · **16 columns**

Charted rushing detail — yards before and after contact, broken tackles. ⚠️ Keyed by `pfr_player_id`.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_pfr_passing.html)


`carries` · `game_id` · `game_type` · `opponent` · `pfr_game_id` · `pfr_player_id` ·
`pfr_player_name` · `receiving_broken_tackles` · `rushing_broken_tackles` ·
`rushing_yards_after_contact` · `rushing_yards_after_contact_avg` ·
`rushing_yards_before_contact` · `rushing_yards_before_contact_avg` · `season` · `team` · `week`

---

## `load_pfr_advstats.rec`

**4,453 rows** (2024) · **17 columns**

Charted receiving detail — drops, broken tackles. ⚠️ Keyed by `pfr_player_id`.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_pfr_passing.html)


`game_id` · `game_type` · `opponent` · `passing_drop_pct` · `passing_drops` · `pfr_game_id` ·
`pfr_player_id` · `pfr_player_name` · `receiving_broken_tackles` · `receiving_drop` ·
`receiving_drop_pct` · `receiving_int` · `receiving_rat` · `rushing_broken_tackles` · `season` ·
`team` · `week`

---

## `load_pfr_advstats.def`

**7,992 rows** (2024) · **29 columns**

Charted defensive detail. Not currently used, but the richest defensive source available.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_pfr_passing.html)


`def_adot` · `def_air_yards_completed` · `def_completion_pct` · `def_completions_allowed` ·
`def_ints` · `def_missed_tackle_pct` · `def_missed_tackles` · `def_passer_rating_allowed` ·
`def_pressures` · `def_receiving_td_allowed` · `def_sacks` · `def_tackles_combined` ·
`def_targets` · `def_times_blitzed` · `def_times_hitqb` · `def_times_hurried` ·
`def_yards_after_catch` · `def_yards_allowed` · `def_yards_allowed_per_cmp` ·
`def_yards_allowed_per_tgt` · `game_id` · `game_type` · `opponent` · `pfr_game_id` ·
`pfr_player_id` · `pfr_player_name` · `season` · `team` · `week`

---

## `load_schedules`

**285 rows** (2024) · **46 columns**

One row per game. Carries the final scores and **the betting lines** — the only source of `spread_line` and `total_line`.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_schedules.html)


`away_coach` · `away_moneyline` · `away_qb_id` · `away_qb_name` · `away_rest` · `away_score` ·
`away_spread_odds` · `away_team` · `div_game` · `espn` · `ftn` · `game_id` · `game_type` ·
`gameday` · `gametime` · `gsis` · `home_coach` · `home_moneyline` · `home_qb_id` ·
`home_qb_name` · `home_rest` · `home_score` · `home_spread_odds` · `home_team` · `location` ·
`nfl_detail_id` · `old_game_id` · `over_odds` · `overtime` · `pff` · `pfr` · `referee` ·
`result` · `roof` · `season` · `spread_line` · `stadium` · `stadium_id` · `surface` · `temp` ·
`total` · `total_line` · `under_odds` · `week` · `weekday` · `wind`

---

## `load_teams`

**36 rows** (2024) · **16 columns**

Team reference: names, colours, and the logo URLs the Team Profile draws with. Not season-scoped.




`team_abbr` · `team_color` · `team_color2` · `team_color3` · `team_color4` · `team_conf` ·
`team_conference_logo` · `team_division` · `team_id` · `team_league_logo` · `team_logo_espn` ·
`team_logo_squared` · `team_logo_wikipedia` · `team_name` · `team_nick` · `team_wordmark`

---

## `load_players`

**25,038 rows** (2024) · **39 columns**

Every player who has ever held an NFL roster spot. The broadest identity source, and what name resolution matches against. Not season-scoped.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_players.html)


`birth_date` · `college_conference` · `college_name` · `common_first_name` · `display_name` ·
`draft_pick` · `draft_round` · `draft_team` · `draft_year` · `esb_id` · `espn_id` · `first_name`
· `football_name` · `gsis_id` · `headshot` · `height` · `jersey_number` · `last_name` ·
`last_season` · `latest_team` · `nfl_id` · `ngs_position` · `ngs_position_group` · `ngs_status`
· `ngs_status_short_description` · `otc_id` · `pff_id` · `pff_position` · `pff_status` ·
`pfr_id` · `position` · `position_group` · `rookie_season` · `short_name` · `smart_id` ·
`status` · `suffix` · `weight` · `years_of_experience`

---

## `load_ff_playerids`

**12,470 rows** (2024) · **35 columns**

**The crosswalk.** Collects the ids every fantasy site uses for the same player, which is how `pfr_player_id` becomes `canonical_id`. Not season-scoped.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_ff_playerids.html)


`age` · `birthdate` · `cbs_id` · `cfbref_id` · `college` · `db_season` · `draft_ovr` ·
`draft_pick` · `draft_round` · `draft_year` · `espn_id` · `fantasy_data_id` · `fantasypros_id` ·
`fleaflicker_id` · `gsis_id` · `height` · `ktc_id` · `merge_name` · `mfl_id` · `name` · `nfl_id`
· `pff_id` · `pfr_id` · `position` · `rotowire_id` · `rotoworld_id` · `sleeper_id` ·
`sportradar_id` · `stats_global_id` · `stats_id` · `swish_id` · `team` · `twitter_username` ·
`weight` · `yahoo_id`

---

## `load_rosters_weekly`

**46,579 rows** (2024) · **36 columns**

Who was on each roster each week, with status. Useful for depth changes; not currently used.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_rosters.html)


`birth_date` · `college` · `depth_chart_position` · `draft_club` · `draft_number` · `entry_year`
· `esb_id` · `espn_id` · `fantasy_data_id` · `first_name` · `football_name` · `full_name` ·
`game_type` · `gsis_id` · `gsis_it_id` · `headshot_url` · `height` · `jersey_number` ·
`last_name` · `ngs_position` · `pff_id` · `pfr_id` · `position` · `rookie_year` · `rotowire_id`
· `season` · `sleeper_id` · `smart_id` · `sportradar_id` · `status` · `status_description_abbr`
· `team` · `week` · `weight` · `yahoo_id` · `years_exp`

---

## `load_injuries`

**6,215 rows** (2024) · **16 columns**

Weekly injury reports. Keyed by `gsis_id`, so it joins directly. Not currently used.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_injuries.html)


`date_modified` · `first_name` · `full_name` · `game_type` · `gsis_id` · `last_name` ·
`position` · `practice_primary_injury` · `practice_secondary_injury` · `practice_status` ·
`report_primary_injury` · `report_secondary_injury` · `report_status` · `season` · `team` ·
`week`

---

## `load_depth_charts`

**37,312 rows** (2024) · **15 columns**

Published depth charts by week. Keyed by `gsis_id`. Not currently used.

[nflverse dictionary](https://nflreadr.nflverse.com/articles/dictionary_depth_charts.html)


`club_code` · `depth_position` · `depth_team` · `elias_id` · `first_name` · `football_name` ·
`formation` · `full_name` · `game_type` · `gsis_id` · `jersey_number` · `last_name` · `position`
· `season` · `week`

---
