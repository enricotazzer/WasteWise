from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from app.deps import CurrentUser, DbSession
from app.models import Category, Product, ProductAlias
from app.models.enums import AliasSource
from app.schemas.catalog import CategoryOut, ProductCreate, ProductOut
from app.seeds.data import WASTE_REASONS
from app.services.normalize import normalize_text

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: DbSession, user: CurrentUser) -> list[Category]:
    return list(db.scalars(select(Category).order_by(Category.sort_order)))


@router.get("/products", response_model=list[ProductOut])
def list_products(
    db: DbSession,
    user: CurrentUser,
    q: str | None = Query(
        default=None, description="Substring match on the Italian or English name"
    ),
    category_id: int | None = None,
    limit: int = Query(default=200, le=1000),
) -> list[Product]:
    stmt = select(Product)
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.name_it.ilike(pattern), Product.name_en.ilike(pattern)))
    return list(db.scalars(stmt.order_by(Product.name_it).limit(limit)))


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: DbSession, user: CurrentUser) -> Product:
    """Add a product the seed didn't cover. Also registers a text alias, so an Open Food
    Facts name that normalizes onto it matches from the first occurrence instead of
    creating a duplicate."""
    category = db.get(Category, payload.category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    product = Product(
        name_it=payload.name_it.strip(),
        name_en=(payload.name_en or None),
        category_id=category.id,
        default_unit=payload.default_unit,
        shelf_life_days=payload.shelf_life_days,
        shelf_life_days_opened=payload.shelf_life_days_opened,
        is_perishable=category.is_perishable,
        is_staple=payload.is_staple,
    )
    db.add(product)
    db.flush()

    norm = normalize_text(product.name_it)
    if norm and db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == norm)) is None:
        db.add(
            ProductAlias(
                raw_text_norm=norm,
                product_id=product.id,
                source=AliasSource.USER,
                confidence=1.0,
            )
        )

    db.commit()
    db.refresh(product)
    return product


@router.get("/waste-reasons")
def waste_reasons(user: CurrentUser) -> list[dict[str, str]]:
    return [{"code": code, "label": label} for code, label in WASTE_REASONS]
