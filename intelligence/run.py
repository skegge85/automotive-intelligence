import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
import logging
import time
from urllib.error import HTTPError, URLError
from uuid import uuid4

from .adapters import ADAPTERS, IncompleteInventory, posting_details
from .model import Job, MANAGEMENT, resolve_observations
from .storage import ROOT, Store, encode, now

LOG = logging.getLogger(__name__)


def registry_data(path):
    registry = json.loads(path.read_text(encoding="utf-8"))
    ranked = [row for row in registry["employers"] if row["rank"] is not None]
    positions = [row["roster_row"] for row in ranked]
    ranks = [row["rank"] for row in sorted(ranked, key=lambda row: row["roster_row"])]
    expected = [*range(1, 140), 139, *range(141, 151)]
    keys = [row["id"] for row in registry["employers"]]
    if (registry["ranking_size"] != 150 or len(positions) != len(set(positions)) or
        sorted(positions) != list(range(1, 151)) or ranks != expected or
        len(keys) != len(set(keys))):
        raise ValueError("Registry rows do not match the published 2024 ranking or employer identifiers repeat")
    return registry


def check(employer):
    result = {"started": now(), "attempted": 0, "status": "unconfigured",
              "jobs": None, "errors": [], "attempts": [], "detail_failures": [],
              "source": employer["board"] or employer["portal"]}
    reader = ADAPTERS.get(employer["method"])
    if reader:
        result["attempted"] = 1
        for attempt in range(1, 3):
            started = now()
            try:
                try:
                    jobs = reader(employer)
                    inventory_status = "complete"
                except IncompleteInventory as error:
                    inventory_status = "partial_inventory"
                    result["errors"].append(str(error))
                    if attempt == 1:
                        result["attempts"].append({"attempt": attempt, "started": started,
                                                   "status": inventory_status, "error": str(error)})
                        time.sleep(2)
                        continue
                    jobs = error.jobs
                result["attempts"].append({"attempt": attempt, "started": started, "status": inventory_status})
                for job in jobs:
                    if not (job.eligible or MANAGEMENT.search(job.title)):
                        continue
                    try:
                        found = posting_details(job)
                    except (URLError, ValueError, OSError, KeyError, TypeError) as error:
                        LOG.info("Detail unavailable for %s %s: %s", employer["id"], job.requisition, error)
                        found = False
                    if not found and not job.eligible:
                        result["detail_failures"].append(job.requisition or job.url)
                result["jobs"] = jobs
                result["status"] = inventory_status if inventory_status != "complete" else (
                    "partial_evaluation" if result["detail_failures"] else "success")
                break
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                result["errors"].append(message)
                result["attempts"].append({"attempt": attempt, "started": started,
                                           "status": "failed", "error": message})
                result["status"] = "failed"
                if isinstance(error, PermissionError) or (
                    isinstance(error, HTTPError) and error.code in (401, 403, 404, 429)):
                    break
                if attempt == 1:
                    time.sleep(2)
    result["finished"] = now()
    return result


def record_vacancy(store, employer, job, status, source_type, evidence, stamp, run_id, extras=None):
    rows = list(store.execute("""select * from vacancies where employer_id=?
        and (requisition=? or normalized_url=?)""", (employer, job.requisition, job.normalized_url)))
    if len(rows) > 1:
        raise ValueError(f"Conflicting posting identities for {employer} {job.requisition}")
    old = rows[0] if rows else None
    if old and job.requisition and old["requisition"] not in (None, job.requisition):
        raise ValueError(f"Source URL reused by a different requisition for {employer}")
    vacancy_id = old["id"] if old else uuid4().hex
    details = {**asdict(job), "scope": job.scope, **(extras or {})}
    if old:
        prior_details = json.loads(old["details"])
        for field, value in prior_details.items():
            if details.get(field) is None:
                details[field] = value
    details = encode(details)
    requisition = job.requisition or (old["requisition"] if old else None)
    changed = not old or old["status"] != status or old["details"] != details
    discovery = (old["first_seen"] if old else None) or (stamp if status == "active" else None)
    store.execute("""insert into vacancies
        (id,employer_id,requisition,url,normalized_url,title,location,status,source_type,
         evidence,score,first_seen,last_verified,last_changed,details)
        values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(id) do update set
        requisition=excluded.requisition,url=excluded.url,normalized_url=excluded.normalized_url,
        title=excluded.title,location=excluded.location,status=excluded.status,source_type=excluded.source_type,
        evidence=excluded.evidence,score=excluded.score,first_seen=excluded.first_seen,
        last_verified=excluded.last_verified,last_changed=excluded.last_changed,details=excluded.details""",
        (vacancy_id, employer, requisition, job.url, job.normalized_url, job.title,
         job.location or (old["location"] if old else None), status, source_type, evidence,
         job.score, discovery, stamp if status != "removed" else old["last_verified"],
         stamp if changed else old["last_changed"], details))
    if changed:
        kind = "new" if not old else "reappeared" if old["status"] != "active" and status == "active" else status if status != "active" else "modified"
        store.execute("insert into vacancy_events values (?,?,?,?,?)",
                      (uuid4().hex, vacancy_id, stamp, kind,
                       encode({"status": status, "evidence": evidence, "run": run_id,
                               "details": json.loads(details)})))
        return kind, vacancy_id
    return None, vacancy_id


def import_history(store):
    fixtures = json.loads((ROOT / "fixtures/requisitions.json").read_text(encoding="utf-8"))
    with store.transaction():
        for fixture in fixtures:
            existing = store.execute("select id from vacancies where employer_id=? and requisition=?",
                                     (fixture["employer"], fixture["requisition"])).fetchone()
            if existing:
                continue
            job = Job(fixture["title"], fixture["url"], fixture["requisition"], fixture.get("location"))
            status = resolve_observations(fixture["observations"])
            record_vacancy(store, fixture["employer"], job, status, "historical_source",
                           encode(fixture["observations"]), now(), None,
                           {"application_status": fixture.get("application_status"),
                            "historical_observations": fixture["observations"], "historical_url": fixture["url"]})


def persist_check(store, run_id, employer, result):
    jobs = result["jobs"]
    eligible = [job for job in jobs or [] if job.eligible]
    changes = {"new": 0, "changed": 0}
    key = employer["id"]
    stamp = result["finished"]
    evidence = evidence_summary(result)
    with store.transaction():
        store.execute("insert into checks values (?,?,?,?,?,?,?,?,?)",
                      (run_id, key, result["attempted"], result["started"], stamp,
                       result["status"], len(jobs) if jobs is not None else None,
                       len(eligible) if jobs is not None else None, encode(evidence)))
        store.execute("""update employers set status=?, last_attempt=coalesce(?,last_attempt),
                      last_success=coalesce(?,last_success) where id=?""",
                      (result["status"], stamp if result["attempted"] else None,
                       stamp if result["status"] == "success" else None, key))
        if jobs is None:
            return changes
        seen = {job.requisition for job in jobs if job.requisition}
        urls = {job.normalized_url for job in jobs}
        active = list(store.execute("select * from vacancies where employer_id=? and status='active'", (key,)))
        for job in eligible:
            prior = store.execute("select details from vacancies where employer_id=? and requisition=?",
                                  (key, job.requisition)).fetchone()
            history = json.loads(prior["details"]).get("historical_observations", []) if prior else []
            conflicting = any(obs["direct"] and obs["status"] in ("filled", "inactive", "expired") for obs in history)
            status = "unverified" if conflicting else "active"
            message = {"inventory_url": job.url, "inventory_evidence": "Present in direct public inventory"}
            if conflicting:
                message["conflicting_direct_observations"] = history
                message["alternate_source"] = json.loads(prior["details"]).get("historical_url")
            kind, vacancy_id = record_vacancy(store, key, job, status, employer["method"],
                                               encode(message), stamp, run_id)
            if kind:
                changes["new" if kind == "new" else "changed"] += 1
            if status == "active" and kind in ("new", "reappeared"):
                body = make_notice(employer["name"], job, stamp)
                alert_id = hashlib.sha256(f"{run_id}:{vacancy_id}".encode()).hexdigest()
                store.execute("insert into deliveries values (?,?,?,?,?,null) on conflict(id) do nothing",
                              (alert_id, "alert", f"{employer['name']} • {job.title}", body, stamp))
        if result["status"] == "success":
            for prior in active:
                if prior["requisition"] in seen or prior["normalized_url"] in urls:
                    continue
                payload = json.loads(prior["details"])
                payload.pop("scope", None)
                payload.pop("application_status", None)
                payload.pop("historical_observations", None)
                payload.pop("historical_url", None)
                record_vacancy(store, key, Job(**payload), "removed", employer["method"],
                               "Absent from a complete inventory; disposition unknown", stamp, run_id)
                changes["changed"] += 1
    return changes


def make_notice(name, job, stamp):
    return "\n".join([name, job.title, job.location or "Location unknown",
        f"Scope: {', '.join(job.scope) or 'unknown'}",
        f"Published compensation: {job.compensation or 'unknown'}",
        f"Equity: {job.equity or 'unknown'}", f"Original posting: {job.published or 'unknown'}",
        f"Discovered: {stamp}", job.url, "Evidence: present on the direct career inventory.",
        "Next action: review the employer’s posting and decide whether to apply."])


def evidence_summary(result):
    jobs = result["jobs"]
    return {**{key: value for key, value in result.items() if key != "jobs"},
            "retrieved": None if jobs is None else len(jobs),
            "eligible": None if jobs is None else sum(job.eligible for job in jobs)}


def scan(store, registry):
    start = time.monotonic()
    started = now()
    run_id = uuid4().hex
    with store.transaction():
        store.execute("insert into runs (id,started,status) values (?,?,'running')", (run_id, started))
    audit = dict(required_top=150, verified_rank_rows=sum(e["rank"] is not None for e in registry["employers"]),
                 priority_unranked=sum(e["rank"] is None for e in registry["employers"]),
                 attempted=0, successful=0, top_successful=0, priority_unranked_successful=0,
                 retrieved=0, eligible=0, new=0, changed=0, employer_results={})
    with ThreadPoolExecutor(max_workers=4) as workers:
        scheduled = {workers.submit(check, e): e for e in registry["employers"]}
        for future in as_completed(scheduled):
            employer = scheduled[future]
            result = future.result()
            changes = persist_check(store, run_id, employer, result)
            jobs = result["jobs"]
            success = int(result["status"] == "success")
            audit["attempted"] += result["attempted"]
            audit["successful"] += success
            audit["top_successful" if employer["rank"] else "priority_unranked_successful"] += success
            audit["retrieved"] += len(jobs or [])
            audit["eligible"] += sum(job.eligible for job in jobs or [])
            for key, number in changes.items():
                audit[key] += number
            audit["employer_results"][employer["id"]] = {**evidence_summary(result), "name": employer["name"]}
            if result["attempted"]:
                LOG.info("%s %s (%s)", employer["name"], result["status"], len(jobs) if jobs is not None else "unknown")
    audit["required_total"] = 150 + audit["priority_unranked"]
    audit["unidentified_rank_slots"] = 150 - audit["verified_rank_rows"]
    audit["top_coverage_pct"] = round(100 * audit["top_successful"] / 150, 2)
    priority = audit["priority_unranked"]
    audit["priority_coverage_pct"] = round(100 * audit["priority_unranked_successful"] / priority, 2) if priority else None
    audit["seconds"] = round(time.monotonic() - start, 2)
    audit["status"] = "COMPLETE" if audit["top_successful"] == 150 and audit["priority_unranked_successful"] == priority else "PARTIAL"
    audit["run_id"] = run_id
    with store.transaction():
        store.execute("update runs set finished=?,status=?,audit=? where id=?",
                      (now(), audit["status"], encode(audit), run_id))
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "report", "import-history", "deliver"])
    parser.add_argument("--db", default="intelligence.sqlite3")
    parser.add_argument("--registry", type=type(ROOT), default=ROOT / "registry.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(args.db)
    try:
        from .report import send_pending, prepare_daily
        registry = registry_data(args.registry)
        store.seed(registry)
        if args.command == "scan":
            print(encode(scan(store, registry)))
        elif args.command == "report":
            prepare_daily(store)
        elif args.command == "import-history":
            import_history(store)
        if args.command == "deliver":
            send_pending(store)
    finally:
        store.close()


if __name__ == "__main__":
    main()
