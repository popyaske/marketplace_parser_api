"""
База данных: async SQLAlchemy + PostgreSQL.

Модель Product хранит результаты парсинга.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Integer, String, Text,
    func, select, desc, UniqueConstraint, update, delete
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import insert


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://parser:parser@postgres:5432/parser",
)


class Base(DeclarativeBase):
    pass


class Product(Base):
    """Результат парсинга одного товара."""

    __tablename__ = "products"

    __table_args__ = (
        UniqueConstraint("url", "marketplace", name="uq_url_marketplace"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Источник
    url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    share_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Данные товара
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    price_with_plus: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_original_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    seller: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    in_stock: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tier_used: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    post_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Статус
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Мета
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class PriceHistory(Base):
    """История цен — новая запись при каждом парсинге."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(BigInteger, index=True)
    price_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_original_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_with_plus: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    in_stock: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class PublishedProduct(Base):
    """Товары, уже опубликованные в Telegram из каталога Такпродам.
    Защита от повторной публикации (дедупликация по takprodam_id)."""

    __tablename__ = "published_products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    takprodam_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    product_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tracking_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class PublishedReferralProduct(Base):
    """Товары из реферальной программы Яндекс.Маркета, уже опубликованные."""

    __tablename__ = "published_referral_products"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    referral_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class PublishedMedia(Base):
    """Фото, опубликованные в Telegram — источник для VK-видео."""

    __tablename__ = "published_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    post_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    video_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )


class PublishedVideo(Base):
    """Собранные видео/клипы для VK."""

    __tablename__ = "published_videos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    vk_owner_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    vk_video_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    vk_post_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True,
    )

# === Engine и сессии ===

_engine: Optional[AsyncEngine] = None
_session_maker: Optional[async_sessionmaker] = None


def init_engine() -> AsyncEngine:
    """Создать engine (один раз при старте)."""
    global _engine, _session_maker
    if _engine is None:
        _engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
        _session_maker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def create_tables():
    """Создать таблицы (если не существуют)."""
    engine = init_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session_maker() -> async_sessionmaker:
    if _session_maker is None:
        init_engine()
    return _session_maker


async def save_product(data: dict) -> int:
    """Сохранить результат парсинга. Возвращает id."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = insert(Product).values(
            url=data.get("url", ""),
            marketplace=data.get("marketplace", "unknown"),
            sku=data.get("sku"),
            title=data.get("title"),
            price_rub=data.get("price_rub"),
            price_with_plus=data.get("price_with_plus"),
            price_original_rub=data.get("price_original_rub"),
            seller=data.get("seller"),
            brand=data.get("brand"),
            rating=data.get("rating"),
            rating_count=data.get("rating_count"),
            in_stock=data.get("in_stock"),
            image_url=data.get("image_url"),
            tier_used=data.get("tier_used"),
            post_text=data.get("post_text"),
            success=data.get("success", True),
            error=data.get("error"),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["url", "marketplace"],
            set_={
                "price_rub": data.get("price_rub"),
                "price_with_plus": data.get("price_with_plus"),
                "price_original_rub": data.get("price_original_rub"),
                "rating": data.get("rating"),
                "rating_count": data.get("rating_count"),
                "in_stock": data.get("in_stock"),
                "created_at": func.now(),
            },
        ).returning(Product.id)
        result = await session.execute(stmt)
        await session.commit()
        product_id = result.scalar_one()

        history = PriceHistory(
            product_id=product_id,
            price_rub=data.get("price_rub"),
            price_original_rub=data.get("price_original_rub"),
            price_with_plus=data.get("price_with_plus"),
            in_stock=data.get("in_stock"),
        )
        session.add(history)
        await session.commit()

        return product_id


async def list_products(
    marketplace: Optional[str] = None,
    search: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    only_success: bool = True,
    page: int = 1,
    per_page: int = 20,
) -> tuple[List[Product], int]:
    """Список товаров с фильтрами. Возвращает (items, total)."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = select(Product)

        if only_success:
            stmt = stmt.where(Product.success == True)
        if marketplace:
            stmt = stmt.where(Product.marketplace == marketplace)
        if search:
            stmt = stmt.where(Product.title.ilike(f"%{search}%"))
        if min_price is not None:
            stmt = stmt.where(Product.price_rub >= min_price)
        if max_price is not None:
            stmt = stmt.where(Product.price_rub <= max_price)

        # Total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar_one()

        # Pagination
        stmt = stmt.order_by(desc(Product.created_at))
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        items = list(result.scalars().all())
        return items, total


async def get_product(product_id: int) -> Optional[Product]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        return await session.get(Product, product_id)


async def delete_product(product_id: int) -> bool:
    session_maker = get_session_maker()
    async with session_maker() as session:
        product = await session.get(Product, product_id)
        if product is None:
            return False
        await session.delete(product)
        await session.commit()
        return True


async def get_stats() -> dict:
    """Статистика: total, by_marketplace, last_24h."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        total = (await session.execute(
            select(func.count()).select_from(Product).where(Product.success == True)
        )).scalar_one()

        by_mp_rows = await session.execute(
            select(Product.marketplace, func.count())
            .where(Product.success == True)
            .group_by(Product.marketplace)
        )
        by_marketplace = {row[0]: row[1] for row in by_mp_rows.all()}

        last_24h = (await session.execute(
            select(func.count())
            .select_from(Product)
            .where(Product.created_at >= datetime.now(timezone.utc) - timedelta(days=1))
        )).scalar_one()

        return {
            "total": total,
            "by_marketplace": by_marketplace,
            "last_24h": last_24h,
        }


async def get_price_history(product_id: int, limit: int = 100) -> List[PriceHistory]:
    """История цен для одного товара."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .order_by(desc(PriceHistory.created_at))
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_products_since(last_id: int) -> List[Product]:
    """Получить товары с id > last_id."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = (
            select(Product)
            .where(Product.id > last_id)
            .order_by(Product.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_published_ids() -> set[str]:
    """Все takprodam_id, которые уже были опубликованы."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        result = await session.execute(select(PublishedProduct.takprodam_id))
        return {row[0] for row in result.all()}


async def mark_published(item: dict) -> None:
    """Отметить товар как опубликованный (идемпотентно)."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = (
            insert(PublishedProduct)
            .values(
                takprodam_id=str(item["id"]),
                product_id=(
                    str(item.get("product_id"))
                    if item.get("product_id") is not None
                    else None
                ),
                marketplace=str(item.get("marketplace_title", "unknown")).lower(),
                title=item.get("title"),
                tracking_link=item.get("tracking_link"),
            )
            .on_conflict_do_nothing(index_elements=["takprodam_id"])
        )
        await session.execute(stmt)
        await session.commit()


async def get_last_published_at() -> Optional[datetime]:
    """Время последней публикации (для логов/диагностики)."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        result = await session.execute(
            select(PublishedProduct.published_at)
            .order_by(desc(PublishedProduct.published_at))
            .limit(1)
        )
        return result.scalar_one_or_none()


async def get_published_referral_ids() -> set[str]:
    """Все product_id из рефералки Яндекса, уже опубликованные."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        result = await session.execute(select(PublishedReferralProduct.product_id))
        return {row[0] for row in result.all()}


async def mark_referral_published(
    product_id: str, title: Optional[str], referral_link: Optional[str]
) -> None:
    """Отметить товар из рефералки как опубликованный (идемпотентно)."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = (
            insert(PublishedReferralProduct)
            .values(
                product_id=str(product_id),
                title=title,
                referral_link=referral_link,
            )
            .on_conflict_do_nothing(index_elements=["product_id"])
        )
        await session.execute(stmt)
        await session.commit()


async def save_media(
    source: str,
    image_path: str,
    title: Optional[str] = None,
    price_rub: Optional[float] = None,
    post_text: Optional[str] = None,
    image_url: Optional[str] = None,
    product_id: Optional[int] = None,
) -> int:
    """Сохранить факт публикации фото."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        media = PublishedMedia(
            source=source,
            product_id=product_id,
            title=title,
            price_rub=price_rub,
            post_text=post_text,
            image_path=image_path,
            image_url=image_url,
        )
        session.add(media)
        await session.commit()
        await session.refresh(media)
        return media.id


async def get_unused_media(limit: int = 5) -> List[PublishedMedia]:
    """Фото, ещё не вошедшие ни в одно видео."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = (
            select(PublishedMedia)
            .where(PublishedMedia.video_id.is_(None))
            .order_by(PublishedMedia.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def create_published_video(
    file_path: str,
    photo_count: int,
    duration_sec: float,
    status: str = "created",
) -> PublishedVideo:
    session_maker = get_session_maker()
    async with session_maker() as session:
        video = PublishedVideo(
            file_path=file_path,
            photo_count=photo_count,
            duration_sec=duration_sec,
            status=status,
        )
        session.add(video)
        await session.commit()
        await session.refresh(video)
        return video


async def update_published_video(video_id: int, **fields) -> None:
    session_maker = get_session_maker()
    async with session_maker() as session:
        video = await session.get(PublishedVideo, video_id)
        if video is None:
            return
        for k, v in fields.items():
            setattr(video, k, v)
        await session.commit()


async def mark_media_as_used(video_id: int, media_ids: List[int]) -> None:
    if not media_ids:
        return
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = (
            update(PublishedMedia)
            .where(PublishedMedia.id.in_(media_ids))
            .values(video_id=video_id)
        )
        await session.execute(stmt)
        await session.commit()


async def cleanup_old_media(days: int = 30) -> int:
    """Удаляет записи о медиа старше N дней."""
    session_maker = get_session_maker()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_maker() as session:
        stmt = (
            delete(PublishedMedia)
            .where(PublishedMedia.created_at < cutoff)
            .where(PublishedMedia.video_id.isnot(None))
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0