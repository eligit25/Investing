import json
from datetime import date
from pathlib import Path

import pytest

from hps.adapters.capture import heuristic_offers
from hps.adapters.reztrip import parse_available_rooms
from hps.adapters.synxis import parse_product_availability
from hps.config import Hotel, RoomRule, load_config
from hps.dates import build_schedule
from hps.models import NO_AVAILABILITY, OK, Offer
from hps.rooms import standard_prices
from hps.tools import url_to_template

FIX = Path(__file__).parent / "fixtures"
POLICY = load_config().settings["rate_policy"]


def _settings(**over):
    s = {"stay": {"nights": 1, "adults": 2, "currency": "INR"},
         "horizons": [{"label": "1w", "days": 7}, {"label": "1m", "days": 30},
                      {"label": "3m", "days": 90}, {"label": "6m", "days": 180, "every_n_days": 2}],
         "checkin_mode": "dow_anchored", "anchor_days": ["tue", "sat"]}
    s.update(over)
    return s


def test_dow_anchored_schedule():
    today = date(2026, 10, 30)  # a Friday
    stays = build_schedule(today, _settings())
    by_label = {s.horizon_label: s.checkin for s in stays}
    assert by_label["1w_tue"] == date(2026, 11, 10) and by_label["1w_sat"] == date(2026, 11, 7)
    assert all(s.checkin >= today for s in stays)
    assert {s.checkin.weekday() for s in stays} == {1, 5}
    # 6m is configured every_n_days=2, so it only appears on days whose ordinal is even.
    assert ("6m_tue" in by_label) == (today.toordinal() % 2 == 0)


def test_fixed_offset_schedule():
    stays = build_schedule(date(2026, 10, 30), _settings(checkin_mode="fixed_offset"))
    assert [s.checkin for s in stays][:3] == [date(2026, 11, 6), date(2026, 11, 29), date(2027, 1, 28)]
    assert all(s.anchor is None for s in stays)


def test_synxis_parse_itc():
    res = parse_product_availability(json.loads((FIX / "synxis_itc_maratha.json").read_text()))
    assert res.status == OK and len(res.offers) == 23
    bar = next(o for o in res.offers if o.room_code == "TWDL" and o.rate_code == "RDAILYB")
    assert bar.price_pre_tax == 42000 and bar.price_incl_tax > bar.price_pre_tax
    assert bar.is_member is False and bar.breakfast_included is True and bar.rooms_left == 44
    member = next(o for o in res.offers if o.rate_code == "RDAILYBM")
    assert member.is_member is True


def test_synxis_empty_is_no_availability():
    res = parse_product_availability({"ContentLists": {}, "ProductAvailabilityDetail": {
        "Prices": [], "LeastRestrictiveFailure": {"Level": "RoomControl", "ProductStatus": "Closed"}}})
    assert res.status == NO_AVAILABILITY and "Closed" in res.error


def test_reztrip_parse_lemontree():
    res = parse_available_rooms(json.loads((FIX / "reztrip_aurika_mumbai.json").read_text()))
    assert res.status == OK and len(res.offers) == 6
    o = res.offers[0]
    assert o.room_code == "DKPV" and o.price_pre_tax == 17980 and o.price_list == 29000
    assert o.is_refundable is True


def _hotel(rooms=None):
    return Hotel(id="h", name="H", micromarket="m", company="c", source="synxis", params={}, rooms=rooms or {})


def test_standard_prices_configured_rooms_exclude_member_and_packages():
    res = parse_product_availability(json.loads((FIX / "synxis_itc_maratha.json").read_text()))
    hotel = _hotel({"A": RoomRule(codes=["TWDL"]), "B": RoomRule(codes=["SEXE"])})
    std = {s.room_std: s for s in standard_prices(hotel, res.offers, POLICY)}
    assert std["A"].offer.rate_code == "RDAILYB" and std["A"].method == "config"   # not the member rate
    # SEXE has a cheaper "Beyond the Suite" package (46,800) that must be ignored.
    assert std["B"].offer.price_pre_tax == 52000
    # LOWEST ignores the run-of-house room.
    assert std["LOWEST"].offer.room_code != "RUNH"


def test_standard_prices_auto_and_sold_out():
    offers = [Offer("X", "Deluxe", "BAR", "Best rate", 100.0, 118.0),
              Offer("Y", "Premier", "BAR", "Best rate", 150.0, 177.0)]
    std = {s.room_std: s for s in standard_prices(_hotel(), offers, POLICY)}
    assert std["A"].offer.room_code == "X" and std["B"].offer.room_code == "Y" and std["A"].method == "auto"
    hotel = _hotel({"A": RoomRule(names=["^Deluxe"]), "B": RoomRule(names=["^Club"])})
    std = {s.room_std: s for s in standard_prices(hotel, offers, POLICY)}
    assert std["B"].offer is None  # configured room not on sale -> recorded as unavailable


def test_heuristic_parser_finds_room_prices():
    payload = {"data": {"rooms": [
        {"roomName": "Superior King", "rates": [{"ratePlan": "Flexible", "price": {"amountBeforeTax": 12000, "amountAfterTax": 14160, "taxAmount": 2160}}]},
        {"roomName": "Club Room", "rates": [{"price": {"amountBeforeTax": 15000, "amountAfterTax": 17700}}]},
    ]}}
    offers = heuristic_offers([payload])
    by_name = {o.room_name: o for o in offers}
    assert by_name["Superior King"].price_pre_tax == 12000 and by_name["Superior King"].price_incl_tax == 14160
    assert by_name["Club Room"].price_pre_tax == 15000


@pytest.mark.parametrize("url", [
    "https://x.com/book?from=12/01/2026&to=12/02/2026&adults=2",
    "https://x.com/book?from=12%2F01%2F2026&to=12%2F02%2F2026",
    "https://x.com/rooms/abc?checkinDate=2026-12-01&checkoutDate=2026-12-02",
])
def test_url_to_template_roundtrip(url):
    tpl = url_to_template(url, date(2026, 12, 1), date(2026, 12, 2))
    assert "{checkin" in tpl and "{checkout" in tpl
    assert tpl.format(checkin=date(2026, 12, 1), checkout=date(2026, 12, 2)) == url
