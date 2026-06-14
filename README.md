# type shi message dashboard

Local dashboard for aggregate Messages stats. The generator reads your macOS Messages database in read-only mode and writes aggregate JSON for the static frontend.

## Refresh data

```bash
python3 scripts/generate_data.py --days 365 --default-window-days 14 --share-safe
```

Defaults:

- group: `type shi`
- data range: trailing 365 calendar days
- default dashboard window: trailing 14 days
- output: `public/data/summary.json`
- hosted data: share-safe mode removes phone-tail details and chat row metadata

Use a different group or window:

```bash
python3 scripts/generate_data.py --group "type shi" --days 365 --default-window-days 30 --share-safe
```

## Optional local detectors

Vibe awards are scored as a percentage of that person's own sent messages in the selected window. For example, `Pick-me radar` is:

```text
messages from that person containing a pick-me signal / messages sent by that person
```

Awards require at least 25 sent messages in the selected window. `Reaction warrior` uses reactions sent divided by messages sent, so it reads as reactions per 100 messages.

Slur counts are supported through a local-only lexicon that is ignored by git:

```bash
cp config/slur_terms.example.json config/slur_terms.local.json
python3 scripts/generate_data.py --days 365 --default-window-days 14 --share-safe --slur-lexicon config/slur_terms.local.json
```

The public JSON publishes category counts only, not the lexicon terms or message text. Highest-reaction message previews are also off by default; add `--include-message-previews` only for a private/local build.

## Run the site

```bash
python3 -m http.server 4173 --directory public
```

Then open `http://localhost:4173`.

## Privacy

The generated JSON does not include message text or raw phone numbers. In share-safe mode it includes aggregate counts, timestamps, and contact names when available.
