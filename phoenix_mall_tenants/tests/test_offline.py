"""Offline tests: parsers, CDX paging, history logic. No network access needed.

Run: python -m pytest -q tests
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from phoenix_tenants import wayback
from phoenix_tenants.history import changes, reconcile_names, same_brand
from phoenix_tenants.malls import MALLS
from phoenix_tenants.normalize import logo_upload_date, map_category, name_key
from phoenix_tenants.platforms import _loads, _paginate

FIX = Path(__file__).parent / "fixtures"


# ── normalize ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,key", [
    ("H & M", "hm"), ("Levi's", "levis"), ("adidas-indore", "adidas"), ("montblanc-2", "montblanc"),
    ("The Body Shop", "body shop"), ("U.S. Polo Assn.", "us polo assn"), ("Zara ", "zara"),
])
def test_name_key(raw, key):
    assert name_key(raw) == key


@pytest.mark.parametrize("raw,name,ttype,cat", [
    ("Bags & Luggage | Foot Fashion, Bags, Luggage & Accessories", "Accessorize", "", "Accessories"),
    ("Sportswear", "Adidas", "", "Sports & Athleisure"),
    ("Womens Fashion", "Aarkee", "", "Apparel"),
    ("", "Starbucks", "dine", "Food & Beverage"),
    ("Watches", "ETHOS SUMMIT", "", "Watches & Jewellery"),
    ("", "Smart Bazaar", "", "Unknown"),
])
def test_map_category(raw, name, ttype, cat):
    assert map_category(raw, name, ttype) == cat


def test_logo_upload_date():
    assert logo_upload_date("17042024181124310_brlo.png") == "2024-04-17"
    assert logo_upload_date("logo.png") == ""
    assert logo_upload_date("99999999999999_x.png") == ""


def test_loads_double_encoded():
    inner = {"brand": [{"id": 1}]}
    assert _loads(json.dumps(json.dumps(inner))) == inner
    assert _loads(json.dumps({"d": json.dumps({"Brands": []})})) == {"Brands": []}
    assert _loads("") is None


def test_paginate_offset_and_return_all_behaviours():
    data = [{"id": i} for i in range(25)]
    paged = lambda n: data[n:n + 10]             # true offset paging
    everything = lambda n: data                  # ignores offset
    once = lambda n: data if n == 0 else []      # all at 0, nothing after
    for fn in (paged, everything, once):
        assert len(_paginate(fn, lambda x: str(x["id"]))) == 25


# ── archived listing parsing ─────────────────────────────────────────────────────────

def _cap(url, ts="20190718000000", mime="text/html"):
    return wayback.Capture(ts, url, mime, "200", "D")


def test_generic_link_extraction_absolute_relative_and_wayback_rewritten():
    html = """<html><body>
      <a href="https://www.phoenixmarketcity.com/bangalore/brand/Zara/101"><img alt="Zara"></a>
      <a href="/bangalore/brand/H-M/102">H &amp; M</a>
      <a href="/web/20190718000000/https://www.phoenixmarketcity.com/bangalore/brand/Nike/103">View Store</a>
      <a href="/bangalore/dine/Social">SOCIAL</a>
      <a href="/bangalore/brand/Zara/101">Zara</a>
      <a href="/bangalore/events">Events</a>
    </body></html>"""
    rows = wayback.parse_archived(html.encode(), _cap("https://www.phoenixmarketcity.com/bangalore/brands"),
                                  MALLS["pmc_bangalore"])
    got = {(r["tenant_type"], r["name"]) for r in rows}
    assert got == {("retail", "Zara"), ("retail", "H & M"), ("retail", "Nike"), ("dine", "SOCIAL")}
    assert all(r["as_of"] == "2019-07-18" and r["source"] == "wayback:listing" for r in rows)


def test_js_shell_yields_nothing():
    shell = b"<html><body><div id='appendBrands'></div><script src='/js/Brands.js'></script></body></html>"
    assert wayback.parse_archived(shell, _cap("phoenixmarketcity.com/bangalore/brands"),
                                  MALLS["pmc_bangalore"]) == []


def test_wordpress_fixture_including_wayback_rewritten_links():
    html = (FIX / "palladium_chennai_shop_by_store.html").read_text()
    m = MALLS["palladium_chennai"]
    live = wayback.parse_archived(html.encode(), _cap("https://palladiumchennai.com/shop-by-store/"), m)
    assert len(live) >= 5 and {"tira", "coach"} & {r["source_id"] for r in live}
    rewritten = html.replace('href="https://palladiumchennai.com/',
                             'href="/web/20211127000000/https://palladiumchennai.com/')
    arch = wayback.parse_archived(rewritten.encode(), _cap("https://palladiumchennai.com/shop-by-store/"), m)
    assert {r["source_id"] for r in arch} == {r["source_id"] for r in live}


def test_moa_json_feed():
    feed = json.dumps([{"id": 1, "title": "Accessorize", "category": "Fashion Accessiores", "floor": "First Floor"},
                       {"id": 2, "title": "Zara", "category": "Fashion", "floor": "Ground Floor"}]).encode()
    rows = wayback.parse_archived(feed, _cap("https://admin.phoenixmallofasia.com/api/brand", mime="application/json"),
                                  MALLS["mall_of_asia_bangalore"])
    assert [r["name"] for r in rows] == ["Accessorize", "Zara"]
    assert rows[0]["category"] == "Accessories"


# ── CDX ──────────────────────────────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, payload, status=200):
        self.text = json.dumps(payload)
        self.status_code = status
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        pass


class FakeFetcher:
    """Serves canned CDX pages keyed by (url, resumeKey) and snapshot bodies by URL."""

    def __init__(self, cdx_pages=None, bodies=None):
        self.cdx_pages, self.bodies, self.calls = cdx_pages or {}, bodies or {}, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        if url == wayback.CDX:
            p = dict(params)
            return FakeResp(self.cdx_pages[(p["url"], p.get("resumeKey"))])
        body = self.bodies[url]
        r = FakeResp(None)
        r.content = body
        return r


def test_cdx_query_follows_resume_key():
    fl = ["timestamp", "original", "statuscode"]
    pages = {
        ("x.com/brand/", None): [fl, ["20200101000000", "http://x.com/brand/a", "200"], [], ["KEY1"]],
        ("x.com/brand/", "KEY1"): [fl, ["20210101000000", "http://x.com/brand/b", "404"]],
    }
    rows = wayback.cdx_query(FakeFetcher(pages), "x.com/brand/", match="prefix", fields=fl)
    assert [r["original"] for r in rows] == ["http://x.com/brand/a", "http://x.com/brand/b"]


def test_pick_periodic_takes_latest_ok_capture_per_quarter():
    rows = [{"timestamp": t, "original": "u", "statuscode": s, "digest": t}
            for t, s in [("20200105000000", "200"), ("20200320000000", "200"), ("20200325000000", "404"),
                         ("20200801000000", "200")]]
    caps = wayback.pick_periodic(rows, "Q")
    assert [c.timestamp for c in caps] == ["20200320000000", "20200801000000"]


def test_detail_inventory_first_last_seen_and_exit_flag():
    fl = ("timestamp", "original", "statuscode", "mimetype")
    pre_b, pre_d = MALLS["pmc_bangalore"].detail_prefixes
    pages = {
        (pre_b, None): [list(fl),
                        ["20190101000000", "https://www.phoenixmarketcity.com/bangalore/brand/Zara/1", "200", "text/html"],
                        ["20230601000000", "https://www.phoenixmarketcity.com/bangalore/brand/Zara/1", "200", "text/html"],
                        ["20200101000000", "https://www.phoenixmarketcity.com/bangalore/brand/Gap/2", "200", "text/html"],
                        ["20220101000000", "https://www.phoenixmarketcity.com/bangalore/brand/Gap/2", "404", "text/html"],
                        ["20210101000000", "https://www.phoenixmarketcity.com/bangalore/brand/logo.png", "200", "text/html"]],
        (pre_d, None): [list(fl),
                        ["20210301000000", "http://phoenixmarketcity.com/bangalore/dine/Social", "200", "text/html"]],
    }
    rows = {r["name_key"]: r for r in wayback.detail_inventory(FakeFetcher(pages), MALLS["pmc_bangalore"],
                                                               log=lambda *_: None)}
    assert set(rows) == {"zara", "gap", "social"}
    assert (rows["zara"]["first_seen"], rows["zara"]["last_seen"]) == ("2019-01-01", "2023-06-01")
    assert rows["gap"]["likely_exited"] and not rows["zara"]["likely_exited"]
    assert rows["social"]["tenant_type"] == "dine"


def test_listing_history_reuses_parse_for_identical_digests(tmp_path):
    url = "phoenixmarketcity.com/bangalore/brands"
    fl = list(wayback.CDX_FIELDS)
    row = lambda ts: ["k", ts, "https://www.phoenixmarketcity.com/bangalore/brands", "text/html", "200", "SAME", "1"]
    pages = {(u, None): [fl] for u in MALLS["pmc_bangalore"].wayback_listings}
    pages[(url, None)] = [fl, row("20190301000000"), row("20190901000000")]
    html = b'<a href="/bangalore/brand/Zara/1">Zara</a>'
    bodies = {f"{wayback.WB}/20190301000000id_/https://www.phoenixmarketcity.com/bangalore/brands": html}
    f = FakeFetcher(pages, bodies)
    rows, log = wayback.listing_history(f, MALLS["pmc_bangalore"], tmp_path, log=lambda *_: None)
    assert [r["as_of"] for r in rows] == ["2019-03-01", "2019-09-01"]
    assert [x["status"] for x in log] == ["ok", "ok:unchanged"]
    assert sum(1 for u, _ in f.calls if u != wayback.CDX) == 1   # second period served from digest


# ── history ──────────────────────────────────────────────────────────────────────────

def test_same_brand_guards_whole_word_differences():
    assert same_brand("van huesen", "van heusen")
    assert same_brand("lakeview milk bar", "lakeview milkbar")
    assert not same_brand("van heusen men", "van heusen women")
    assert not same_brand("adidas", "adidas originals")


def _obs(rows):
    return pd.DataFrame([{"mall_id": "m", "mall_name": "M", "as_of": d, "tenant_type": t, "name": n,
                          "name_key": name_key(n), "category": "Apparel", "source": "s"} for d, t, n in rows])


def test_changes_open_close_and_confirm():
    df = _obs([("2020-01-01", "retail", "Zara"), ("2020-01-01", "retail", "Gap"),
               ("2020-04-01", "retail", "Zara"),                                   # Gap missing once
               ("2020-07-01", "retail", "Zara"), ("2020-07-01", "retail", "Gap"),   # ...and back
               ("2020-07-01", "retail", "Uniqlo")])
    ev1, _ = changes(df, confirm=1)
    assert set(zip(ev1["as_of"], ev1["event"], ev1["name"])) == {
        ("2020-04-01", "close", "Gap"), ("2020-07-01", "open", "Gap"), ("2020-07-01", "open", "Uniqlo")}
    ev2, sm = changes(df, confirm=2)
    assert ("close", "Gap") not in set(zip(ev2["event"], ev2["name"]))   # flicker suppressed
    assert list(sm["n_tenants"]) == [2, 1, 3]


def test_changes_ignore_tenant_types_missing_from_one_snapshot():
    df = _obs([("2020-01-01", "retail", "Zara"), ("2020-01-01", "dine", "Social"),
               ("2020-04-01", "retail", "Zara")])                       # source had no dining list
    ev, _ = changes(df)
    assert ev.empty


def test_reconcile_names_merges_spelling_variants_only():
    df = _obs([("2020-01-01", "retail", "Van Heusen"), ("2020-01-01", "retail", "Van Heusen Women"),
               ("2020-04-01", "retail", "Van Huesen"), ("2020-04-01", "retail", "Van Heusen Men")])
    out = reconcile_names(df)
    assert set(out["name_key"]) == {"van heusen", "van heusen women", "van heusen men"}


# ── regressions: archive URL quirks, detail panel, family separation ────────────────

def test_norm_url_strips_port_scheme_www_query_and_decodes():
    assert wayback._norm_url("http://www.phoenixmarketcity.com:80/bangalore/brand/Zara/1?x=1#y") == \
        "phoenixmarketcity.com/bangalore/brand/Zara/1"
    assert wayback._norm_url("https://www.phoenixmallofasia.com/brand/Michael%20Kors") == \
        "phoenixmallofasia.com/brand/Michael Kors"


def test_detail_inventory_handles_port_80_and_encoded_slugs():
    fl = ("timestamp", "original", "statuscode", "mimetype")
    m = MALLS["mall_of_asia_bangalore"]
    pages = {(p, None): [list(fl)] for p in m.detail_prefixes}
    pages[(m.detail_prefixes[0], None)] += [
        ["20240101000000", "http://www.phoenixmallofasia.com:80/brand/Michael%20Kors", "200", "text/html"]]
    rows = wayback.detail_inventory(FakeFetcher(pages), m, log=lambda *_: None)
    assert [(r["name"], r["name_key"]) for r in rows] == [("Michael Kors", "michael kors")]


def test_details_to_panel_extends_recent_tenants_to_horizon():
    from phoenix_tenants.history import details_to_panel
    det = pd.DataFrame([
        {"mall_id": "m", "mall_name": "M", "tenant_type": "retail", "name": "Zara", "name_key": "zara",
         "category": "Apparel", "first_seen": "2020-02-01", "last_seen": "2021-11-01",
         "last_capture": "2021-11-01", "likely_exited": "False"},
        {"mall_id": "m", "mall_name": "M", "tenant_type": "retail", "name": "Gap", "name_key": "gap",
         "category": "Apparel", "first_seen": "2020-02-01", "last_seen": "2020-05-01",
         "last_capture": "2022-03-01", "likely_exited": "True"},
        {"mall_id": "m", "mall_name": "M", "tenant_type": "retail", "name": "Uniqlo", "name_key": "uniqlo",
         "category": "Apparel", "first_seen": "2021-07-01", "last_seen": "2021-08-01",
         "last_capture": "2021-08-01", "likely_exited": "False"},
    ])
    p = details_to_panel(det)
    q = lambda k: sorted(p[p.name_key == k]["as_of"])
    assert q("gap") == ["2020-03-31", "2020-06-30"]                     # exited: stops at last 200
    assert q("zara")[-1] == "2022-03-31"                                # recent: extended to horizon
    assert q("uniqlo")[0] == "2021-09-30" and q("uniqlo")[-1] == "2022-03-31"


def test_listing_and_detail_families_are_never_compared():
    df = pd.concat([
        _obs([("2020-03-31", "retail", "Zara"), ("2020-03-31", "retail", "Gap")]).assign(family="listing"),
        _obs([("2020-06-30", "retail", "Zara")]).assign(family="details"),
    ])
    ev, sm = changes(df)
    assert ev.empty and set(sm["family"]) == {"listing", "details"}
