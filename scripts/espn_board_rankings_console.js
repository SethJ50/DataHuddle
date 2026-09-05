/*
 * ESPN draft board rankings -> CSV, run in the browser console.
 *
 * ESPN's draft board is login-gated, JS-rendered, and paginated, so it can't be
 * fetched by a script. This snippet runs in the browser where you're already
 * logged in: it collects the current page's rows, clicks "next" until the last
 * page, then hands back everything as espn_board_rankings.csv.
 *
 * THE RANK IS THE ROW ORDER. Unlike Yahoo, ESPN does not print a rank number in
 * this table -- the ranking IS the order the rows come in. So the two rules
 * below are not optional:
 *
 *   - DO NOT click a column header to sort. Any sort silently rewrites every
 *     rank this produces, and the result will look perfectly plausible.
 *   - Run it BEFORE the draft starts, or with the player filter showing all
 *     players. Once players are drafted they drop out of the list and every
 *     rank below them shifts up.
 *
 * Usage:
 *   1. Open your ESPN league's draft board (or the Players tab) so the ranked
 *      list is showing, with the position filter set to ALL, on page 1.
 *   2. Open DevTools (Cmd+Option+I) -> Console tab.
 *   3. Paste this entire file and press Enter.
 *      (If Chrome says pasting is blocked, type: allow pasting  and retry.)
 *   4. Wait while it pages through the table (progress logs in the console).
 *      When it finishes it tries to copy the CSV to your clipboard; if that
 *      is blocked, it tells you to type:  copy(window.__espnCSV)
 *   5. Paste your clipboard into a new file and save it as:
 *      data/espn_board_rankings.csv
 *
 * The analyst columns (MB, MC, TC, DD, EK, LL, EM, FY, AVG) are deliberately
 * skipped: those are WEEKLY rankings, not the draft board, and they read "--"
 * outside the season anyway.
 */

(async () => {
  const rows = [];
  const seen = new Set();

  const text = (el) => (el?.textContent ?? '').trim();

  const grabCurrentPage = () => {
    let added = 0;
    // Table__TR--lg is the tall player row; the header rows do not carry it.
    document.querySelectorAll('tbody.Table__TBODY tr.Table__TR--lg').forEach((tr) => {
      const athlete = tr.querySelector('.player-column__athlete');
      // The first AnchorLink is the player's name; the second is his news link.
      const name =
        text(athlete?.querySelector('a.AnchorLink')) ||
        (athlete?.getAttribute('title') ?? '').trim();

      const team = text(tr.querySelector('.playerinfo__playerteam')).toUpperCase();
      const position = text(tr.querySelector('.playerinfo__playerpos')).toUpperCase();

      // Team defenses have no team abbreviation of their own, so key on the
      // name alone for them rather than skipping the row.
      const key = team ? `${name}|${team}` : name;
      if (name && !seen.has(key)) {
        seen.add(key);
        rows.push({
          espn_rank: rows.length + 1,   // the row order IS the ranking
          name,
          team,
          position,
        });
        added++;
      }
    });
    return added;
  };

  const nextButton = () =>
    document.querySelector('button.Pagination__Button--next');

  const isDisabled = (btn) =>
    !btn || btn.disabled || btn.getAttribute('aria-disabled') === 'true';

  const lastPage = () => {
    const items = [...document.querySelectorAll('.Pagination__list a[data-nav-item]')]
      .map((a) => parseInt(a.getAttribute('data-nav-item'), 10))
      .filter((n) => !isNaN(n));
    return items.length ? Math.max(...items) : null;
  };

  const firstRowName = () =>
    text(document.querySelector('tbody.Table__TBODY tr.Table__TR--lg .player-column__athlete a.AnchorLink'));

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  if (!document.querySelector('tbody.Table__TBODY tr.Table__TR--lg')) {
    console.log(
      'No player rows found. Make sure the ranked player table is visible, ' +
      'then re-run. If ESPN changed its markup, right-click a player row -> ' +
      'Inspect and share the HTML so the selector can be fixed.'
    );
    return;
  }

  const total = lastPage();
  console.log(`Starting. ${total ? `${total} pages detected.` : 'Page count unknown.'}`);
  console.log(`First player: ${firstRowName()} — confirm this is the top of the board.`);

  grabCurrentPage();
  console.log(`Page 1: ${rows.length} players`);

  let page = 1;
  let guard = 0;
  while (guard++ < 200) {
    const next = nextButton();
    if (isDisabled(next)) break;

    const before = firstRowName();
    next.click();

    // Wait for the table to actually change (up to ~6s). Clicking is async, and
    // grabbing too early would re-read the page we just left.
    let changed = false;
    for (let i = 0; i < 30; i++) {
      await sleep(200);
      const now = firstRowName();
      if (now && now !== before) { changed = true; break; }
    }
    if (!changed) {
      console.log(`Page ${page + 1}: table never changed — stopping here.`);
      break;
    }

    page++;
    const added = grabCurrentPage();
    console.log(`Page ${page}${total ? `/${total}` : ''}: +${added} players (total ${rows.length})`);
    if (added === 0) break;   // last page reached, or the table stopped moving
  }

  const columns = ['espn_rank', 'name', 'team', 'position'];
  const escape = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [
    columns.join(','),
    ...rows.map((r) => columns.map((c) => escape(r[c])).join(',')),
  ].join('\n');

  // Stash the result globally — DevTools' copy() helper is NOT available
  // inside async code after an await, so it can't be called from here.
  window.__espnCSV = csv;

  const missingPosition = rows.filter((r) => !r.position).length;

  try {
    await navigator.clipboard.writeText(csv);
    console.log(`Done — ${rows.length} players copied to clipboard as CSV.`);
    console.log('Paste into a new file and save as data/espn_board_rankings.csv');
  } catch (e) {
    console.log(`Done — ${rows.length} players collected (clipboard write was blocked).`);
    console.log('Type this in the console to copy the CSV, then paste it into a file:');
    console.log('copy(window.__espnCSV)');
  }

  console.log(`Ranks 1..${rows.length}, taken from row order.`);
  if (missingPosition) {
    console.log(`Note: ${missingPosition} row(s) had no position — usually team defenses.`);
  }
  if (total && page < total) {
    console.log(
      `Warning: stopped on page ${page} of ${total}. The CSV is incomplete — ` +
      `re-run, or share the pagination HTML if the next button was not found.`
    );
  }
})();
