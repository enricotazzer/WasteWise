from pydantic import BaseModel, ConfigDict

from app.models.enums import Storage, Unit


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name_it: str
    name_en: str
    storage: Storage
    default_shelf_life_days: int | None
    is_food: bool
    is_perishable: bool
    sort_order: int


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name_it: str
    name_en: str | None
    category_id: int
    default_unit: Unit
    shelf_life_days: int | None
    shelf_life_days_opened: int | None
    is_perishable: bool
    is_staple: bool


class ProductCreate(BaseModel):
    name_it: str
    name_en: str | None = None
    category_id: int
    default_unit: Unit = Unit.PZ
    shelf_life_days: int | None = None
    shelf_life_days_opened: int | None = None
    is_staple: bool = False
