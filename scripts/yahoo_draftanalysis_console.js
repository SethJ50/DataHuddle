/*
 * Yahoo Draft Analysis -> CSV, run in the browser console.
 *
 * The draft analysis table is login-gated, JS-rendered, and paginated 30
 * players at a time, so it can't be fetched by a script. This snippet runs in
 * the browser where you're already logged in: it collects the current page's
 * rows, clicks "next" until the last page, then downloads everything as
 * yahoo_draftanalysis.csv.
 *
 * Usage:
 *   1. Open https://football.fantasysports.yahoo.com/f1/draftanalysis
 *      and set the position filter to ALL (page 1).
 *   2. Open DevTools (Cmd+Option+I) -> Console tab.
 *   3. Paste this entire file and press Enter.
 *      (If Chrome says pasting is blocked, type: allow pasting  and retry.)
 *   4. Wait while it pages through the table (progress logs in the console).
 *      When it finishes it tries to copy the CSV to your clipboard; if that
 *      is blocked, it tells you to type:  copy(window.__yahooCSV)
 *   5. Paste your clipboard into a new file and save it as:
 *      data/yahoo_draftanalysis.csv
 *
 * Locked Fantasy Plus cells (Pos Rank, CER, Plus ADP) come out blank.
 * Yahoo's ADP on this page is based on standard scoring.
 */

(async () => {
  const rows = [];
  const seen = new Set();

  const grabCurrentPage = () => {
    let added = 0;
    document.querySelectorAll('tr[data-tst^="table-row"]').forEach((tr) => {
      const name = tr.querySelector('[data-tst="player-name"]')?.textContent.trim();
      const posEl = tr.querySelector('[data-tst="player-position"]');
      const position = posEl?.textContent.trim() ?? '';
      const team = (posEl?.parentElement?.textContent.split('-')[0] ?? '')
        .trim()
        .toUpperCase();

      const tds = tr.querySelectorAll('td');
      const cell = (i) => {
        const t = tds[i]?.textContent.trim().replace('%', '');
        const n = parseFloat(t);
        return t && !isNaN(n) ? n : '';
      };

      const key = `${name}|${team}`;
      if (name && !seen.has(key)) {
        seen.add(key);
        rows.push({
          yahoo_rank: cell(1),
          name,
          team,
          position,
          percent_drafted: cell(4),
          preseason_adp: cell(5),
          adp: cell(6), // "All Drafts"
        });
        added++;
      }
    });
    return added;
  };

  const findNextButton = () => {
    // Yahoo's next-page button is a bare <button> containing a caret-right icon
    const caret = document.querySelector('button svg[data-icon="caret-right"]');
    if (caret) return caret.closest('button');
    // fallback heuristics in case the markup changes
    return [...document.querySelectorAll('button, a')].find((el) => {
      const label = (el.getAttribute('aria-label') || '').trim();
      const text = el.textContent.trim();
      return /next/i.test(label) || /^next$/i.test(text) || text === '>' || text === '›';
    });
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  grabCurrentPage();
  console.log(`Page 1: ${rows.length} players`);

  let guard = 0;
  while (guard++ < 60) {
    const next = findNextButton();
    if (!next || next.disabled || next.getAttribute('aria-disabled') === 'true') break;

    const firstNameBefore = document.querySelector('[data-tst="player-name"]')?.textContent;
    next.click();

    // wait for the table to actually change (up to ~6s)
    for (let i = 0; i < 30; i++) {
      await sleep(200);
      const firstNameNow = document.querySelector('[data-tst="player-name"]')?.textContent;
      if (firstNameNow && firstNameNow !== firstNameBefore) break;
    }

    const added = grabCurrentPage();
    console.log(`Page ${guard + 1}: +${added} players (total ${rows.length})`);
    if (added === 0) break; // last page reached (or table stopped changing)
  }

  rows.sort((a, b) => (a.yahoo_rank || 1e9) - (b.yahoo_rank || 1e9));

  const columns = ['yahoo_rank', 'name', 'team', 'position', 'percent_drafted', 'preseason_adp', 'adp'];
  const escape = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [
    columns.join(','),
    ...rows.map((r) => columns.map((c) => escape(r[c])).join(',')),
  ].join('\n');

  // Stash the result globally — DevTools' copy() helper is NOT available
  // inside async code after an await, so it can't be called from here.
  window.__yahooCSV = csv;

  try {
    await navigator.clipboard.writeText(csv);
    console.log(`Done — ${rows.length} players copied to clipboard as CSV.`);
    console.log('Paste into a new file and save as data/yahoo_draftanalysis.csv');
  } catch (e) {
    console.log(`Done — ${rows.length} players collected (clipboard write was blocked).`);
    console.log('Type this in the console to copy the CSV, then paste it into a file:');
    console.log('copy(window.__yahooCSV)');
  }
  if (!findNextButton()) {
    console.log(
      'Note: no "next" button was found. If the table has more pages, ' +
      'right-click the next-page button -> Inspect, and share its HTML so the selector can be fixed.'
    );
  }
})();