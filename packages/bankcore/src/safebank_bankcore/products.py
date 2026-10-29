"""Product definitions and read-side projections (R14/R15/R17).

Pure functions – no IO, no BankState mutation.

Internal ``ProductDef`` carries fields not yet in the frozen contracts
(``base_nav``, ``base_date``).  These are *not* promoted to the shared
``ProductView`` by this module; the coordinator will promote them in a
separate task.

Ambiguities noted for coordinator:

- ``recent_return_bp``: the PRD says 「由 NAV 序列计算」 but does not define
  the lookback window or rounding rule.  This module exports an explicit
  ``recent_return_bp(product, on_date, window_trading_days)`` that requires
  the caller to supply the window.  Default omitted on purpose – do NOT guess.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .calendar import is_trading_day, previous_trading_day
from .nav import MIN_NAV
from .nav import nav as _nav

__all__ = [
    "ProductDef",
    "NavPoint",
    "list_products",
    "compare_products",
    "get_product",
    "recent_return_bp",
]

# ── internal dataclasses (pending future promotion to contracts) ──────────


@dataclass(frozen=True, slots=True)
class ProductDef:
    """Raw product definition – includes fields not yet in the shared ProductView."""

    id: str
    name: str
    product_type: str  # "MMF" | "BOND" | "MIXED" | "EQUITY"
    risk_level: int  # 1..5
    risk_label: str
    base_nav: Decimal
    base_date: date
    confirm_days: int
    settle_days: int
    min_amount_cents: int
    risk_disclosure: str
    disclosure_version: str


@dataclass(frozen=True, slots=True)
class NavPoint:
    """A single NAV history entry.  Mirrors contracts NavPoint shape."""

    date: date
    nav: Decimal


# ── helpers ───────────────────────────────────────────────────────────────


def _product_nav(product: ProductDef, on_date: date) -> Decimal:
    """NAV on *on_date*, following the non-trading-day fallback rule."""
    return _nav(product.base_nav, product.base_date, product.product_type, on_date)


def _effective_nav_date(product: ProductDef, on_date: date) -> date:
    """The date whose NAV is actually used for *on_date* (trading day or previous)."""
    if product.product_type == "MMF" or is_trading_day(on_date):
        return on_date
    return previous_trading_day(on_date)


# ── recent_return_bp (explicit parameters – no default window) ─────────────


# PRD 8.3: recent_return_bp uses the same 30-trading-day window as nav_history.
RECENT_RETURN_WINDOW_TRADING_DAYS = 30


def recent_return_bp(
    product: ProductDef,
    on_date: date,
    window_trading_days: int,
) -> int:
    """Return the return over the last *window_trading_days* trading days in bp.

    ``(nav_latest - nav_earliest) / nav_earliest * 10000``, rounded to int
    with ROUND_HALF_UP.  ``nav_earliest`` is *window_trading_days*
    trading days before *on_date* (exclusive: the window spans exactly
    *window_trading_days* trading-day steps forward from the start date).

    **Ambiguity (reported)**: The PRD does not specify the default window.
    Callers must supply *window_trading_days* explicitly.
    """
    # Find the trading day *window_trading_days* trading days before on_date.
    cur = _effective_nav_date(product, on_date)
    for _ in range(window_trading_days):
        cur = previous_trading_day(cur - date.resolution)
    earliest_nav = _product_nav(product, cur)
    latest_nav = _product_nav(product, on_date)
    if earliest_nav == 0:
        earliest_nav = MIN_NAV
    ratio = (latest_nav - earliest_nav) / earliest_nav
    return int((ratio * 10000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ── R14: list_products ────────────────────────────────────────────────────


def list_products(
    products: tuple[ProductDef, ...],
    *,
    max_risk_level: int | None = None,
    product_type: str | None = None,
) -> list[ProductDef]:
    """Filter products by optional risk cap and type.  No suitability filter
    is applied (per PRD: 「list 不默认做适当性过滤」).

    Returns products in definition order (stable).
    """
    result: list[ProductDef] = []
    for p in products:
        if max_risk_level is not None and p.risk_level > max_risk_level:
            continue
        if product_type is not None and p.product_type != product_type:
            continue
        result.append(p)
    return result


# ── R15: compare_products ─────────────────────────────────────────────────


def compare_products(
    products: tuple[ProductDef, ...],
    product_ids: tuple[str, ...],
    on_date: date,
) -> dict:
    """Compare 2–4 unique products.

    Returns a dict with keys ``items`` (list of product dicts) and
    ``comparison_fields`` (ordered field names).  Each product dict includes
    ``nav`` and ``nav_date`` computed for *on_date*.

    Raises ``ValueError`` on duplicate or missing product IDs.
    """
    if len(product_ids) < 2 or len(product_ids) > 4:
        raise ValueError("compare_products requires 2–4 product_ids")
    if len(set(product_ids)) != len(product_ids):
        raise ValueError("duplicate product_ids")

    by_id = {p.id: p for p in products}
    missing = [pid for pid in product_ids if pid not in by_id]
    if missing:
        raise ValueError(f"unknown product_ids: {missing}")

    comparison_fields = [
        "name",
        "risk_level",
        "risk_label",
        "product_type",
        "nav",
        "recent_return_bp",
        "min_amount_cents",
        "confirm_days",
        "settle_days",
    ]

    items: list[dict] = []
    for pid in product_ids:
        p = by_id[pid]
        p_nav = _product_nav(p, on_date)
        nav_date = _effective_nav_date(p, on_date)
        items.append(
            {
                "id": p.id,
                "name": p.name,
                "product_type": p.product_type,
                "risk_level": p.risk_level,
                "risk_label": p.risk_label,
                "nav": p_nav,
                "nav_date": nav_date,
                "recent_return_bp": recent_return_bp(
                    p, on_date, window_trading_days=RECENT_RETURN_WINDOW_TRADING_DAYS
                ),
                "confirm_days": p.confirm_days,
                "settle_days": p.settle_days,
                "min_amount_cents": p.min_amount_cents,
                "risk_disclosure": p.risk_disclosure,
                "disclosure_version": p.disclosure_version,
            }
        )
    return {"items": items, "comparison_fields": comparison_fields}


# ── R17: get_product ──────────────────────────────────────────────────────


def get_product(
    products: tuple[ProductDef, ...],
    product_id: str,
    on_date: date,
) -> dict | None:
    """Return a single product with full 30-trading-day NAV history.

    Returns ``None`` when *product_id* is unknown.
    """
    by_id = {p.id: p for p in products}
    p = by_id.get(product_id)
    if p is None:
        return None

    p_nav = _product_nav(p, on_date)
    nav_date = _effective_nav_date(p, on_date)

    # Build 30-trading-day history
    from .nav import nav_history as _nav_history

    history = _nav_history(p.base_nav, p.base_date, p.product_type, on_date, count=30)
    nav_history_list = [{"date": d, "nav": str(n)} for d, n in history]

    return {
        "id": p.id,
        "name": p.name,
        "product_type": p.product_type,
        "risk_level": p.risk_level,
        "risk_label": p.risk_label,
        "nav": p_nav,
        "nav_date": nav_date,
        "recent_return_bp": recent_return_bp(
            p, on_date, window_trading_days=RECENT_RETURN_WINDOW_TRADING_DAYS
        ),
        "confirm_days": p.confirm_days,
        "settle_days": p.settle_days,
        "min_amount_cents": p.min_amount_cents,
        "risk_disclosure": p.risk_disclosure,
        "disclosure_version": p.disclosure_version,
        "nav_history": nav_history_list,
    }
