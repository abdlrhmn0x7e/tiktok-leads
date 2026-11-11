import asyncio

from tiktok_leads import daemon, yahoo_discovery
from tiktok_leads.daemon import WorkItem
from tiktok_leads.db import LeadRepository
from tiktok_leads.settings import Settings

QUERY = '"gmail.com" "gym"'


def result_page(*handles: str) -> str:
    return "".join(
        f'<div class="dd algo"><a href="https://www.tiktok.com/@{handle}">x</a> 25K Followers {handle}@gmail.com</div>'
        for handle in handles
    )


def run_yahoo_item(monkeypatch, tmp_path, pages):
    """Run one Yahoo work item against canned result pages; a page that is an Exception fails its fetch."""
    repository = LeadRepository(tmp_path / "leads.sqlite")
    repository.initialize()
    fetched, checked = [], []

    def fetch_page(query, page):
        fetched.append(page)
        if isinstance(pages[page], Exception):
            raise pages[page]
        return pages[page]

    async def scrape_handles(source, repository, notifier, *, handles, **_):
        checked.extend(handles)
        return 0

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(yahoo_discovery, "fetch_page", fetch_page)
    monkeypatch.setattr(daemon, "scrape_handles", scrape_handles)
    monkeypatch.setattr(daemon.asyncio, "sleep", no_sleep)
    batch = [WorkItem("yahoo", "fitness", QUERY)]
    asyncio.run(daemon._run_batch(None, Settings(_env_file=None), repository, None, batch, 1))
    done = yahoo_discovery.is_done(repository, QUERY, max_age_days=14)
    return fetched, checked, done


def test_yahoo_item_reads_every_page_until_one_is_empty(monkeypatch, tmp_path):
    pages = [result_page("gym.amy"), result_page("gym.amy", "lift.bo"), ""]
    assert run_yahoo_item(monkeypatch, tmp_path, pages) == ([0, 1, 2], ["gym.amy", "lift.bo"], True)


def test_failed_yahoo_fetch_stops_the_item_and_keeps_the_page_for_later(monkeypatch, tmp_path):
    pages = [result_page("gym.amy"), RuntimeError("Yahoo returned HTTP 500")]
    assert run_yahoo_item(monkeypatch, tmp_path, pages) == ([0, 1], ["gym.amy"], False)
