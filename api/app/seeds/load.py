"""Idempotent seed loader.

Safe to re-run: categories and products are upserted by natural key, so you can edit
data.py and `make seed` again without dropping the database or losing inventory.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Category, Product, ProductAlias, User
from app.models.enums import AliasSource
from app.security import hash_password
from app.seeds.data import CATEGORIES, PRODUCTS
from app.services.normalize import normalize_text


def seed_categories(db: Session) -> dict[str, Category]:
    by_code: dict[str, Category] = {}
    for code, it, en, storage, days, is_food, is_perish, order in CATEGORIES:
        cat = db.scalar(select(Category).where(Category.code == code))
        if cat is None:
            cat = Category(code=code)
            db.add(cat)
        cat.name_it = it
        cat.name_en = en
        cat.storage = storage
        cat.default_shelf_life_days = days
        cat.is_food = is_food
        cat.is_perishable = is_perish
        cat.sort_order = order
        by_code[code] = cat
    db.flush()
    return by_code


def seed_products(db: Session, cats: dict[str, Category]) -> int:
    created = 0
    for name_it, name_en, cat_code, unit, days, days_opened, is_staple in PRODUCTS:
        category = cats[cat_code]
        prod = db.scalar(
            select(Product).where(Product.name_it == name_it, Product.category_id == category.id)
        )
        if prod is None:
            prod = Product(name_it=name_it, category_id=category.id)
            db.add(prod)
            created += 1
        prod.name_en = name_en
        prod.default_unit = unit
        prod.shelf_life_days = days
        prod.shelf_life_days_opened = days_opened
        prod.is_perishable = category.is_perishable
        prod.is_staple = is_staple
        db.flush()

        # Seed the alias table with the canonical name so exact matching works from day 1.
        norm = normalize_text(name_it)
        alias = db.scalar(select(ProductAlias).where(ProductAlias.raw_text_norm == norm))
        if alias is None:
            db.add(
                ProductAlias(
                    raw_text_norm=norm,
                    product_id=prod.id,
                    source=AliasSource.SEED,
                    confidence=1.0,
                )
            )
    return created


def seed_first_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == settings.first_user_email))
    if user is None:
        user = User(
            email=settings.first_user_email,
            password_hash=hash_password(settings.first_user_password),
            display_name="Enrico",
        )
        db.add(user)
        db.flush()
        print(f"  created user {user.email}")
    else:
        print(f"  user {user.email} already exists (password unchanged)")
    return user


def main() -> int:
    if settings.first_user_password == "change-me":
        print("WARNING: FIRST_USER_PASSWORD is still the default. Set it in .env.", file=sys.stderr)

    with SessionLocal() as db:
        print("seeding categories...")
        cats = seed_categories(db)
        print(f"  {len(cats)} categories")

        print("seeding products...")
        created = seed_products(db, cats)
        print(f"  {len(PRODUCTS)} products ({created} new)")

        print("seeding first user...")
        seed_first_user(db)

        db.commit()
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
