"""CONTRACT_WIN: work a firm says on its own site that it won.

Read off the company's own homepage, the page a search found on its site,
or its news, projects or press page. Every business and place here is
fictional. Only the firm's own contract counts: not a prize, a certificate,
or the clients it helps win tenders. The date decides whether the writer
may call it new.
"""

import os
import tempfile
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio

from sellbird.collectors.discover import PRESETS, build_queries, is_junk, preset_problem
from sellbird.collectors.profile import ProfileCollector, contract_win
from sellbird.contract_wins import (contract_fact, contract_facts, contract_recency, contract_text,
                                    parse_contract)
from sellbird.models.company import Company
from sellbird.signals import SIGNAL_CATALOG, seed_signal_catalog
from sellbird.state import StateManager

RECENT = date.fromordinal(date.today().toordinal() - 60).strftime("%Y-%m")   # about 2 months ago


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(os.path.join(tmp, "c.db"))
        await sm.init_db()
        yield sm


# ── the detector ──


@pytest.mark.parametrize("html", [
    "<p>We have been awarded the contract for the new primary school in Northtown.</p>",
    "<p>Acme Construction has been appointed main contractor on the Riverside housing scheme.</p>",
    "<p>We're delighted to announce that we have won the contract to upgrade the Westbay water works.</p>",
    "<p>Acme has secured a €4.5m framework for road resurfacing.</p>",
    "<p>We have commenced works on site at the Hilltop medical centre.</p>",
    "<p>Work has commenced on site at the new community hall in Eastfield.</p>",
    "<p>Acme Group Ltd has been selected as preferred contractor for the harbour extension.</p>",
])
def test_a_win_the_firm_announces_is_found(html):
    found = contract_win(html, domain="acme.example")
    assert found is not None
    assert found["summary"] and "<" not in found["summary"]


@pytest.mark.parametrize("html", [
    "<p>We have been awarded Contractor of the Year 2025.</p>",
    "<p>We were awarded ISO 9001 certification.</p>",
    "<p>We have been shortlisted for the regional construction awards.</p>",
    "<p>We help our clients win tenders and contracts across the region.</p>",
    "<p>We have won new clients this year.</p>",
    "<p>Our team has been awarded a gold rating for safety.</p>",
    "<p>We have been awarded planning permission for our new yard.</p>",
    "<p>Contact us to discuss your next contract.</p>",
    "<p>Acme helps contractors who have been awarded contracts to plan their works.</p>",
])
def test_prizes_certificates_and_other_peoples_wins_are_not_contracts(html):
    assert contract_win(html, domain="acme.example") is None


def test_value_and_date_come_from_the_sentence():
    html = "<p>In March 2026, we were awarded a €12m contract for the Southport bypass.</p>"
    found = contract_win(html, domain="acme.example")
    assert found["amount"] == "€12M"
    assert found["date"] == "2026-03"


def test_a_news_post_is_dated_by_its_publish_date():
    html = ('<meta property="article:published_time" content="2026-04-10T09:00:00Z">'
            "<h1>New school contract</h1><p>We have been awarded the contract for the new "
            "school in Northtown.</p>")
    found = contract_win(html, domain="acme.example", url="https://acme.example/news/new-school")
    assert found["date"] == "2026-04-10"


def test_a_homepage_date_is_not_the_wins():
    html = ('<meta property="article:published_time" content="2026-04-10T09:00:00Z">'
            "<p>We have been awarded the contract for the new school in Northtown.</p>")
    assert contract_win(html, domain="acme.example", url="https://acme.example/")["date"] == ""


def test_the_newest_win_on_a_page_is_kept():
    html = ("<p>In January 2024, we were awarded the contract for the Northtown library.</p>"
            "<p>In February 2026, we were awarded the contract for the Eastfield school.</p>")
    assert "Eastfield" in contract_win(html, domain="acme.example")["summary"]


def test_a_future_date_is_not_when_it_was_won():
    html = "<p>We have been awarded the contract for the depot, due to finish in 2099.</p>"
    assert contract_win(html, domain="acme.example")["date"] == ""


def test_a_long_sentence_is_clipped():
    html = "<p>We have been awarded the contract for the " + "very " * 80 + "big school.</p>"
    assert len(contract_win(html, domain="acme.example")["summary"]) <= 201


# ── storing and reading back ──


def test_the_stored_value_reads_back():
    found = {"summary": "We have been awarded the contract · for a school.", "amount": "€4M", "date": "2026-03"}
    text = contract_text(found)
    assert text.count("·") == 2
    assert parse_contract(text) == {"summary": "We have been awarded the contract , for a school.",
                                    "amount": "€4M", "date": "2026-03"}


def test_recency():
    today = date(2026, 11, 1)
    assert contract_recency("2026-06", today) == "recent"
    assert contract_recency("2024-06", today) == "old"
    assert contract_recency("", today) == "unknown"
    assert contract_recency("2025", today) == "old"     # 1 January 2025: never newer than stated


def test_the_writer_only_calls_a_recent_win_new():
    today = date(2026, 11, 1)
    win = {"summary": "We were awarded the school contract.", "amount": "", "date": "2026-09"}
    assert "month(s) ago" in contract_fact(win, today)
    old = dict(win, date="2023-02")
    assert "don't write as if they just won it" in contract_fact(old, today)
    undated = dict(win, date="")
    assert "don't call it recent" in contract_fact(undated, today)


# ── the catalog, the collector and the writer's facts ──


def test_the_signal_is_in_the_catalog_and_proposed():
    [sig] = [s for s in SIGNAL_CATALOG if s["code"] == "CONTRACT_WIN"]
    assert sig["collector"] == "profile" and sig["cost_note"].startswith("free")


def _collector(state, home, pages=None):
    c = ProfileCollector.__new__(ProfileCollector)
    c.site_names, c.state = {}, state
    fetched = []

    async def resolve(self, domain):
        return f"https://{domain}", home
    c._resolve_site = resolve.__get__(c)

    async def get(url):
        fetched.append(url)
        return (pages or {}).get(url, "")
    c._get = get
    return c, fetched


_PAD = "<p>Civil engineering and building contractors since 1990.</p>" * 12


@pytest.mark.asyncio
async def test_profiling_reads_the_news_page_and_the_writer_gets_the_fact(state):
    await seed_signal_catalog(state)
    await state.set_signal_status("CONTRACT_WIN", "confirmed")
    news = (f"<h1>Latest news</h1><p>In {date.today().year} we were awarded the contract for the "
            "Northtown school extension.</p>" + _PAD)
    c, fetched = _collector(state, "<h1>Acme</h1><p>Builders.</p>" + _PAD,
                            pages={"https://acme.example/news": news})
    c.confirmed = await state.confirmed_signal_codes()
    cid = await state.add_company(Company(name="Acme", domain="acme.example"))
    [obs] = [o for o in await c.profile_company(cid, "acme.example") if o["signal_code"] == "CONTRACT_WIN"]
    assert obs["value_num"] == 1 and "Northtown school extension" in obs["value_text"]
    assert obs["evidence_url"] == "https://acme.example/news"
    await state.add_observations([obs])
    assert "Northtown" in (await state.contract_win(cid))["summary"]
    [fact] = await contract_facts(state, cid)
    assert fact.startswith("- Contract win, as their own site says")


@pytest.mark.asyncio
async def test_no_win_is_recorded_as_none_and_gives_no_fact(state):
    await seed_signal_catalog(state)
    await state.set_signal_status("CONTRACT_WIN", "confirmed")
    c, _ = _collector(state, "<h1>Plain</h1><p>Family-run since 1987.</p>")
    c.confirmed = await state.confirmed_signal_codes()
    cid = await state.add_company(Company(name="Plain", domain="plain.example"))
    [obs] = [o for o in await c.profile_company(cid, "plain.example") if o["signal_code"] == "CONTRACT_WIN"]
    assert obs["value_num"] == 0
    await state.add_observations([obs])
    assert await state.contract_win(cid) is None
    assert await contract_facts(state, cid) == []


@pytest.mark.asyncio
async def test_not_collected_until_confirmed(state):
    await seed_signal_catalog(state)
    c, fetched = _collector(state, "<p>We have been awarded the contract for the depot.</p>" + _PAD)
    c.confirmed = await state.confirmed_signal_codes()
    cid = await state.add_company(Company(name="Acme", domain="acme.example"))
    assert not [o for o in await c.profile_company(cid, "acme.example") if o["signal_code"] == "CONTRACT_WIN"]
    assert not [u for u in fetched if u.endswith(("/news", "/projects"))]


# ── the discovery preset ──


def _config():
    return SimpleNamespace(
        icp=SimpleNamespace(industries=["civil engineering contractor"], geography=["Northtown", "Eastfield"],
                            geo_coordinates={}, hiring_signals=[], titles=["Director"]),
        product=SimpleNamespace(name="P"),
    )


def test_the_contracts_preset_searches_every_place_in_the_last_year():
    assert preset_problem("contracts", "serper", _config()) is None
    queries = build_queries(_config(), preset="contracts")
    assert {q.location for q in queries} == {"Northtown", "Eastfield"}
    since = date.fromordinal(date.today().toordinal() - 365).isoformat()
    for q in queries:
        assert f"after:{since}" in q.term and "awarded the contract" in q.term
        assert len(q.keyword().split()) <= 32     # Google reads the first 32 words
    assert PRESETS["contracts"]["local"] is True


def test_the_contracts_preset_needs_a_search_provider():
    assert preset_problem("contracts", "osm", _config())


@pytest.mark.parametrize("domain", ["etenders.gov.ie", "www.constructionnews.co.uk", "tenders.example.gov"])
def test_tender_portals_and_trade_press_are_junk(domain):
    assert is_junk(domain)
