# type shi message dashboard

Local dashboard for aggregate Messages stats. The generator reads your macOS Messages database in read-only mode and writes aggregate JSON for the static frontend.

Message totals are normalized into conversation turns by default. Consecutive same-sender message bubbles within 30 seconds count as one turn, so rapid-fire split thoughts do not dominate the leaderboard. Raw bubble counts are still included as context.

Tapback stats are structured data from Messages, not AI classifications. `Reaction demon` ranks who sent the most reactions in the selected window. `Most liked` ranks whose messages received the most reactions in the selected window.

## Refresh data

```bash
python3 scripts/generate_data.py --days 365 --default-window-days 14 --share-safe --include-message-previews
```

Defaults:

- group: `type shi`
- data range: trailing 365 calendar days
- default dashboard window: trailing 14 days
- normalization: consecutive same-sender bubbles within 30 seconds count as one conversation turn
- output: `public/data/summary.json`
- hosted data: share-safe mode removes phone-tail details and chat row metadata, while highest-reaction message cards include short text previews or media-type evidence

Use a different group or window:

```bash
python3 scripts/generate_data.py --group "type shi" --days 365 --default-window-days 30 --share-safe --include-message-previews
```

Change the normalization gap:

```bash
python3 scripts/generate_data.py --days 365 --default-window-days 14 --share-safe --turn-gap-seconds 45
```

Generate an internal-only file with raw contact identifiers:

```bash
npm run generate:internal
```

That writes `public/data/summary.internal.json`, which is ignored by git and should not be deployed.

## Optional local detectors

AI sentiment rankings are generated separately from the normal dashboard data:

```bash
export OPENAI_API_KEY="..."
npm run classify
```

This writes `public/data/sentiments.json` with aggregate percentages and short example quotes for `Most racist`, `Most pick me`, `Most self insert`, `Biggest brainrot`, `Most vulnerable`, and `Biggest glazer`. The local cache is written to `public/data/sentiment-cache.local.json` and ignored by git.

Slur counts are supported through a local-only lexicon that is ignored by git:

```bash
cp config/slur_terms.example.json config/slur_terms.local.json
python3 scripts/generate_data.py --days 365 --default-window-days 14 --share-safe --include-message-previews --slur-lexicon config/slur_terms.local.json
```

The public JSON publishes category counts only, not the lexicon terms.

## Run the site

```bash
python3 -m http.server 4173 --directory public
```

Then open `http://localhost:4173`.

## Privacy

The hosted `summary.json` includes short highest-reaction message previews but does not include raw phone numbers or raw contact handles. Use the internal-only command for local debugging with raw contact identifiers.
