"""
База данных: async SQLAlchemy + PostgreSQL.

Модель Product хранит результаты парсинга.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List
import logging

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Integer, String, Text,
    func, select, desc, UniqueConstraint, update, delete, text
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import insert


logger = logging.getLogger(__name__)


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
        UniqueConstraint("sku", "marketplace", name="uq_sku_marketplace"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Источник
    url: Mapped[str] = mapped_column(Text, nullable=False)
    share_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

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
    __tablename__ = "published_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    marketplace: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True,)
    product_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    price_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_with_plus: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_original_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rating_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    legal_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # === Промокод ===
    promo_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    promo_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

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


class PublishedMessage(Base):
    """Сообщения, опубликованные в Telegram — со ссылкой для отслеживания."""
    __tablename__ = "published_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_link: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    marketplace: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
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


def get_session_maker() -> async_sessionmaker:
    if _session_maker is None:
        init_engine()
    return _session_maker


async def save_product(data: dict) -> int:
    """
    Сохранить результат парсинга. Возвращает id.

    Уникальность: (sku, marketplace).
      • Если товар с таким (sku, marketplace) уже есть — обновляем его
        (цена, рейтинг, наличие, URL и т.д.) и возвращаем id.
      • Если нет — вставляем новую запись.
      • Если sku пустой — fallback на URL (чтобы не терять товары без артикула).
    """
    session_maker = get_session_maker()
    async with session_maker() as session:
        sku = data.get("sku")
        marketplace = data.get("marketplace", "unknown")
        url = data.get("url", "")

        if sku:
            sku_str = str(sku).strip()
        else:
            # Нет артикула — используем url как ключ (редкий случай)
            sku_str = None

        # === Находим существующий товар по (sku, marketplace) ===
        existing_id = None
        if sku_str:
            row = (await session.execute(
                select(Product.id)
                .where(Product.sku == sku_str)
                .where(Product.marketplace == marketplace)
                .limit(1)
            )).first()
            if row is not None:
                existing_id = row[0]

        # Если артикула нет — ищем по URL (старое поведение)
        if existing_id is None and not sku_str:
            row = (await session.execute(
                select(Product.id)
                .where(Product.url == url)
                .where(Product.marketplace == marketplace)
                .limit(1)
            )).first()
            if row is not None:
                existing_id = row[0]

        # === Если нашли — обновляем ===
        if existing_id is not None:
            await session.execute(
                update(Product)
                .where(Product.id == existing_id)
                .values(
                    url=url,
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
                    created_at=func.now(),
                )
            )
            logger.info(
                "save_product: обновлён id=%d (sku=%s, mp=%s)",
                existing_id, sku_str, marketplace,
            )

            history = PriceHistory(
                product_id=existing_id,
                price_rub=data.get("price_rub"),
                price_original_rub=data.get("price_original_rub"),
                price_with_plus=data.get("price_with_plus"),
                in_stock=data.get("in_stock"),
            )
            session.add(history)
            await session.commit()
            return existing_id

        # === Иначе — вставляем ===
        stmt = insert(Product).values(
            url=url,
            marketplace=marketplace,
            sku=sku_str,
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

        logger.info(
            "save_product: создан id=%d (sku=%s, mp=%s, url=%s)",
            product_id, sku_str, marketplace, url[:80],
        )
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


async def create_tables():
    """Только для тестов. В prod используем Alembic (`alembic upgrade head`)."""
    engine = init_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
    marketplace: Optional[str] = None,
    title: Optional[str] = None,
    brand: Optional[str] = None,
    price_rub: Optional[float] = None,
    price_with_plus: Optional[float] = None,
    price_original_rub: Optional[float] = None,
    rating: Optional[float] = None,
    rating_count: Optional[int] = None,
    legal_text: Optional[str] = None,
    promo_code: Optional[str] = None,          # <-- NEW
    promo_percent: Optional[int] = None,       # <-- NEW
    post_text: Optional[str] = None,
    image_url: Optional[str] = None,
    product_id: Optional[int] = None,
) -> int:
    session_maker = get_session_maker()
    async with session_maker() as session:
        media = PublishedMedia(
            source=source,
            marketplace=marketplace,
            product_id=product_id,
            title=title,
            brand=brand,
            price_rub=price_rub,
            price_with_plus=price_with_plus,
            price_original_rub=price_original_rub,
            rating=rating,
            rating_count=rating_count,
            legal_text=legal_text,
            promo_code=promo_code,
            promo_percent=promo_percent,
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


async def cleanup_old_media(days: int = 30) -> int:
    """Удаляет старые записи + файлы. Возвращает число удалённых записей."""
    from services.media_storage import delete_photo  # локальный импорт, чтобы не было цикла

    session_maker = get_session_maker()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    async with session_maker() as session:
        # Сначала соберём пути к файлам
        stmt = (
            select(PublishedMedia.image_path)
            .where(PublishedMedia.created_at < cutoff)
            .where(PublishedMedia.video_id.isnot(None))
        )
        rows = await session.execute(stmt)
        paths = [r[0] for r in rows.all() if r[0]]

        # Удаляем записи
        del_stmt = (
            delete(PublishedMedia)
            .where(PublishedMedia.created_at < cutoff)
            .where(PublishedMedia.video_id.isnot(None))
        )
        result = await session.execute(del_stmt)
        await session.commit()

    # Удаляем файлы (после коммита — если что-то упадёт, хотя бы БД чистая)
    for p in paths:
        delete_photo(p)

    return result.rowcount or 0


async def delete_media_records(media_ids: List[int]) -> int:
    """Удаляет записи о медиа из БД по списку id."""
    if not media_ids:
        return 0
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = delete(PublishedMedia).where(PublishedMedia.id.in_(media_ids))
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def cleanup_old_referral_publications(days: int = 30) -> int:
    """Удаляет старые записи о публикациях (по умолчанию 30 дней),
    чтобы товары снова попали в ротацию."""
    session_maker = get_session_maker()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with session_maker() as session:
        stmt = (
            delete(PublishedReferralProduct)
            .where(PublishedReferralProduct.published_at < cutoff)
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def product_exists(sku: str, marketplace: str) -> bool:
    """
    Проверяет, есть ли товар в products по (sku, marketplace).

    Возвращает True, если товар уже в БД.
    Используется consumer'ом, чтобы не публиковать дубли.
    """
    if not sku:
        return False
    sku_str = str(sku).strip()
    if not sku_str:
        return False

    session_maker = get_session_maker()
    async with session_maker() as session:
        row = (await session.execute(
            select(Product.id)
            .where(Product.sku == sku_str)
            .where(Product.marketplace == marketplace)
            .limit(1)
        )).first()
        return row is not None


async def save_published_message(
    chat_id: str,
    message_id: int,
    message_link: str,
    source_url: Optional[str] = None,
    marketplace: Optional[str] = None,
    title: Optional[str] = None,
    product_id: Optional[int] = None,
) -> int:
    session_maker = get_session_maker()
    async with session_maker() as session:
        msg = PublishedMessage(
            chat_id=str(chat_id),
            message_id=message_id,
            message_link=message_link,
            source_url=source_url,
            marketplace=marketplace,
            title=title,
            product_id=product_id,
        )
        session.add(msg)
        await session.commit()
        await session.refresh(msg)
        return msg.id


async def list_published_messages(
    limit: int = 50,
    offset: int = 0,
    marketplace: Optional[str] = None,
    only_ozon_short: bool = False,
) -> tuple[List[PublishedMessage], int]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        stmt = select(PublishedMessage)

        if marketplace:
            stmt = stmt.where(PublishedMessage.marketplace == marketplace)

        if only_ozon_short:
            stmt = stmt.where(PublishedMessage.marketplace == "ozon")
            stmt = stmt.where(
                PublishedMessage.source_url.like("https://ozon.ru/t/%")
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(desc(PublishedMessage.created_at))
        stmt = stmt.offset(offset).limit(limit)

        result = await session.execute(stmt)
        return list(result.scalars().all()), total


async def get_published_message(msg_id: int) -> Optional[PublishedMessage]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        return await session.get(PublishedMessage, msg_id)


async def delete_published_message(msg_id: int) -> bool:
    session_maker = get_session_maker()
    async with session_maker() as session:
        msg = await session.get(PublishedMessage, msg_id)
        if msg is None:
            return False
        await session.delete(msg)
        await session.commit()
        return True


async def cleanup_orphan_photos() -> int:
    """
    Удаляет из media/photos все файлы, на которые нет ссылок в БД.
    Возвращает число удалённых файлов.
    """
    from pathlib import Path
    from services.media_storage import PHOTOS_DIR

    session_maker = get_session_maker()
    async with session_maker() as session:
        rows = (await session.execute(
            select(PublishedMedia.image_path)
        )).all()
        used = {r[0] for r in rows if r[0]}

    photos_dir = Path(PHOTOS_DIR)
    if not photos_dir.exists():
        return 0

    removed = 0
    for f in photos_dir.iterdir():
        if not f.is_file():
            continue
        if str(f) in used:
            continue
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    return removed