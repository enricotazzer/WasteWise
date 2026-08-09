from enum import StrEnum


class Storage(StrEnum):
    """Where an item lives. Drives the default shelf life and the Kitchen screen grouping."""

    PANTRY = "pantry"
    FRIDGE = "fridge"
    FREEZER = "freezer"
    NONFOOD = "nonfood"


class Unit(StrEnum):
    KG = "kg"
    G = "g"
    L = "l"
    ML = "ml"
    PZ = "pz"  # pezzi / units


class MatchMethod(StrEnum):
    """How a barcode was mapped to a canonical product. The distribution across these is the
    measured quality of Module 1: CACHE and OFF cost nothing, LLM costs a free-tier call,
    MANUAL costs the user's time — which is the only expensive one."""

    CACHE = "cache"  # already in product_barcodes — no network at all
    OFF = "off"  # Open Food Facts + the rules-based category map
    LLM = "llm"  # OFF gave a product but the category needed a model
    MANUAL = "manual"  # user named it: unknown barcode, or OFF was wrong
    UNRESOLVED = "unresolved"  # seen, not yet mapped to anything


class ItemStatus(StrEnum):
    """Lifecycle of one inventory item — the unit of observation for the survival model."""

    ACTIVE = "active"  # right-censored while in this state
    CONSUMED = "consumed"  # event of interest
    WASTED = "wasted"  # competing event
    PARTIAL = "partial"  # opened / partly used, still active


class EventType(StrEnum):
    CONSUMED = "consumed"
    WASTED = "wasted"
    PARTIAL = "partial"


class EventSource(StrEnum):
    """Label provenance. Inferred labels are down-weighted in training — they conflate
    consumption with waste, which is exactly the distinction this project measures."""

    MANUAL = "manual"
    REPURCHASE_INFERRED = "repurchase_inferred"


class ExpirySource(StrEnum):
    LABEL = "label"  # user typed the printed date — most trustworthy
    PRODUCT = "product"  # per-product shelf life
    CATEGORY = "category"  # category default — weakest


class AliasSource(StrEnum):
    USER = "user"  # confirmed by a human — authoritative
    LLM = "llm"  # proposed by a model, unconfirmed
    SEED = "seed"
    OFF = "off"  # taken from an Open Food Facts product name


class RiskBand(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SavingsDomain(StrEnum):
    """Which part of household spending an entry belongs to.

    Food is the only domain that writes rows today. The other two are declared now, not
    later, because they are what makes `savings_entries` a ledger rather than a second
    inventory table: the aggregation queries group by domain, so a Bills module adds rows
    and appears on the dashboard without a schema change or a second set of queries.
    """

    FOOD = "food"
    UTILITIES = "utilities"
    SUBSCRIPTIONS = "subscriptions"


class EntryKind(StrEnum):
    """What an entry says about the money.

    SPEND is the whole; USED and WASTE are partitions of it. AVOIDED is a *counterfactual* —
    money that would have been spent and wasn't — and the food domain deliberately never
    writes one. See the R6 note in `services/savings/rules.py`.
    """

    SPEND = "spend"  # money left the household
    USED = "used"  # part of a SPEND that produced its intended value
    WASTE = "waste"  # part of a SPEND that bought nothing
    AVOIDED = "avoided"  # counterfactual saving, measured against a baseline


class EntryBasis(StrEnum):
    """How much the number on the row is worth believing. Ordered here from strongest to
    weakest; the reporting layer refuses to sum euros across two of them."""

    MEASURED = "measured"  # a price the user actually typed
    INFERRED_PRICE = "inferred_price"  # this item had no price; one was carried over
    MODELLED = "modelled"  # produced by a model, not observed
    UNPRICED = "unpriced"  # the outcome is known, the money is not — amount_eur is NULL


class SourceKind(StrEnum):
    """What produced an entry. Half of the ledger's identity key, and deliberately not a
    foreign key: a domain that has no inventory items still needs stable row identity."""

    INVENTORY_ITEM = "inventory_item"
    CONSUMPTION_EVENT = "consumption_event"
    SAVINGS_BASELINE = "savings_baseline"  # utilities: the counterfactual an AVOIDED cites
