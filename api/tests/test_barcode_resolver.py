"""The four-tier barcode resolver.

Two properties matter most and are asserted repeatedly here:

  1. **A barcode reaches the network at most once, ever.** The cache is what makes the
     scanner fast and what keeps us inside Open Food Facts' rate limit.
  2. **Nothing raises.** OFF down, no LLM, unknown code — all of it degrades to "ask the
     user for a name", because the scanner is a convenience over an add form that works.
"""

import httpx
import respx
from factories import off_response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Product, ProductAlias, ProductBarcode
from app.models.enums import MatchMethod
from app.services.barcode.resolver import bind_barcode, resolve_barcode

BARCODE = "8076809513692"
URL = f"{settings.off_base_url}/api/v2/product/{BARCODE}.json"

pytestmark = []


def _mock_off(**kwargs):
    return respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(**kwargs)))


# --- tier 2: Open Food Facts + rules map ---


@respx.mock
def test_off_hit_creates_a_product_in_the_mapped_category(db: Session, seeded, no_llm):
    _mock_off()
    result = resolve_barcode(db, BARCODE)

    assert result.match_method is MatchMethod.OFF
    assert result.product_name == "Napoletana"
    # en:tomato-sauces -> PASSATA, not the broad en:plant-based-foods that precedes it.
    assert result.category_code == "PASSATA"
    assert result.off_brand == "Barilla"
    # Fresh OFF matches are unverified, so the UI asks once.
    assert result.needs_confirmation is True

    product = db.get(Product, result.product_id)
    assert product.name_it == "Napoletana"
    assert product.category.code == "PASSATA"


@respx.mock
def test_pack_size_becomes_a_prefilled_quantity_in_base_units(db: Session, seeded, no_llm):
    """400 g -> 0.4 kg. Grams and millilitres are converted so prices stay comparable
    across pack sizes."""
    _mock_off(quantity_value=400, quantity_unit="g")
    result = resolve_barcode(db, BARCODE)
    assert result.suggested_quantity == 0.4
    assert result.suggested_unit.value == "kg"


@respx.mock
def test_millilitres_convert_to_litres(db: Session, seeded, no_llm):
    _mock_off(quantity_value=1500, quantity_unit="ml")
    result = resolve_barcode(db, BARCODE)
    assert (result.suggested_quantity, result.suggested_unit.value) == (1.5, "l")


@respx.mock
def test_an_off_name_matching_a_known_product_does_not_create_a_duplicate(
    db: Session, seeded, no_llm
):
    """The text alias table is shared with the recipe pipeline: an OFF name that normalizes
    onto an existing product must bind to it, or the catalog fragments."""
    db.add(
        ProductAlias(raw_text_norm="MOZZARELLA", product_id=seeded["mozzarella"].id, confidence=1.0)
    )
    db.commit()
    before = db.scalar(select(Product).where(Product.name_it == "Mozzarella")).id

    _mock_off(name="Mozzarella 125g", categories=["en:fresh-cheeses"])
    result = resolve_barcode(db, BARCODE)

    assert result.product_id == before
    assert db.query(Product).filter(Product.name_it.ilike("mozzarella%")).count() == 1


# --- tier 1: the cache ---


@respx.mock
def test_second_scan_is_served_from_cache_without_touching_the_network(db: Session, seeded, no_llm):
    route = _mock_off()
    first = resolve_barcode(db, BARCODE)
    second = resolve_barcode(db, BARCODE)

    assert route.call_count == 1, "the cache must prevent a second OFF request"
    assert first.match_method is MatchMethod.OFF
    assert second.match_method is MatchMethod.CACHE
    assert second.from_cache is True
    assert second.product_id == first.product_id
    # Already seen and mapped — no need to ask the user again.
    assert second.needs_confirmation is False


@respx.mock
def test_lookup_count_accumulates_across_scans(db: Session, seeded, no_llm):
    _mock_off()
    for _ in range(3):
        resolve_barcode(db, BARCODE)
    record = db.scalar(select(ProductBarcode).where(ProductBarcode.barcode == BARCODE))
    assert record.lookup_count == 3


@respx.mock
def test_the_verbatim_off_payload_is_stored_once_and_not_rewritten(db: Session, seeded, no_llm):
    _mock_off()
    resolve_barcode(db, BARCODE)
    record = db.scalar(select(ProductBarcode).where(ProductBarcode.barcode == BARCODE))
    snapshot = dict(record.off_payload)

    resolve_barcode(db, BARCODE)
    db.refresh(record)
    assert record.off_payload == snapshot


# --- store-internal codes ---


@respx.mock
def test_store_produce_labels_never_reach_the_network(db: Session, seeded, no_llm):
    """EAN-13 codes starting with 2 encode a price at one supermarket. Asking OFF about
    them wastes a request against a rate limit that matters."""
    route = respx.get(url__regex=r".*openfoodfacts.*").mock(
        return_value=httpx.Response(200, json=off_response())
    )
    result = resolve_barcode(db, "2100123456789")

    assert route.call_count == 0
    assert result.match_method is MatchMethod.UNRESOLVED
    assert result.needs_confirmation is True


# --- failure paths, all of which must be survivable ---


@respx.mock
def test_unknown_barcode_is_unresolved_not_an_error(db: Session, seeded, no_llm):
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(found=False)))
    result = resolve_barcode(db, BARCODE)
    assert result.match_method is MatchMethod.UNRESOLVED
    assert result.product_id is None
    assert result.needs_confirmation is True


@respx.mock
def test_off_being_down_degrades_to_manual_naming(db: Session, seeded, no_llm):
    respx.get(URL).mock(side_effect=httpx.ConnectError("offline"))
    result = resolve_barcode(db, BARCODE)
    assert result.match_method is MatchMethod.UNRESOLVED
    assert result.product_id is None


@respx.mock
def test_an_off_record_with_no_name_is_unusable(db: Session, seeded, no_llm):
    """OFF is community-edited: records exist with a barcode and nothing else."""
    _mock_off(name=None)
    assert resolve_barcode(db, BARCODE).match_method is MatchMethod.UNRESOLVED


@respx.mock
def test_unmappable_category_without_an_llm_falls_back_rather_than_failing(
    db: Session, seeded, no_llm
):
    """No API key configured is the state a reviewer clones this repo in. The product is
    still created — just uncategorized, and visibly so."""
    _mock_off(name="Cosa strana", categories=["en:something-nobody-has-seen"])
    result = resolve_barcode(db, BARCODE)
    assert result.match_method is MatchMethod.OFF
    assert result.category_code == "ALTRO"
    assert result.product_id is not None


def test_a_barcode_with_no_digits_is_rejected_cleanly(db: Session, seeded, no_llm):
    result = resolve_barcode(db, "???")
    assert result.barcode == ""
    assert result.match_method is MatchMethod.UNRESOLVED


@respx.mock
def test_unresolved_scans_are_still_recorded(db: Session, seeded, no_llm):
    """A barcode we couldn't identify is still evidence — it's what the hit-rate
    denominator counts, and it's the queue of things to bind by hand."""
    respx.get(URL).mock(return_value=httpx.Response(200, json=off_response(found=False)))
    resolve_barcode(db, BARCODE)
    record = db.scalar(select(ProductBarcode).where(ProductBarcode.barcode == BARCODE))
    assert record is not None
    assert record.is_resolved is False
    assert record.lookup_count == 1


# --- tier 4: manual bind ---


def test_bind_creates_a_product_and_a_confirmed_alias(db: Session, seeded, no_llm):
    result = bind_barcode(
        db,
        "2100123456789",
        new_product_name="Mele Golden sfuse",
        category_id=seeded["fridge"].id,
        shelf_life_days=21,
    )
    assert result.match_method is MatchMethod.MANUAL
    assert result.product_name == "Mele Golden sfuse"

    alias = db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == "MELE GOLDEN SFUSE"))
    assert alias is not None
    assert alias.source.value == "user"
    # A human said so, so it counts as confirmed ground truth.
    assert alias.confirmed_at is not None


def test_bind_to_an_existing_product_reuses_it(db: Session, seeded, no_llm):
    result = bind_barcode(db, "2100000000001", product_id=seeded["mozzarella"].id)
    assert result.product_id == seeded["mozzarella"].id
    assert db.query(Product).count() == 4  # nothing new created


def test_a_bound_barcode_resolves_from_cache_forever_after(db: Session, seeded, no_llm):
    """The point of tier 4: type it once, never again — including for store labels that OFF
    will never know about."""
    bind_barcode(db, "2100123456789", product_id=seeded["mozzarella"].id)
    result = resolve_barcode(db, "2100123456789")
    assert result.match_method is MatchMethod.CACHE
    assert result.product_id == seeded["mozzarella"].id
    assert result.needs_confirmation is False


@respx.mock
def test_a_manual_bind_overrides_an_earlier_off_match(db: Session, seeded, no_llm):
    """OFF got it wrong and the user corrected it. That correction is authoritative and a
    later OFF change must never silently undo it."""
    _mock_off()
    resolve_barcode(db, BARCODE)
    bind_barcode(db, BARCODE, product_id=seeded["pasta"].id)

    record = db.scalar(select(ProductBarcode).where(ProductBarcode.barcode == BARCODE))
    assert record.product_id == seeded["pasta"].id
    assert record.confirmed_by_user is True
    assert record.match_method is MatchMethod.MANUAL
