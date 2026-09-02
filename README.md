# PA Districts Map

A self-contained, interactive map of Pennsylvania's political geography — U.S. House, State Senate, State House, counties, municipalities, and school districts — with officeholders, partisan lean, voter registration, and demographics for each.

Live: served via GitHub Pages from this repo's `main` branch.

## Keeping voter registration current

`scripts/sync_voter_reg.py` refreshes the county-level voter registration
numbers embedded in `pa-districts-map.html` from the PA Department of
State's official weekly export:
https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/voting-and-election-statistics

A scheduled GitHub Actions workflow (`.github/workflows/sync-voter-reg.yml`)
runs it every Tuesday and commits the update automatically if the source
data changed. Run it by hand from the **Actions** tab (`workflow_dispatch`)
to force a refresh, or locally:

```
pip install openpyxl
python scripts/sync_voter_reg.py
```

The script validates the source against PA's 67 counties and fails loudly
(nonzero exit, no file write) if the source's shape ever changes in a way
it doesn't recognize, rather than risk writing bad data silently.
