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
