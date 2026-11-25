# Automotive Intelligence

Python vacancy monitor, persistent SQLite and PostgreSQL storage, and a separate Netlify dashboard backed by Supabase Auth. The configured baseline is the public 2024 Automotive News Top 150, ranked on 2023 new-vehicle retail sales. The registry contains 150 published rows and six additional priorities. Current portal coverage is reported separately from roster coverage.

## Run locally

Python 3.12 or newer. Local scans and tests use the standard library.

```sh
python -m unittest discover -s tests -v
python -m intelligence.run import-history
python -m intelligence.run scan
python -m intelligence.run report
```

The default database is `intelligence.sqlite3`. Supply `--db /path/to/state.sqlite3` to use a different file. The scan prints its audit as JSON, and the database retains per-employer results, lifecycle events and pending deliveries. The `report` command queues one report per Eastern calendar day. It also queues a report when there are zero discoveries. Delivery runs only through the separate `deliver` command.

See [architecture](ARCHITECTURE.md), [implementation tracker](TRACKER.md), [validation results](TEST_RESULTS.md) and [deployment](DEPLOYMENT.md). Review the implementation tracker before relying on this system for executive search coverage.

## Historical ranking source

[Automotive News 2024 Top 150 dealership groups](https://crain-platform-autonews-assets.s3.amazonaws.com/projects/data/pdf/top-150-dealership-groups-2024.pdf), pages 7–12 and index on page 13. The source uses 2023 sales and has a tie at No. 139. The registry uses distinct employer identifiers and preserves both reported ranks. A domain listed in the historical ranking is recorded as a historical domain. Only evidence-backed current portals appear in the portal field.

The ranked Kelly group is based in Danvers, Massachusetts. The priority Kelly Auto Group is in the Allentown, Pennsylvania area. Cavender Auto Family and Cavender Auto Group are represented separately.
