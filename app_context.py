"""Composition root — builds every repository/adapter/service once and hands
them to the UI layer as a single object, replacing the scattered module-level
globals (nfl_data/ffb_data/ui_data/ui_maker) that used to live in app.py.

Built once at app startup and shared read-only across all Shiny sessions —
appropriate here since the app is single-user/no-auth (see PLANNING.md) and
this state (projections, player identity) isn't supposed to differ per
browser session.
"""

from registry import Collections
from repositories.collection_repo import CollectionRepo
from repositories.nfl_read_repo import NflReadRepo
from repositories.player_directory import PlayerDirectory
from repositories.player_identity_repo import PlayerIdentityRepo
from adapters.ffb_projections_adapter import FfbProjectionsAdapter
from adapters.udk_rankings_adapter import UdkRankingsAdapter
from services.projections_service import ProjectionsService
from services.roster_service import RosterService
from adapters.adp_source_adapter import EspnAdpAdapter, SleeperAdpAdapter, YahooAdpAdapter
from services.adp_comparison_service import AdpComparisonService
from services.draft_plan_service import DraftPlanService
from services.draft_service import DraftService
from services.player_markings_service import PlayerMarkingsService
from services.team_notes_service import TeamNotesService


class AppContext:
    def __init__(self, seasons: list):
        self.nfl_read_repo = NflReadRepo(seasons)
        self.player_directory = PlayerDirectory(self.nfl_read_repo)

        self.identity_repo = PlayerIdentityRepo(
            CollectionRepo(Collections.PLAYER_ID_MAP),
            self.player_directory,
        )

        # The app's player universe: only players ranked in UDK's QB/RB/WR/TE
        # rankings are in scope. Built before anything that needs to filter
        # down to it (ADP comparison, projections).
        self.udk_rankings_adapter = UdkRankingsAdapter(
            CollectionRepo(Collections.UDK_QB_RANKINGS),
            CollectionRepo(Collections.UDK_RB_RANKINGS),
            CollectionRepo(Collections.UDK_WR_RANKINGS),
            CollectionRepo(Collections.UDK_TE_RANKINGS),
        )
        self.roster_service = RosterService(
            self.udk_rankings_adapter,
            self.identity_repo,
            self.player_directory,
        )

        self.espn_adp_adapter = EspnAdpAdapter(CollectionRepo(Collections.ESPN_PROJECTIONS))
        self.sleeper_adp_adapter = SleeperAdpAdapter(CollectionRepo(Collections.SLEEPER_PROJECTIONS))
        self.yahoo_adp_adapter = YahooAdpAdapter(CollectionRepo(Collections.YAHOO_DRAFTANALYSIS))

        self.adp_comparison_service = AdpComparisonService(
            self.espn_adp_adapter,
            self.sleeper_adp_adapter,
            self.yahoo_adp_adapter,
            self.identity_repo,
            self.roster_service,
        )

        self.ffb_adapter = FfbProjectionsAdapter(
            CollectionRepo(Collections.FFB_QB_PROJECTIONS),
            CollectionRepo(Collections.FFB_FLEX_PROJECTIONS),
        )
        self.projections_service = ProjectionsService(
            self.ffb_adapter,
            self.identity_repo,
            self.roster_service,
        )

        self.draft_plan_service = DraftPlanService(
            self.roster_service,
            self.adp_comparison_service,
            self.projections_service
        )

        self.draft_service = DraftService()
        self.player_markings_service = PlayerMarkingsService()
        self.team_notes_service = TeamNotesService()