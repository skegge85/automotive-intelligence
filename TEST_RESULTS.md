# Validation results

## Local tests

- 11 Python regression tests passed. They cover the known requisitions, conflicting evidence, lifecycle and alert deduplication, failed checks, ranking ties and entity identity, pagination, and daily reporting with zero discoveries.
- Chrome dashboard smoke passed at 390 px using mocked Supabase Auth and REST. Tested sign-in and sign-out, search, bookmarking, application stages, notes, and the coverage view.
- JavaScript syntax checks and Netlify TOML parse passed. Hosted PostgreSQL policies, Netlify deploys, Actions scheduling, provider delivery, and inbox receipt require a configured environment.

## Live employer baseline

Run on November 25, 2026, using the requested 2024 published roster. Runtime was 88.42 seconds locally. Hosted CI minutes have yet to be measured.

| Metric | Result |
| --- | ---: |
| Registry | 150 ranked + 6 additional |
| Required employers | 156 |
| Attempted employers | 7 |
| Successful full checks | 4 |
| Top 150 full coverage | 3/150 (2.0%) |
| Additional priority full coverage | 1/6 (16.67%) |
| Posting rows retrieved, including usable partial inventories | 2019 |
| Deterministically eligible candidates, including unverified | 22 |
| New records from this run | 21 |
| Changed records from this run | 1 |
| Status | **PARTIAL** |

| Attempted employer | Result | Posting rows | Evidence |
| --- | --- | ---: | --- |
| Asbury Automotive Group | failed | Unknown | PermissionError: Public robots policy disallows /v1/companies/AsburyAutomotiveGroup/postings |
| AutoNation | success | 642 | Inventory evaluated |
| Holman | success | 181 | Inventory evaluated |
| Lithia & Driveway | partial_inventory | 974 | Inventory contains 1 incomplete posting rows · Inventory contains 2 incomplete posting rows |
| MileOne Autogroup | success | 164 | Inventory evaluated |
| Preston Automotive Group | success | 58 | Inventory evaluated |
| Sonic Automotive | failed | Unknown | PermissionError: Public robots policy disallows /v1/companies/SonicAutomotive/postings |

[Machine-readable audit](validation/baseline-audit.json) includes every listed employer, timestamps, retry results, and error evidence. Unconfigured employers remain uncovered.

## Known requisitions

- **AutoNation JR2026152536 · Unverified.** The branded career page states that the job was filled. The same requisition appears in AutoNation's public Workday inventory. Both sources are preserved, the prior application status is retained, and the conflict suppresses active alerts. [Branded career page](https://careers.autonation.com/us/en/job/JR2026152536/Market-President) · [Workday posting](https://autonation.wd5.myworkdayjobs.com/Careers/job/BMW-of-Buena-Park/Market-President_JR2026152536)
- **Preston Automotive Group 3532264 · Inactive.** Paylocity redirects to its Job Not Found page and reports that the posting is missing or inactive. Its disposition is unknown. [Original posting](https://recruiting.paylocity.com/recruiting/jobs/Details/3532264/Preston-Automotive-Group/Automotive-Market-Area-Director)

The local outbox contains 21 candidate alerts and one daily digest. Provider delivery has yet to be tested.
