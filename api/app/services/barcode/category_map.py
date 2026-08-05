"""Open Food Facts category tags -> the 31 seeded categories.

OFF returns a hierarchy from broad to specific:

    ["en:plant-based-foods-and-beverages", ..., "en:pasta-sauces", "en:tomato-sauces"]

The last tags are the most specific, so matching walks the list backwards and takes the
first rule that fires. That ordering is the whole trick: "en:tomato-sauces" must beat
"en:plant-based-foods", which would otherwise drag half the shop into ORTAGGI.

The rules table covers a normal Italian basket. Anything it misses goes to an LLM, which is
constrained to pick from the seeded codes and can never invent one. If no model is
configured the tail falls back to ALTRO — usable, just uncategorized, and visibly so.
"""

import logging

from app.models.enums import Storage

log = logging.getLogger(__name__)

# OFF tag (without the "en:" prefix) -> seeded category code.
# Ordered specific-to-general within each block for readability; lookup order comes from
# OFF's own hierarchy, not from this dict.
OFF_TAG_TO_CATEGORY: dict[str, str] = {
    # --- Fresh produce ---
    "fresh-vegetables": "ORTAGGI",
    "vegetables": "ORTAGGI",
    "tomatoes": "ORTAGGI",
    "potatoes": "ORTAGGI",
    "onions": "ORTAGGI",
    "carrots": "ORTAGGI",
    "mushrooms": "ORTAGGI",
    "fresh-fruits": "FRUTTA",
    "fruits": "FRUTTA",
    "apples": "FRUTTA",
    "bananas": "FRUTTA",
    "citrus": "FRUTTA",
    "berries": "FRUTTA",
    "prepared-salads": "INSALATA_BUSTA",
    "salads": "INSALATA_BUSTA",
    "fresh-herbs": "ERBE",
    "herbs": "ERBE",
    # --- Meat & fish ---
    "beef": "CARNE_ROSSA",
    "pork": "CARNE_ROSSA",
    "red-meat": "CARNE_ROSSA",
    "minced-meat": "CARNE_ROSSA",
    "sausages": "CARNE_ROSSA",
    "poultry": "CARNE_BIANCA",
    "chicken": "CARNE_BIANCA",
    "turkey": "CARNE_BIANCA",
    "chicken-breasts": "CARNE_BIANCA",
    "fresh-fish": "PESCE",
    "fishes": "PESCE",
    "seafood": "PESCE",
    "salmon": "PESCE",
    "hams": "SALUMI",
    "prosciutto": "SALUMI",
    "salamis": "SALUMI",
    "charcuterie": "SALUMI",
    "cured-meats": "SALUMI",
    "bacons": "SALUMI",
    # --- Dairy & eggs ---
    "milks": "LATTE",
    "uht-milks": "LATTE",
    "fresh-milks": "LATTE",
    "plant-based-milk-alternatives": "LATTE",
    "yogurts": "YOGURT",
    "greek-yogurts": "YOGURT",
    "fermented-milk-products": "YOGURT",
    "fresh-cheeses": "FORMAGGIO_FRESCO",
    "mozzarella": "FORMAGGIO_FRESCO",
    "ricotta": "FORMAGGIO_FRESCO",
    "cream-cheeses": "FORMAGGIO_FRESCO",
    "creams": "FORMAGGIO_FRESCO",
    "cheeses": "FORMAGGIO_STAG",
    "hard-cheeses": "FORMAGGIO_STAG",
    "parmigiano-reggiano": "FORMAGGIO_STAG",
    "grated-cheeses": "FORMAGGIO_STAG",
    "butters": "FORMAGGIO_STAG",
    "eggs": "UOVA",
    "chicken-eggs": "UOVA",
    # --- Fresh prepared ---
    "fresh-pasta": "PASTA_FRESCA",
    "stuffed-pasta": "PASTA_FRESCA",
    "gnocchi": "PASTA_FRESCA",
    "ready-made-meals": "PIATTI_PRONTI",
    "prepared-meals": "PIATTI_PRONTI",
    "pizzas": "PIATTI_PRONTI",
    "sandwiches": "PIATTI_PRONTI",
    # --- Bread ---
    "breads": "PANE",
    "fresh-breads": "PANE",
    "baguettes": "PANE",
    "sliced-breads": "PANE_CONF",
    "packaged-breads": "PANE_CONF",
    "rusks": "PANE_CONF",
    "crackers": "PANE_CONF",
    # --- Frozen ---
    "frozen-foods": "SURGELATI",
    "frozen-vegetables": "SURGELATI",
    "frozen-desserts": "SURGELATI",
    "ice-creams": "SURGELATI",
    "ice-cream": "SURGELATI",
    # --- Pantry ---
    "pastas": "PASTA_RISO",
    "dried-pastas": "PASTA_RISO",
    "durum-wheat-pasta": "PASTA_RISO",
    "rices": "PASTA_RISO",
    "cereals-and-potatoes": "PASTA_RISO",
    "legumes": "LEGUMI",
    "pulses": "LEGUMI",
    "chickpeas": "LEGUMI",
    "beans": "LEGUMI",
    "lentils": "LEGUMI",
    "canned-foods": "CONSERVE",
    "canned-fishes": "CONSERVE",
    "canned-tuna": "CONSERVE",
    "canned-vegetables": "CONSERVE",
    "tomato-sauces": "PASSATA",
    "pasta-sauces": "PASSATA",
    "tomato-purees": "PASSATA",
    "canned-tomatoes": "PASSATA",
    "tomatoes-and-their-products": "PASSATA",
    "olive-oils": "OLIO_ACETO",
    "vegetable-oils": "OLIO_ACETO",
    "vinegars": "OLIO_ACETO",
    "flours": "FARINA_ZUCCHERO",
    "wheat-flours": "FARINA_ZUCCHERO",
    "sugars": "FARINA_ZUCCHERO",
    "spices": "SPEZIE",
    "salts": "SPEZIE",
    "condiments": "SPEZIE",
    "coffees": "CAFFE_TE",
    "ground-coffees": "CAFFE_TE",
    "coffee-capsules": "CAFFE_TE",
    "teas": "CAFFE_TE",
    "infusions": "CAFFE_TE",
    "biscuits-and-cakes": "DOLCI_SNACK",
    "biscuits": "DOLCI_SNACK",
    "chocolates": "DOLCI_SNACK",
    "snacks": "DOLCI_SNACK",
    "sweet-snacks": "DOLCI_SNACK",
    "salty-snacks": "DOLCI_SNACK",
    "chips-and-fries": "DOLCI_SNACK",
    "spreads": "DOLCI_SNACK",
    "breakfast-cereals": "DOLCI_SNACK",
    "jams": "DOLCI_SNACK",
    "beverages": "BEVANDE",
    "waters": "BEVANDE",
    "sodas": "BEVANDE",
    "fruit-juices": "BEVANDE",
    "juices": "BEVANDE",
    "beers": "BEVANDE",
    "wines": "BEVANDE",
    "alcoholic-beverages": "BEVANDE",
}

# Broad tags that carry no useful signal on their own. If one of these is the only thing
# that matches, prefer asking a model over accepting a near-meaningless answer.
LOW_SIGNAL_TAGS = {
    "plant-based-foods",
    "plant-based-foods-and-beverages",
    "vegetable-based-foods",
    "vegetable-based-foods-and-beverages",
    "groceries",
    "foods",
    "food",
}

FALLBACK_CATEGORY = "ALTRO"

# Schema for the LLM tail. `enum` is filled in at call time with the real seeded codes, so
# the model physically cannot return a category that doesn't exist.
_LLM_SCHEMA_TEMPLATE = {
    "type": "object",
    "properties": {
        "category_code": {"type": "string", "enum": []},
        "shelf_life_days": {"type": ["integer", "null"]},
        "is_staple": {"type": "boolean"},
    },
    "required": ["category_code", "shelf_life_days", "is_staple"],
    "additionalProperties": False,
}

_LLM_SYSTEM = """Sei un classificatore di prodotti alimentari per un'app italiana di \
gestione della dispensa.

Dato il nome di un prodotto, la marca e i tag di categoria di Open Food Facts, scegli \
UNA categoria dall'elenco consentito e stima la sua durata di conservazione in giorni \
dall'acquisto, se non aperto.

Regole:
- category_code DEVE essere uno dei codici consentiti. Non inventarne altri.
- shelf_life_days: sii conservativo. La domanda e' "entro quando sara' ancora buono", \
non "entro quando e' legalmente sicuro". Usa null solo per prodotti non deperibili \
(detersivi, sale).
- is_staple: true solo per basi di dispensa sempre presenti in casa (olio, sale, pepe, \
farina, zucchero, aglio, cipolla, aceto). Tutto il resto e' false.
- Se il prodotto non e' cibo (detersivi, cura della persona), usa la categoria non-food \
piu' adatta."""


def map_off_categories(tags: list[str], known_codes: set[str]) -> str | None:
    """Rules-based mapping. Returns None when nothing specific matched.

    Walks OFF's hierarchy backwards because it runs broad -> specific, and the specific end
    is the only part that identifies a product. Low-signal tags are skipped so a hit on
    "plant-based-foods" never wins over a real match.
    """
    for tag in reversed(tags):
        bare = tag.split(":", 1)[-1]
        if bare in LOW_SIGNAL_TAGS:
            continue
        code = OFF_TAG_TO_CATEGORY.get(bare)
        if code and code in known_codes:
            return code
    return None


def classify_with_llm(
    *,
    name: str,
    brand: str | None,
    tags: list[str],
    known_codes: set[str],
    llm=None,
) -> tuple[str, int | None, bool] | None:
    """The tail: ask a model when the rules table has nothing.

    Returns `(category_code, shelf_life_days, is_staple)` or None if no provider is
    configured or every provider failed. None is an expected outcome — the caller falls
    back to ALTRO and the user can correct it in one tap.
    """
    from app.services.llm import LLMError, get_llm

    provider = llm if llm is not None else get_llm()
    if not provider.is_configured():
        return None

    schema = {
        **_LLM_SCHEMA_TEMPLATE,
        "properties": {
            **_LLM_SCHEMA_TEMPLATE["properties"],
            "category_code": {"type": "string", "enum": sorted(known_codes)},
        },
    }
    user = (
        f"Prodotto: {name}\n"
        f"Marca: {brand or 'sconosciuta'}\n"
        f"Tag Open Food Facts: {', '.join(tags[-8:]) if tags else 'nessuno'}\n"
        f"Codici consentiti: {', '.join(sorted(known_codes))}"
    )

    try:
        result = provider.complete_json(
            system=_LLM_SYSTEM, user=user, schema=schema, schema_name="product_category"
        )
    except LLMError as exc:
        log.info("LLM category classification unavailable for %r: %s", name, exc)
        return None

    code = result.get("category_code")
    if code not in known_codes:
        # Strict decoding should prevent this; if it happens, don't trust the rest either.
        log.warning("LLM returned unknown category %r for %r", code, name)
        return None

    days = result.get("shelf_life_days")
    if not isinstance(days, int) or days <= 0:
        days = None
    return code, days, bool(result.get("is_staple"))


def storage_for_category(storage: Storage) -> str:
    """Human label for the confirm screen."""
    return {
        Storage.FRIDGE: "Frigo",
        Storage.FREEZER: "Congelatore",
        Storage.PANTRY: "Dispensa",
        Storage.NONFOOD: "Non alimentare",
    }[storage]
