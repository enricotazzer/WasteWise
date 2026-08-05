"""Payload builders shared across test modules."""


def off_response(
    barcode: str = "8076809513692",
    *,
    name: str | None = "Napoletana",
    brand: str = "Barilla",
    quantity: str = "400g",
    quantity_value: float | None = 400,
    quantity_unit: str = "g",
    categories: list[str] | None = None,
    found: bool = True,
) -> dict:
    """An Open Food Facts v2 payload, shaped like the real one.

    The field names and the `status: 1` envelope were verified against the live API on
    2026-08-05 with barcode 8076809513692 (Barilla Napoletana). If OFF ever changes shape,
    this builder is what should be corrected first — everything else mocks against it.
    """
    if not found:
        return {"code": barcode, "status": 0, "status_verbose": "product not found"}
    return {
        "code": barcode,
        "status": 1,
        "status_verbose": "product found",
        "product": {
            "code": barcode,
            "product_name": name,
            "product_name_it": name,
            "brands": brand,
            "quantity": quantity,
            "product_quantity": quantity_value,
            "product_quantity_unit": quantity_unit,
            # Real ordering: broad -> specific, which is what makes reverse matching matter.
            "categories_tags": categories
            if categories is not None
            else ["en:plant-based-foods", "en:sauces", "en:tomato-sauces"],
        },
    }
