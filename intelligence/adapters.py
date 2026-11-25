"""Public board readers. Unknown payloads and incomplete pagination raise errors."""
from html import unescape
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
from urllib.error import HTTPError

from .model import Job
from .browser import read as browser

AGENT = "AutomotiveIntelligence"
HOST_LOCK = threading.Lock()
HOST_TIMES = {}
ROBOTS = {}
ENTITY_MARKUP = re.compile(r"<[^>]*>")


def _fetch(url, body=None):
    host = urlsplit(url).netloc
    if urlsplit(url).scheme != "https":
        raise ValueError("Career board must use HTTPS")
    with HOST_LOCK:
        now = time.monotonic()
        slot = max(now, HOST_TIMES.get(host, now))
        HOST_TIMES[host] = slot + 0.5
    time.sleep(max(0, slot - time.monotonic()))
    request = Request(url, data=None if body is None else json.dumps(body).encode(),
                      headers={"User-Agent": AGENT, "Accept": "*/*",
                               "Content-Type": "application/json"})
    with urlopen(request, timeout=18) as response:
        if urlsplit(response.url).netloc.lower() != host.lower():
            raise ValueError("Career portal redirected to a different host")
        return response.read(12_000_000).decode("utf-8-sig")


def public_allowed(url):
    parsed = urlsplit(url)
    key = parsed.netloc
    if key not in ROBOTS:
        robots_url = f"{parsed.scheme}://{key}/robots.txt"
        robots = RobotFileParser(robots_url)
        try:
            robots.parse(_fetch(robots_url).splitlines())
        except HTTPError as error:
            if error.code not in (404, 410):
                raise
            robots.parse([])
        ROBOTS[key] = robots
    return ROBOTS[key].can_fetch(AGENT, url)


def fetch(url, body=None):
    if not public_allowed(url):
        parsed = urlsplit(url)
        raise PermissionError(f"Public robots policy disallows {parsed.path}")
    return _fetch(url, body)


def embedded(html, marker):
    match = re.search(rf"{re.escape(marker)}\s*=\s*", html)
    if not match:
        raise ValueError(f"Missing structured inventory ({marker})")
    return json.JSONDecoder().raw_decode(html[match.end():])[0]


class IncompleteInventory(ValueError):
    def __init__(self, jobs, missing):
        super().__init__(f"Inventory contains {missing} incomplete posting rows")
        self.jobs = jobs
        self.missing = missing


def paginated(page, size, max_pages=250, first_page_total_only=False):
    """Require stable source totals and distinct requisitions across all pages."""
    jobs = {}
    expected = None
    missing = 0
    for number in range(max_pages):
        total, rows = page(number * size)
        if type(total) is not int or total < 0 or not isinstance(rows, list):
            raise ValueError("Invalid inventory payload")
        expected = total if expected is None else expected
        if (total != expected and not (first_page_total_only and number and total == 0 and rows)) or len(rows) != min(size, max(0, expected - len(jobs) - missing)):
            raise ValueError(f"Incomplete page at offset {number * size}: total {total}, expected total {expected}, rows {len(rows)}, prior rows {len(jobs)}")
        for job in rows:
            if job is None:
                missing += 1
                continue
            key = job.requisition or job.normalized_url
            if key in jobs:
                raise ValueError("Pagination repeated a posting")
            jobs[key] = job
        if len(jobs) + missing == expected:
            if missing:
                raise IncompleteInventory(list(jobs.values()), missing)
            return list(jobs.values())
    raise ValueError("Page limit reached before inventory was exhausted")


def workday(employer):
    board = urlsplit(employer["board"])
    tenant = board.hostname.split(".")[0]
    site = board.path.strip("/").split("/")[-1]
    base = f"https://{board.netloc}"
    endpoint = f"{base}/wday/cxs/{tenant}/{site}"

    def page(offset):
        data = json.loads(fetch(f"{endpoint}/jobs", {
            "limit": 20, "offset": offset, "searchText": "", "appliedFacets": {}}))
        jobs = []
        for row in data["jobPostings"]:
            if not row.get("externalPath") or not row.get("title"):
                jobs.append(None)
                continue
            if not row["externalPath"].startswith("/job/"):
                raise ValueError("Unexpected Workday job path")
            jobs.append(Job(row["title"], base + board.path.rstrip("/") + row["externalPath"],
                            next(iter(row.get("bulletFields", [])), None),
                            row.get("locationsText")))
        return data["total"], jobs

    return paginated(page, 20, first_page_total_only=True)


def paylocity(employer):
    board = employer["board"]
    data = embedded(fetch(board), "window.pageData")
    if data.get("ModuleTitle") != employer["name"] or data.get("ShowInternal") is not False:
        raise ValueError("Paylocity board identity or visibility does not match registry")
    jobs = []
    seen = set()
    for row in data["Jobs"]:
        key = str(row["JobId"])
        if key in seen or not key.isdecimal():
            raise ValueError("Unexpected Paylocity requisition")
        seen.add(key)
        location = row.get("JobLocation") or {}
        address = ", ".join(filter(None, [location.get("City"), location.get("State")]))
        jobs.append(Job(row["JobTitle"], urljoin(board, f"/Recruiting/Jobs/Details/{key}"),
                        key, address or row.get("LocationName"), row.get("PublishedDate"),
                        row.get("Description")))
    return jobs


def phenom(employer):
    portal = employer["portal"]

    def page(offset):
        url = f"{portal}?{urlencode({'from': offset, 'keywords': '', 's': 1})}" if offset else portal
        data = embedded(fetch(url), "phApp.ddo")["eagerLoadRefineSearch"]
        if data["status"] not in (200, "success"):
            raise ValueError("Phenom inventory response was unsuccessful")
        jobs = []
        for row in data["data"]["jobs"]:
            job_id = row["jobId"]
            path = row.get("applyUrl", "")
            if not path.startswith("https://"):
                slug = re.sub(r"[^A-Za-z0-9]+", "-", row["title"]).strip("-")
                path = urljoin(portal, f"/us/en/job/{job_id}/{slug}")
            jobs.append(Job(row["title"], path.removesuffix("/apply"), job_id,
                            row.get("location"), row.get("postedDate"), row.get("descriptionTeaser")))
        return data["totalHits"], jobs

    return paginated(page, 10)


def greenhouse(employer):
    data = json.loads(fetch(employer["board"]))
    rows = data["jobs"]
    if data["meta"]["total"] != len(rows):
        raise ValueError("Greenhouse returned a partial inventory")
    return [Job(r["title"], r["absolute_url"], str(r["id"]), r["location"]["name"],
                description=r.get("content")) for r in rows]


def lever(employer):
    board = employer["board"]
    seen = {}
    for skip in range(0, 25_000, 100):
        rows = json.loads(fetch(f"{board}?mode=json&limit=100&skip={skip}"))
        if not isinstance(rows, list) or len(rows) > 100:
            raise ValueError("Invalid Lever page")
        for row in rows:
            job = Job(row["text"], row["hostedUrl"], row["id"], row["categories"].get("location"),
                      description=row.get("descriptionPlain"), compensation=row.get("salaryRange"))
            if job.requisition in seen:
                raise ValueError("Lever repeated a posting")
            seen[job.requisition] = job
        if len(rows) < 100:
            return list(seen.values())
    raise ValueError("Lever page limit reached")


def smartrecruiters(employer):
    board = employer["board"]
    company = urlsplit(board).path.split("/")[-2]

    def page(offset):
        query = urlencode({"limit": 100, "offset": offset})
        data = json.loads(fetch(f"{board}?{query}"))
        if data["offset"] != offset:
            raise ValueError("SmartRecruiters returned the wrong offset")
        jobs = []
        for row in data["content"]:
            if row["company"]["identifier"].casefold() != company.casefold():
                raise ValueError("SmartRecruiters board identity mismatch")
            slug = re.sub(r"[^A-Za-z0-9]+", "-", row["name"]).strip("-").lower()
            jobs.append(Job(row["name"], f"https://jobs.smartrecruiters.com/{company}/{row['id']}-{slug}",
                            row["id"], row["location"].get("fullLocation"), row.get("releasedDate")))
        return int(data["totalFound"]), jobs

    return paginated(page, 100)


def group_one(employer):
    portal = employer["board"]
    page = fetch(portal)
    total = re.search(r"<h1>\s*([\d,]+) Jobs Found\s*</h1>", page)
    if not total:
        raise ValueError("Group 1 board did not provide an inventory total")
    rows = re.findall(
        r'<td class="hidden-xs af-job-id">([^<]+)</td>\s*'
        r'<td class="af-job-title"><a href="([^"]+)">(.*?)</a></td>\s*'
        r'<td class="af-job-location">([^<]*)</td>', page, re.S)
    expected = int(total[1].replace(",", ""))
    ids = {row[0].strip() for row in rows}
    if len(rows) != expected or len(ids) != expected:
        raise ValueError("Group 1 inventory rows do not match the advertised total")
    jobs = []
    for requisition, href, title, location in rows:
        key = requisition.strip()
        source = urlsplit(urljoin(portal, unescape(href)))
        if (source.hostname != urlsplit(portal).hostname or source.path != "/job.html"
                or parse_qs(source.query).get("id") != [key]):
            raise ValueError("Group 1 job URL does not match its requisition")
        jobs.append(Job(unescape(ENTITY_MARKUP.sub("", title)).strip(),
                        urljoin(portal, f"/job.html?{urlencode({'id': key})}"),
                        key, unescape(location).strip() or None))
    return jobs


ADAPTERS = {"workday": workday, "paylocity": paylocity, "phenom": phenom,
            "greenhouse": greenhouse, "lever": lever, "smartrecruiters": smartrecruiters, "browser": browser, "group-one": group_one}


class PostingMeta(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "meta" and attrs.get("property", "").startswith("og:"):
            self.values[attrs["property"]] = attrs.get("content", "")


def posting_details(job):
    """Read source-provided JobPosting fields for shortlisted roles."""
    source = urlsplit(job.url)
    if source.hostname.endswith(".myworkdayjobs.com") and "/job/" in source.path:
        board, path = source.path.split("/job/", 1)
        tenant = source.hostname.split(".")[0]
        site = board.strip("/").split("/")[-1]
        data = json.loads(fetch(f"https://{source.netloc}/wday/cxs/{tenant}/{site}/job/{path}"))["jobPostingInfo"]
        if job.requisition and data.get("jobReqId") != job.requisition:
            return False
        job.published = data.get("startDate") or job.published
        if data.get("jobDescription"):
            job.description = ENTITY_MARKUP.sub(" ", data["jobDescription"])
        return bool(job.description)
    if source.hostname == "jobs.smartrecruiters.com":
        _, company, slug = source.path.split("/", 2)
        posting_id = slug.split("-", 1)[0]
        item = json.loads(fetch(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"))
        if item["id"] != job.requisition or item.get("active") is not True:
            return False
        job.url = item["postingUrl"]
        sections = item.get("jobAd", {}).get("sections", {})
        text = " ".join(section.get("text", "") for section in sections.values() if isinstance(section, dict))
        job.description = ENTITY_MARKUP.sub(" ", text).strip() or None
        return bool(job.description)
    html = fetch(job.url)
    for block in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                            html, re.I | re.S):
        try:
            payload = json.loads(block, strict=source.hostname != "www.group1careers.com")
        except ValueError:
            continue
        for item in payload if isinstance(payload, list) else payload.get("@graph", [payload]):
            if item.get("@type") != "JobPosting":
                continue
            identifier = item.get("identifier")
            if isinstance(identifier, dict) and identifier.get("value") and job.requisition:
                if str(identifier["value"]).casefold() != job.requisition.casefold():
                    continue
            if item.get("datePosted"):
                job.published = item["datePosted"]
            if item.get("description"):
                job.description = ENTITY_MARKUP.sub(" ", item["description"])
            if item.get("baseSalary"):
                job.compensation = item["baseSalary"]
            return True
    if source.hostname == "recruiting.paylocity.com":
        metadata = PostingMeta(html).values
        page = embedded(html, "window.pageData")
        if page.get("jobTitle") == job.title and metadata.get("og:description"):
            job.description = unescape(metadata["og:description"])
            return True
    return False
