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
from repositories.dfs_read_repo import DfsReadRepo
from repositories.nfl_read_repo import NflReadRepo
from repositories.player_directory import PlayerDirectory
from repositories.player_identity_repo import PlayerIdentityRepo
from adapters.ffb_projections_adapter import ANALYSTS, FfbProjectionsAdapter
from adapters.udk_rankings_adapter import UdkRankingsAdapter
from services.projections_service import ProjectionsService
from services.roster_service import RosterService
from adapters.adp_source_adapter import EspnAdpAdapter, SleeperAdpAdapter, YahooAdpAdapter
from services.adp_comparison_service import AdpComparisonService
from services.draft_plan_service import DraftPlanService
from services.draft_service import DraftService
from services.player_markings_service import PlayerMarkingsService
from services.team_notes_service import TeamNotesService
from repositories.ffc_repo import FfcRepo
from services.ffc_service import FfcService
from services.draft_sim_service import DraftSimService


class AppContext:
    """Builds and holds every repository, adapter, and service the app uses.

    This is the app's "composition root": the one place that decides which
    concrete object gets plugged into which. Everything else takes its
    collaborators as arguments and never constructs them, which is what keeps the
    rest of the codebase testable with fakes.

    Build one of these at startup and pass it around. Every attribute is a
    finished, ready-to-use service; pages reach them as `ctx.roster_service` and
    so on.
    """

    def __init__(self, seasons: list, dfs_seasons: list = None):
        """Wire up the whole object graph, in dependency order.

        Nothing here touches the network or the database. Every repository loads
        lazily on first use, so building this is fast — the slow part is the
        first page that actually asks for data.

        The order below is not arbitrary: each thing is built after whatever it
        depends on.

        Steps:
            1. Build the nflreadpy repository and the player directory on top of
               it, since player identity underpins everything else.
            2. Build the identity repository, which needs both the curated
               mapping collection and that directory.
            3. Build the UDK rankings adapter and the roster service, which
               together define the app's player universe. These come before
               anything that has to filter down to it.
            4. Build the three ADP adapters and the comparison service over them.
            5. Build the projections adapter, giving it one QB and flex
               repository pair per analyst, and the projections service over it.
            6. Build the draft plan service, which needs the roster, the ADP
               comparison, and the projections.
            7. Build the three services that only talk to MongoDB and so need no
               collaborators at all.
            8. Build the FFC repository and service, the app's only source of
               draft-position spread.
            9. Build the simulation service, since it depends on several of the
               above.
            10. Build the Daily Fantasy repository. It depends on nothing else
                and loads its own, shorter set of seasons.

        Args:
            seasons: Which NFL seasons of game stats to make available, as a list
                of years. Only affects game logs; the player and team reference
                tables are not season-scoped.
            dfs_seasons: Which seasons the Daily Fantasy pages load. Deliberately
                separate from `seasons` and normally shorter, because those pages
                read play-by-play data, which costs far more per season than a
                game log does. Left out, it falls back to `seasons`, which is
                correct but slower than it needs to be -- pass `DFS_SEASONS` from
                streamlit_state.py.
        """
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

        # One QB/flex repo pair per analyst. Their disagreement is a signal in
        # its own right -- see ProjectionsService.disagreement().
        self.ffb_adapter = FfbProjectionsAdapter({
            analyst: (
                CollectionRepo(Collections.FFB_QB_PROJECTIONS.format(analyst=analyst)),
                CollectionRepo(Collections.FFB_FLEX_PROJECTIONS.format(analyst=analyst)),
            )
            for analyst in ANALYSTS
        })
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

        # Fantasy Football Calculator — the app's only source of draft-position
        # SPREAD (stdev), which the draft model needs alongside ADP. Unlike the
        # other sources this one is keyed by FFC's own ids, so FfcService attaches
        # canonical_id where it can (nullable — see draft_model/DESIGN.md).
        self.ffc_repo = FfcRepo()
        self.ffc_service = FfcService(self.ffc_repo, self.identity_repo)

        # Serves saved simulation runs to the UI. Holds no simulation itself --
        # the expensive work happens offline in scripts/run_draft_sim.py.
        self.draft_sim_service = DraftSimService(
            self.ffc_service,
            self.adp_comparison_service,
            self.projections_service,
        )
        # Daily Fantasy's own data loader. Kept apart from `nfl_read_repo`
        # above because the two halves of the app want different sources over
        # different seasons -- season-long wants many years of game logs, while
        # DFS wants a few years of play-by-play, snap counts and expected
        # points. Nothing loads until a DFS page asks for it.
        self.dfs_read_repo = DfsReadRepo(dfs_seasons or seasons)
