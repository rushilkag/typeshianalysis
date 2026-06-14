# type shi message dashboard

Local dashboard for aggregate Messages stats. The generator reads your macOS Messages database in read-only mode and writes aggregate JSON for the static frontend.

## Refresh data

```bash
python3 scripts/generate_data.py --share-safe
```

Defaults:

- group: `type shi`
- window: trailing 14 days
- output: `public/data/summary.json`
- hosted data: share-safe mode removes phone-tail details and chat row metadata

Use a different group or window:

```bash
python3 scripts/generate_data.py --group "type shi" --days 30 --share-safe
```

## Run the site

```bash
python3 -m http.server 4173 --directory public
```

Then open `http://localhost:4173`.

## Privacy

The generated JSON does not include message text or raw phone numbers. In share-safe mode it includes aggregate counts, timestamps, and contact names when available.
