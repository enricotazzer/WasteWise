"""Seeded taxonomy for an Italian household shop.

⛳ CHECKPOINT REVIEW ITEM. These shelf-life numbers are the cold-start prior for the whole
system: they set every item's expiry date until the survival model has data, and they are
what the v0 risk heuristic ranks on. Wrong numbers here produce wrong risk flags for weeks.

They are deliberately conservative (shorter than the printed best-before), because the
question is "when will this realistically still be good", not "when is it legally safe".
Adjust freely — `make seed` is idempotent and updates in place.

Non-food categories are included because a shop contains them, but is_food=False keeps them
out of every waste and savings metric.
"""

from app.models.enums import Storage, Unit

# (code, name_it, name_en, storage, shelf_life_days, is_food, is_perishable, sort_order)
CATEGORIES: list[tuple] = [
    # --- Fresh, fridge: the categories that actually get wasted ---
    ("ORTAGGI",         "Ortaggi freschi",      "Fresh vegetables",   Storage.FRIDGE,   7,   True,  True,  10),
    ("FRUTTA",          "Frutta fresca",        "Fresh fruit",        Storage.FRIDGE,   7,   True,  True,  11),
    ("INSALATA_BUSTA",  "Insalata in busta",    "Bagged salad",       Storage.FRIDGE,   4,   True,  True,  12),
    ("ERBE",            "Erbe aromatiche",      "Fresh herbs",        Storage.FRIDGE,   5,   True,  True,  13),
    ("CARNE_ROSSA",     "Carne rossa",          "Red meat",           Storage.FRIDGE,   3,   True,  True,  20),
    ("CARNE_BIANCA",    "Carne bianca",         "Poultry",            Storage.FRIDGE,   2,   True,  True,  21),
    ("PESCE",           "Pesce fresco",         "Fresh fish",         Storage.FRIDGE,   2,   True,  True,  22),
    ("SALUMI",          "Salumi",               "Cured meats",        Storage.FRIDGE,  15,   True,  True,  23),
    ("LATTE",           "Latte",                "Milk",               Storage.FRIDGE,   6,   True,  True,  30),
    ("YOGURT",          "Yogurt",               "Yoghurt",            Storage.FRIDGE,  14,   True,  True,  31),
    ("FORMAGGIO_FRESCO","Formaggio fresco",     "Fresh cheese",       Storage.FRIDGE,   5,   True,  True,  32),
    ("FORMAGGIO_STAG",  "Formaggio stagionato", "Aged cheese",        Storage.FRIDGE,  30,   True,  True,  33),
    ("UOVA",            "Uova",                 "Eggs",               Storage.FRIDGE,  21,   True,  True,  34),
    ("PASTA_FRESCA",    "Pasta fresca",         "Fresh pasta",        Storage.FRIDGE,   7,   True,  True,  40),
    ("PIATTI_PRONTI",   "Piatti pronti",        "Ready meals",        Storage.FRIDGE,   4,   True,  True,  41),
    ("PANE",            "Pane fresco",          "Fresh bread",        Storage.PANTRY,   3,   True,  True,  50),

    # --- Frozen ---
    ("SURGELATI",       "Surgelati",            "Frozen food",        Storage.FREEZER, 180,  True,  True,  60),

    # --- Pantry: long shelf life, rarely wasted, but they dominate spend ---
    ("PANE_CONF",       "Pane confezionato",    "Packaged bread",     Storage.PANTRY,  20,   True,  True,  70),
    ("PASTA_RISO",      "Pasta e riso",         "Pasta and rice",     Storage.PANTRY, 540,   True,  False, 71),
    ("LEGUMI",          "Legumi secchi",        "Dried pulses",       Storage.PANTRY, 720,   True,  False, 72),
    ("CONSERVE",        "Conserve e scatolame", "Canned goods",       Storage.PANTRY, 720,   True,  False, 73),
    ("PASSATA",         "Passata e pelati",     "Tomato passata",     Storage.PANTRY, 540,   True,  False, 74),
    ("OLIO_ACETO",      "Olio e aceto",         "Oil and vinegar",    Storage.PANTRY, 365,   True,  False, 75),
    ("FARINA_ZUCCHERO", "Farina e zucchero",    "Flour and sugar",    Storage.PANTRY, 365,   True,  False, 76),
    ("SPEZIE",          "Spezie",               "Spices",             Storage.PANTRY, 730,   True,  False, 77),
    ("CAFFE_TE",        "Caffè e tè",           "Coffee and tea",     Storage.PANTRY, 365,   True,  False, 78),
    ("DOLCI_SNACK",     "Dolci e snack",        "Sweets and snacks",  Storage.PANTRY,  90,   True,  True,  79),
    ("BEVANDE",         "Bevande",              "Drinks",             Storage.PANTRY, 180,   True,  False, 80),

    # --- Non-food: excluded from waste metrics ---
    ("DETERGENTI",      "Detergenti e casa",    "Cleaning products",  Storage.NONFOOD, None, False, False, 90),
    ("CURA_PERSONA",    "Cura della persona",   "Personal care",      Storage.NONFOOD, None, False, False, 91),
    ("ALTRO",           "Altro",                "Other",              Storage.NONFOOD, None, False, False, 99),
]

# A small starter vocabulary so the add form has something to pick from on day 1. It grows
# on its own from Phase 1 onward: every Open Food Facts hit and every hand-bound barcode
# adds to it.
#
# is_staple = "assumed to be in the house". Recipe ranking (Phase 4) never counts a staple
# as a missing ingredient — otherwise every recipe looks uncookable because nobody logs
# their salt. Staples are still trackable as inventory; they just don't gate a suggestion.
#
# (name_it, name_en, category_code, default_unit, shelf_life_days, shelf_life_days_opened, is_staple)
PRODUCTS: list[tuple] = [
    # Ortaggi
    ("Zucchine",        "zucchini",       "ORTAGGI",          Unit.KG,   7,   None, False),
    ("Pomodori",        "tomato",         "ORTAGGI",          Unit.KG,   7,   None, False),
    ("Insalata",        "lettuce",        "ORTAGGI",          Unit.PZ,   5,   None, False),
    ("Carote",          "carrot",         "ORTAGGI",          Unit.KG,  14,   None, False),
    ("Cipolle",         "onion",          "ORTAGGI",          Unit.KG,  30,   None, True),
    ("Patate",          "potato",         "ORTAGGI",          Unit.KG,  30,   None, False),
    ("Aglio",           "garlic",         "ORTAGGI",          Unit.PZ,  60,   None, True),
    ("Peperoni",        "bell pepper",    "ORTAGGI",          Unit.KG,   8,   None, False),
    ("Melanzane",       "eggplant",       "ORTAGGI",          Unit.KG,   7,   None, False),
    ("Spinaci",         "spinach",        "ORTAGGI",          Unit.PZ,   4,   None, False),
    ("Funghi",          "mushroom",       "ORTAGGI",          Unit.PZ,   5,   None, False),
    ("Broccoli",        "broccoli",       "ORTAGGI",          Unit.KG,   7,   None, False),
    # Frutta
    ("Mele",            "apple",          "FRUTTA",           Unit.KG,  21,   None, False),
    ("Banane",          "banana",         "FRUTTA",           Unit.KG,   6,   None, False),
    ("Arance",          "orange",         "FRUTTA",           Unit.KG,  14,   None, False),
    ("Limoni",          "lemon",          "FRUTTA",           Unit.KG,  21,   None, False),
    ("Fragole",         "strawberry",     "FRUTTA",           Unit.PZ,   3,   None, False),
    ("Uva",             "grape",          "FRUTTA",           Unit.KG,   7,   None, False),
    ("Pere",            "pear",           "FRUTTA",           Unit.KG,  10,   None, False),
    # Erbe
    ("Basilico",        "basil",          "ERBE",             Unit.PZ,   5,   None, False),
    ("Prezzemolo",      "parsley",        "ERBE",             Unit.PZ,   6,   None, False),
    ("Rosmarino",       "rosemary",       "ERBE",             Unit.PZ,  10,   None, False),
    # Carne / pesce
    ("Petto di pollo",  "chicken breast", "CARNE_BIANCA",     Unit.KG,   2,   None, False),
    ("Macinato di manzo","ground beef",   "CARNE_ROSSA",      Unit.KG,   2,   None, False),
    ("Salsiccia",       "sausage",        "CARNE_ROSSA",      Unit.KG,   3,   None, False),
    ("Salmone",         "salmon",         "PESCE",            Unit.KG,   2,   None, False),
    ("Prosciutto crudo","prosciutto",     "SALUMI",           Unit.PZ,  15,     4, False),
    ("Prosciutto cotto","cooked ham",     "SALUMI",           Unit.PZ,  12,     3, False),
    # Latticini
    ("Latte intero",    "milk",           "LATTE",            Unit.L,    6,     4, False),
    ("Yogurt bianco",   "yogurt",         "YOGURT",           Unit.PZ,  14,     3, False),
    ("Mozzarella",      "mozzarella",     "FORMAGGIO_FRESCO", Unit.PZ,   5,     2, False),
    ("Ricotta",         "ricotta",        "FORMAGGIO_FRESCO", Unit.PZ,   5,     3, False),
    ("Parmigiano",      "parmesan",       "FORMAGGIO_STAG",   Unit.PZ,  60,    30, False),
    ("Uova",            "egg",            "UOVA",             Unit.PZ,  21,   None, False),
    ("Burro",           "butter",         "FORMAGGIO_STAG",   Unit.PZ,  45,    21, False),
    # Dispensa
    ("Pasta",           "pasta",          "PASTA_RISO",       Unit.KG, 540,   None, False),
    ("Riso",            "rice",           "PASTA_RISO",       Unit.KG, 540,   None, False),
    ("Passata di pomodoro","tomato sauce","PASSATA",          Unit.PZ, 540,     4, False),
    ("Pelati",          "canned tomatoes","PASSATA",          Unit.PZ, 540,     4, False),
    ("Olio extravergine","olive oil",     "OLIO_ACETO",       Unit.L,  365,   None, True),
    ("Aceto",           "vinegar",        "OLIO_ACETO",       Unit.L,  730,   None, True),
    ("Tonno in scatola","canned tuna",    "CONSERVE",         Unit.PZ, 720,     2, False),
    ("Ceci",            "chickpeas",      "LEGUMI",           Unit.PZ, 720,     3, False),
    ("Fagioli",         "beans",          "LEGUMI",           Unit.PZ, 720,     3, False),
    ("Farina",          "flour",          "FARINA_ZUCCHERO",  Unit.KG, 365,   None, True),
    ("Zucchero",        "sugar",          "FARINA_ZUCCHERO",  Unit.KG, 730,   None, True),
    ("Sale",            "salt",           "SPEZIE",           Unit.KG,None,   None, True),
    ("Pepe nero",       "black pepper",   "SPEZIE",           Unit.PZ, 730,   None, True),
    ("Peperoncino",     "chilli",         "SPEZIE",           Unit.PZ, 730,   None, True),
    ("Piselli",         "peas",           "LEGUMI",           Unit.PZ, 540,     3, False),
    ("Marmellata",      "jam",            "DOLCI_SNACK",      Unit.PZ, 540,    30, False),
    ("Caffè",           "coffee",         "CAFFE_TE",         Unit.PZ, 365,    30, False),
    # Pane
    ("Pane",            "bread",          "PANE",             Unit.KG,   3,   None, False),
    ("Pane in cassetta","sliced bread",   "PANE_CONF",        Unit.PZ,  20,     6, False),
    # Non-food
    ("Detersivo piatti","dish soap",      "DETERGENTI",       Unit.PZ, None,  None, False),
    ("Carta igienica",  "toilet paper",   "CURA_PERSONA",     Unit.PZ, None,  None, False),
]

# Waste reasons offered in the Kitchen screen. Kept short — a long list means nobody picks.
WASTE_REASONS = [
    ("scaduto", "Scaduto / andato a male"),
    ("dimenticato", "Dimenticato in frigo"),
    ("troppo", "Comprato troppo"),
    ("avanzo", "Avanzo non consumato"),
    ("non_piaciuto", "Non piaciuto"),
    ("altro", "Altro"),
]
