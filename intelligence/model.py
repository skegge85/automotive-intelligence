from dataclasses import dataclass
import re
from urllib.parse import urlsplit, urlunsplit

LEADERSHIP = re.compile(
    r"\b(?:CEO|COO|SVP|RVP|EVP|VP|president|vice president|chief (?:executive|operating) officer"
    r"|(?:market(?: area)?|area|regional|district|division(?:al)?|operations|sales|executive) director"
    r"|director of (?:[\w-]+ )?(?:operations|sales)|(?:market(?: area)?|district|regional) manager"
    r"|dealer principal|(?:managing|operating) partner)\b", re.I)
SCOPE = re.compile(
    r"\b(?:multi[\s-]*(?:store|site|location|rooftop|dealership)|multiple (?:stores|dealerships|rooftops|locations)"
    r"|P\s*&\s*L|profit and loss|acquisition integration|succession planning|(?:dealership|business|equity) ownership|phantom equity)\b", re.I)
def annual_salary_floor(compensation):
    if not isinstance(compensation, dict) or compensation.get("currency") != "USD":
        return None
    value = compensation.get("value")
    if not isinstance(value, dict) or value.get("unitText") not in ("YEAR", "ANNUAL"):
        return None
    floor = value.get("minValue", value.get("value"))
    return floor if type(floor) in (int, float) else None


MANAGEMENT = re.compile(r"\b(?:manager|director|head|leader|GM|GSM|partner)\b", re.I)


@dataclass
class Job:
    title: str
    url: str
    requisition: str | None = None
    location: str | None = None
    published: str | None = None
    description: str | None = None
    compensation: dict | str | None = None
    bonus: str | None = None
    equity: str | None = None
    relocation: str | None = None
    travel: str | None = None

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if not self.title.strip() or parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Posting is missing a title or a valid source URL")

    @property
    def normalized_url(self):
        parsed = urlsplit(self.url)
        return urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))

    @property
    def eligible(self):
        text = f"{self.title} {self.description or ''}"
        return bool(LEADERSHIP.search(self.title) or
                    (MANAGEMENT.search(self.title) and SCOPE.search(text)))

    @property
    def scope(self):
        return sorted(set(word.lower() for word in SCOPE.findall(
            f"{self.title} {self.description or ''}")))

    @property
    def score(self):
        score = 35 if re.search(r"\b(?:president|ceo|coo|chief executive officer|chief operating officer)\b",
                                self.title, re.I) else 15
        score += min(30, 10 * len(self.scope))
        score += 15 * bool(re.search(r"\b(?:Pennsylvania|PA)\b", self.location or ""))
        score += 15 * bool(self.equity)
        floor = annual_salary_floor(self.compensation)
        score += 20 * (floor is not None and floor >= 600_000)
        return score


def resolve_observations(observations):
    direct = sorted((item for item in observations if item["direct"]),
                    key=lambda item: item.get("date", ""), reverse=True)
    if not direct:
        return "unverified"
    latest_date = direct[0].get("date")
    statuses = {item["status"] for item in direct if item.get("date") == latest_date}
    return statuses.pop() if len(statuses) == 1 else "unverified"
