"""Prometheus metrics collectors."""

import logging
import re
from collections.abc import Callable

import sqlalchemy
from prometheus_client import REGISTRY, Gauge, make_asgi_app
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from spoolman.database import models

registry = REGISTRY

PREFIX = "spoolman"

SPOOL_PRICE = Gauge(f"{PREFIX}_spool_price", "Total Spool price", ["spool_id", "filament_id", "owner"])
SPOOL_USED_WEIGHT = Gauge(
    f"{PREFIX}_spool_weight_used", "Spool Used Weight in grams", ["spool_id", "filament_id", "owner"]
)
SPOOL_INITIAL_WEIGHT = Gauge(
    f"{PREFIX}_spool_initial_weight",
    "Spool Net weight in grams",
    ["spool_id", "filament_id", "owner"],
)
FILAMENT_INFO = Gauge(
    f"{PREFIX}_filament_info",
    "Filament information",
    ["filament_id", "vendor", "name", "material", "color", "owner"],
)
FILAMENT_DENSITY = Gauge(f"{PREFIX}_filament_density", "Density of filament gram/cm3", ["filament_id", "owner"])
FILAMENT_DIAMETER = Gauge(f"{PREFIX}_filament_diameter", "Diameter of filament", ["filament_id", "owner"])
FILAMENT_WEIGHT = Gauge(f"{PREFIX}_filament_weight", "Net weight of filament", ["filament_id", "owner"])

logger = logging.getLogger(__name__)


def make_metrics_app() -> Callable:
    """Start ASGI prometheus app with global registry."""
    logger.info("Start metrics app")
    return make_asgi_app(registry=registry)


metrics_app = make_asgi_app()

# The label sets each gauge was last given, so the next refresh knows which ones are gone.
_published: dict[Gauge, set[tuple]] = {}


def _publish(gauge: Gauge, values: dict[tuple, float]) -> None:
    """Make a gauge hold exactly these label sets, dropping any left from an earlier refresh.

    Without the drop, an archived or deleted spool (or a deleted filament) kept being exported
    until restart. The new values are set before the stale ones are removed rather than
    clearing first: /metrics is served from a worker thread, so a scrape can land mid-refresh,
    and this way it never sees a series that is still live go missing.
    """
    for labels, value in values.items():
        gauge.labels(*labels).set(value)
    for labels in _published.get(gauge, set()) - values.keys():
        gauge.remove(*labels)
    _published[gauge] = set(values)


async def _owner_labels(db: AsyncSession) -> dict[int, str]:
    """Map each user id to its `owner` label: the display name as a slug, `_<user_id>` added on a collision.

    The lowest user id keeps the bare slug, so a newcomer with the same name never renames an
    existing owner's series. The unclaimed placeholder is just a user named "Unclaimed data".
    """
    users = (
        await db.execute(sqlalchemy.select(models.User.id, models.User.display_name).order_by(models.User.id))
    ).all()
    labels: dict[int, str] = {}
    for user_id, display_name in users:
        slug = re.sub(r"[^a-z0-9_]", "_", display_name.lower())
        labels[user_id] = f"{slug}_{user_id}" if slug in labels.values() else slug
    return labels


async def spool_metrics(db: AsyncSession) -> None:
    """Get metrics by Spools from DB and write to prometheus.

    Args:
        db: async db session

    """
    stmt = sqlalchemy.select(models.Spool).where(
        sqlalchemy.or_(
            models.Spool.archived.is_(False),
            models.Spool.archived.is_(None),
        ),
    )
    owners = await _owner_labels(db)
    rows = await db.execute(stmt)
    result = list(rows.unique().scalars().all())
    price: dict[tuple, float] = {}
    initial_weight: dict[tuple, float] = {}
    used_weight: dict[tuple, float] = {}
    for row in result:
        labels = (str(row.id), str(row.filament_id), owners[row.owner_id])
        if row.price is not None:
            price[labels] = row.price
        if row.initial_weight is not None:
            initial_weight[labels] = row.initial_weight
        used_weight[labels] = row.used_weight
    _publish(SPOOL_PRICE, price)
    _publish(SPOOL_INITIAL_WEIGHT, initial_weight)
    _publish(SPOOL_USED_WEIGHT, used_weight)


async def filament_metrics(db: AsyncSession) -> None:
    """Get metrics and info by Filaments from DB and write to prometheus.

    Args:
        db: async db session

    """
    stmt = (
        sqlalchemy.select(models.Filament)
        .options(contains_eager(models.Filament.vendor))
        .join(models.Filament.vendor, isouter=True)
    )
    owners = await _owner_labels(db)
    rows = await db.execute(stmt)
    result = list(rows.unique().scalars().all())
    info: dict[tuple, float] = {}
    density: dict[tuple, float] = {}
    diameter: dict[tuple, float] = {}
    weight: dict[tuple, float] = {}
    for row in result:
        owner = owners[row.owner_id]
        vendor_name = "-"
        if row.vendor is not None:
            vendor_name = row.vendor.name
        info[(str(row.id), vendor_name, row.name, row.material, row.color_hex, owner)] = 1
        density[(str(row.id), owner)] = row.density
        diameter[(str(row.id), owner)] = row.diameter
        if row.weight is not None:
            weight[(str(row.id), owner)] = row.weight
    _publish(FILAMENT_INFO, info)
    _publish(FILAMENT_DENSITY, density)
    _publish(FILAMENT_DIAMETER, diameter)
    _publish(FILAMENT_WEIGHT, weight)
