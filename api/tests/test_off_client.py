"""Open Food Facts client.

No network here: respx intercepts httpx. The point of these tests is that every failure
mode OFF can produce turns into `None` rather than an exception, because the scanner must
degrade to manual naming and never to an error screen.
"""

import httpx
import pytest
import respx
from factories import off_response

from app.config import settings
from app.services.barcode.off_client import (
    fetch_product,
    is_store_internal,
    normalize_barcode,
)

URL = f"{settings.off_base_url}/api/v2/product/8076809513692.json"


def test_normalize_strips_everything_that_is_not_a_digit():
    # Scanners emit trailing newlines; a barcode with a stray space would miss the cache
    # forever and re-fetch on every single scan.
    assert normalize_barcode(" 8076809513692\n") == "8076809513692"
    assert normalize_barcode("807-680.951 3692") == "8076809513692"
    assert normalize_barcode("") == ""
    assert normalize_barcode("abc") == ""


@pytest.mark.parametrize(
    "barcode,expected",
    [
        ("2100123456789", True),  # store-weighed produce label
        ("2000000000000", True),
        ("8076809513692", False),  # ordinary EAN-13
        ("80052760", False),  # EAN-8 starting with 8
        ("21001234", False),  # EAN-8: the 2-prefix rule is EAN-13 only
    ],
)
def test_store_internal_detection(barcode, expected):
    assert is_store_internal(barcode) is expected


@respx.mock
def test_parses_a_found_product():
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_response()))
    off = fetch_product("8076809513692")
    assert off is not None
    assert off.name == "Napoletana"
    assert off.brand == "Barilla"
    assert off.quantity_value == 400.0
    assert off.quantity_unit == "g"
    assert "en:tomato-sauces" in off.categories
    # The verbatim payload is the record of what we matched on — OFF is community-edited
    # and the remote row can change afterwards.
    assert off.payload["product_name"] == "Napoletana"


@respx.mock
def test_only_the_first_brand_is_kept():
    respx.get(URL).mock(
        return_value=httpx.Response(200, json=off_response(brand="Barilla,Mulino Bianco"))
    )
    assert fetch_product("8076809513692").brand == "Barilla"


@respx.mock
def test_unknown_product_is_none_not_an_error():
    """OFF answers 200 with status 0 for an unknown barcode — the commonest outcome for
    small Italian brands, and it must route to manual naming."""
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(found=False)))
    assert fetch_product("8076809513692") is None


@respx.mock
def test_network_failure_is_none_not_an_exception():
    respx.get(URL).mock(side_effect=httpx.ConnectError("offline"))
    assert fetch_product("8076809513692") is None


@respx.mock
def test_server_error_is_none():
    respx.get(URL).mock(return_value=httpx.Response(503, text="down"))
    assert fetch_product("8076809513692") is None


@respx.mock
def test_non_json_body_is_none():
    respx.get(URL).mock(return_value=httpx.Response(200, text="<html>rate limited</html>"))
    assert fetch_product("8076809513692") is None


@respx.mock
def test_unparseable_quantity_does_not_break_the_lookup():
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(quantity_value="circa")))
    off = fetch_product("8076809513692")
    assert off is not None and off.quantity_value is None


@respx.mock
def test_sends_the_required_user_agent():
    """OFF blocks generic agents outright, so this header is not optional."""
    route = respx.get(URL).mock(return_value=httpx.Response(200, json=off_response()))
    fetch_product("8076809513692")
    ua = route.calls[0].request.headers["user-agent"]
    assert ua.startswith("WasteWise/")
    assert "@" in ua  # contact address, as OFF's terms require
