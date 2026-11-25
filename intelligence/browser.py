"""Inventory reader for public JavaScript boards with an independently visible total."""
import re
from urllib.parse import urljoin, urlsplit

from .model import Job


def read(employer):
    from playwright.sync_api import sync_playwright
    from .adapters import public_allowed

    config = employer["browser"]
    portal = employer["board"] or employer["portal"]
    if not public_allowed(portal):
        raise PermissionError("Public robots policy disallows the browser inventory page")
    expected_host = urlsplit(portal).netloc
    jobs = []
    seen = set()
    with sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(15_000)

            def route_request(route):
                url = route.request.url
                if route.request.is_navigation_request():
                    if urlsplit(url).netloc != expected_host or not public_allowed(url):
                        route.abort()
                        return
                route.continue_()

            page.route("**/*", route_request)
            response = page.goto(portal, wait_until="domcontentloaded")
            if not response or not response.ok:
                raise ValueError("Browser inventory navigation failed")
            total_text = page.locator(config["total"]).inner_text()
            match = re.search(config["total_pattern"], total_text)
            if not match:
                raise ValueError("Browser inventory total could not be verified")
            expected = int(match.group(1).replace(",", ""))
            rows = page.locator(config["rows"])
            for _ in range(200):
                if expected:
                    rows.first.wait_for()
                for row in rows.all():
                    source = row.locator(config["link"])
                    href = source.get_attribute("href")
                    if not href:
                        raise ValueError("Browser posting missing source")
                    job = Job(source.inner_text().strip(), urljoin(page.url, href))
                    if job.normalized_url in seen:
                        raise ValueError("Browser paging repeated a posting")
                    jobs.append(job)
                    seen.add(job.normalized_url)
                if len(jobs) == expected:
                    return jobs
                if len(jobs) > expected:
                    raise ValueError("Browser inventory exceeded the displayed total")
                if rows.count() == 0:
                    raise ValueError("Browser returned no posting rows for a nonempty inventory")
                previous = rows.first.locator(config["link"]).get_attribute("href")
                page.locator(config["next"]).click()
                page.wait_for_function(
                    """([rows, link, previous]) => {
                        const item = document.querySelector(rows)?.querySelector(link);
                        return item && item.getAttribute('href') !== previous;
                    }""", arg=[config["rows"], config["link"], previous])
        finally:
            browser.close()
    raise ValueError("Browser inventory page limit was reached")
