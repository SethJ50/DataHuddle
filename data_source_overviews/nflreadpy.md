# nflreadpy`

`nfl.load_pbp()`
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_pbp.html)
SOOO much

`nfl.load_player_stats(summary_level = ["week", "reg"])`
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_player_stats.html)
- headshot_url
- season
- week
- team
- opponent_team
- passing
	- completions
	- attempts
	- passing_yards
	- passing_tds
	- passing_interceptions
	- sacks_suffered
	- passing_air_yards
	- passing_yards_after_catch
	- passing_epa
	- passing_cpoe
	- pacr
- rushing
	- carries
	- rushing_yards
	- rushing_tds
	- rushing_epa
- receiving
	- receptions
	- targets
	- receiving_yards
	- receiving_tds
	- receiving_air_yards
	- receiving_yards_after_catch
	- receiving_epa
	- racr
	- target_share
	- air_yards_share
	- wopr
- fantasy
	- fantasy_points = standard
	- fantasy_points_ppr

`nfl.load_snap_counts()`
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_snap_counts.html)
- player
- offense_snaps
- offense_pct

`nfl.load_next_gen_stats(stat_type=["passing", "rushing", "receiving")`
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_nextgen_stats.html)
- passing
	- avg_time_to_throw
	- avg_completed_air_yds
	- passer_rating
	- expected_completion_percentage
	- aggressiveness
	- completion_percentage_above_expectation
- receiving
	- avg_air_distance = ADOT
	- avg_cushion = time of snap
	- avg_separation
	- percent_share_of_intended_air_yards
	- avg_yac_above_expectation
- rushing
	- efficiency
	- percent_attempts_gte_eight_defenders = percentage 'stacked box'
	- rush_yards_over_expected
	- rush_yards_over_expected_per_att

`nfl.load_ftn_charting()`
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_ftn_charting.html)
- qb_location (under center, shotgun, pistol)
- n_offense_backfield = num players in backfield
- is_no_huddle
- is_motion
- is_play_action
- is_screen_pass
- is_rpo
- is_qb_out_of_pocket
- ...

`nfl.load_participation()`
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_participation.html)
- nflverse_game_id
- possession_team
- offensive_formation
- offensive_personnel
- defenders_in_box
- defense_personnel
- number_of_pass_rushers
- time_to_throw
- was_pressure
- route = route of primary receiver
- defense_man_zone_type
- defense_coverage_type
- offense_names
- defense_names

`nfl.load_pfr_advstats(stat_type = ["pass", "rush", "rec", "def"])`
- pass
	- receiving_drop_pct
	- times_hit
	- times_pressured
	- times_hurried
- rush
	- rushing_yards_before_contact_avg
	- rushing_yards_after_contact_avg
- rec
	- receiving_drop_pct

`nfl.load_ff_rankings(type = ["draft", "week", "all"])`
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_ff_rankings.html)
Fantasy Pros Scraper Based?
- draft
	- fp_page
	- page_type
	- ecr_type
	- player
	- id
	- pos
	- team
	- ecr
	- sd (of expert ranking?)
	- best
	- worst
	- sportsdata_id
	- player_filename
	- yahoo_id
	- cbs_id
	- player_owned_avg
	- player_owned_espn
	- ...
- week
	- fantasypros_id
	- player_name
	- ecr
	- sd
	- best
	- worst
	- note
	- tag
	- recommendation

`nfl.load_ff_opportunity(stat_type = ["weekly", "pbp_pass", "pbp_rush"])`
primarily expected points data
[Dictionary](https://nflreadr.nflverse.com/articles/dictionary_ff_opportunity.html)
- weekly
	- full_name
	- posteam
	- rush_touchdown_exp
	- rush_first_down_exp
	- pass_attemnpt
	- pass_touchdown_exp
	- pass_first_down_exp
	- pass_interception_exp
	- player_id
	- pass_air_yards
	- rec_air_yards
	- ...
	- pass_fantasy_points_exp
	- rec_fantasy_points_exp
	- rush_fantasy_points_exp
	- total_fantasy_points_exp
	- pass_fantasy_points_diff (over expectation)
- pbp_rush
	- run_location
	- run_gap
	- run_gap_dir
	- surface
	- roof
	- shotgun
	- no_huddle
	- wind
	- temp
	- xpass
	- ydstogo
	- vegas_wp
	- rush_yards_exp
- pbp_pass
	- relative_to_endzone