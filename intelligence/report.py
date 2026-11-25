from datetime import datetime, timedelta, timezone
import json
import os
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .storage import encode, now


def prepare_daily(store):
    local = datetime.now(ZoneInfo("America/New_York"))
    report_date = local.date().isoformat()
    threshold = (local.astimezone(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    rows = list(store.execute("""select v.*, e.name from vacancies v
        join employers e on e.id=v.employer_id where v.first_seen>=? or v.last_changed>=?
        order by v.score desc""", (threshold, threshold)))
    latest = store.execute("select * from runs order by started desc limit 1").fetchone()
    audit = json.loads(latest["audit"]) if latest and latest["audit"] else None
    summary = ("No completed coverage run is available." if not audit else
               f"{audit['status']} • Top 150 {audit['top_successful']}/150 • "
               f"Unranked priority queue {audit['priority_unranked_successful']}/{audit['priority_unranked']} • "
               f"Run finished {latest['finished']}")
    sections = [summary, f"New eligible active vacancies in the last 24 hours: "
                f"{sum(row['status']=='active' and (row['first_seen'] or '')>=threshold for row in rows)}"]
    for heading, statuses in [
        ("Verified active", {"active"}), ("Unverified", {"unverified"}),
        ("Recently closed or removed", {"filled", "inactive", "removed", "expired"}),
        ("Off-market", {"off_market"}),
    ]:
        sections.append(f"\n{heading}")
        matches = [row for row in rows if row["status"] in statuses]
        if not matches:
            sections.append("No records in this reporting window.")
        for row in matches:
            detail = json.loads(row["details"])
            sections.append("\n".join([
                f"{row['name']} — {row['title']}",
                row["location"] or "Location unknown",
                f"Scope: {', '.join(detail.get('scope', [])) or 'unknown'}",
                f"Compensation: {detail.get('compensation') or 'unknown'} • Equity: {detail.get('equity') or 'unknown'}",
                f"Posted: {detail.get('published') or 'unknown'} • Discovered: {row['first_seen'] or 'unknown'}",
                row["url"], f"Evidence: {row['evidence'] or 'unknown'}",
                "Next action: review the official listing." if row["status"] == "active"
                else "Next action: retain for history or networking."
            ]))
    with store.transaction():
        store.execute("insert into deliveries values (?,?,?,?,?,null) on conflict(id) do nothing",
                      (f"daily-{report_date}", "daily", f"Executive intelligence • {report_date}",
                       "\n\n".join(sections), now()))


def send_pending(store):
    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["REPORT_FROM"]
    recipient = os.environ["REPORT_TO"]
    for item in list(store.execute("select * from deliveries where sent is null order by created,id")):
        request = Request("https://api.resend.com/emails", data=encode({
            "from": sender, "to": [recipient], "subject": item["subject"], "text": item["body"]
        }).encode(), headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                               "Idempotency-Key": item["id"]})
        with urlopen(request, timeout=20) as response:
            if not json.load(response).get("id"):
                raise ValueError("Email provider did not confirm acceptance")
        with store.transaction():
            store.execute("update deliveries set sent=? where id=?", (now(), item["id"]))
