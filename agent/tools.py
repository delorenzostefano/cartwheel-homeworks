"""Homework 1: the remaining commerce-agent tools.

The three lecture tools (`search_help_center`, `get_order`, `issue_refund`)
are implemented in agent/agent.py and are worked examples of the pattern:
check permissions first, go through agent/db.py for data, and return a
structured dict, never a prose error. The homework tools follow the same
pattern. agent/agent.py already wraps each function below as an SDK tool, so
once a function works here it works in chat with no further wiring.

Result convention (see agent/auth.py):
  - Success: a dict with "ok": True plus the payload fields named in each
    docstring.
  - Failure: {"ok": False, "error": <code>, "reason": <human-readable str>}.

Run the contract tests with: uv run pytest tests/test_hw_holes.py -k hw1
They are marked xfail and flip to passing as you implement each function.
"""

from __future__ import annotations

from typing import Any

from agent import db
from agent.auth import AuthContext, can_cancel_order, permission_denied, can_view_order
from agent.helpcenter import load_policy_docs
from agent.killswitch import kill_switch
from agent.config import load_facts
from datetime import timedelta
from seed.eligibility import effective_return_window_days, is_refund_eligible

MAX_SEARCH_LIMIT = 25
DEFAULT_ORDER_LIMIT = 20


def get_policy(ctx: AuthContext, policy_id: str) -> dict[str, Any]:
    """Fetch one policy doc by its exact id. Risk tier: read.

    Every role may read every policy doc (the corpus is public help-center
    content), so this tool needs no permission check.

    Args:
        ctx: The caller's auth context. Unused here, but every tool takes it.
        policy_id: An exact policy id, e.g. "cw-returns" or
            "store-juniper-home-goods-policy". Matching is exact and
            case-sensitive; ids are the `policy_id` front-matter field of the
            files in data/policies/.

    Returns:
        On success: {"ok": True, "policy_id": str, "title": str,
        "audience": str, "body": str} where body is the markdown body of the
        doc without the front matter.
        If no doc has that id: {"ok": False, "error": "not_found",
        "reason": ...} naming the id that was requested.

    Implementation notes:
        agent.helpcenter.load_policy_docs() returns every parsed doc.
    """
    for doc in load_policy_docs():
        if doc.policy_id == policy_id:
            return {
                "ok": True,
                "policy_id": doc.policy_id,
                "title": doc.title,
                "audience": doc.audience,
                "body": doc.body
            }
    return {
        "ok": False, 
        "error": "not_found",
        "reason": f"No policy doc with id {policy_id!r}"
    }


def search_products(
    ctx: AuthContext,
    query: str,
    store: str | None = None,
    max_price_usd: float | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search the product catalog. Risk tier: read.

    Every role may search products. Matching is deterministic keyword
    matching, not semantic search: a product matches when every whitespace
    token of `query` appears case-insensitively as a substring of the
    product's title or description.

    Args:
        ctx: The caller's auth context.
        query: Free-text query. Must be non-empty after stripping whitespace;
            otherwise return {"ok": False, "error": "invalid_argument",
            "reason": ...}.
        store: Optional store filter. Matched with
            agent.db.get_store_by_name (case-insensitive name or slug). If
            given and no store matches, return {"ok": False, "error":
            "not_found", "reason": ...} naming the store string.
        max_price_usd: Optional inclusive price ceiling. If given and not
            strictly positive, return an "invalid_argument" error.
        limit: Maximum products to return. Clamp to the range
            [1, MAX_SEARCH_LIMIT]; do not error on out-of-range values.

    Returns:
        {"ok": True, "products": [...], "count": <len(products)>} where each
        product is {"product_id": int, "store_id": int, "title": str,
        "price_usd": float}. Sort matches by price_usd ascending, then by
        product_id ascending, and truncate to `limit`. No matches is still a
        success: {"ok": True, "products": [], "count": 0}.

    Implementation notes:
        agent.db.list_products(conn, store_id) gives the candidate set.
        Use `with db.connection() as conn:` to close the database automatically.
    """
    tokens = query.lower().split()
    if not tokens:
        return {
            "ok": False, 
            "error": "invalid_argument",
            "reason": "query must be non-empty"
        }
    if max_price_usd is not None and max_price_usd <= 0:
        return {
            "ok": False, 
            "error": "invalid_argument",
            "reason": f"max_price_usd must be positive, got {max_price_usd!r}"
        }
    limit = max(1, min(limit, MAX_SEARCH_LIMIT))

    conn = db.connect()
    try:
        store_id: int | None = None
        if store is not None:
            found = db.get_store_by_name(conn, store)
            if found is None:
                return {
                    "ok": False, 
                    "error": "not_found",
                    "reason": f"No store matches {store!r}"
                }
            store_id = found.id
        candidates = db.list_products(conn, store_id)
    finally:
        conn.close()

    matches = [
        p
        for p in candidates
        if all(t in f"{p.title} {p.description}".lower() for t in tokens)
        and (max_price_usd is None or p.price_usd <= max_price_usd)
    ]
    matches.sort(key=lambda p: (p.price_usd, p.id))
    products = [
        {
            "product_id": p.id,
            "store_id": p.store_id,
            "title": p.title,
            "price_usd": p.price_usd,
        }
        for p in matches[:limit]
    ]
    return {
        "ok": True, 
        "products": products, 
        "count": len(products)
    }


def list_my_orders(ctx: AuthContext) -> dict[str, Any]:
    """List recent orders in the caller's own scope. Risk tier: read.

    Role behavior, straight from the access matrix in SPEC.md:
        - shopper: the caller's own orders.
        - merchant: the caller's store's orders (ctx.store_id).
        - support: support staff have no orders of their own and look up
          specific orders with get_order instead, so return {"ok": False,
          "error": "invalid_argument", "reason": ...} saying exactly that.

    Returns:
        For shopper and merchant: {"ok": True, "orders": [...],
        "count": <len(orders)>} where each order is
        agent.db.Order.to_public_dict() and the list holds at most
        DEFAULT_ORDER_LIMIT orders, newest first (agent.db.list_orders_for_user
        and list_orders_for_store already sort and limit this way).

    Implementation notes:
        No permission check is needed beyond the role dispatch, because the
        scope is baked into which query you run. That is the point of the
        tool: the model cannot ask for someone else's orders through it.
    """
    if ctx.role == "support":
        return {
            "ok": False,
            "error": "invalid_argument", 
            "reason": "support staff have no order of their own; use get_order"
        }
    conn = db.connect()
    try:
        if ctx.role == "shopper":
            orders = db.list_orders_for_user(conn, ctx.user_id, DEFAULT_ORDER_LIMIT)
        else:
            orders = db.list_orders_for_store(conn, ctx.store_id, DEFAULT_ORDER_LIMIT)
    finally:
        conn.close()

    public = [o.to_public_dict()  for o in orders]

    return {
        "ok": True, 
        "orders": public,
        "count": len(public)
    }


def cancel_order(ctx: AuthContext, order_id: int, reason: str) -> dict[str, Any]:
    """Cancel an order. Risk tier: write.

    This is the homework's write tool, and it must enforce two independent
    rules in this order:

    1. The access matrix (scope): use agent.auth.can_cancel_order. Shoppers
       may cancel only their own orders, merchants only their own store's
       orders, support any order. On failure return
       agent.auth.permission_denied(...) with a reason naming the role and
       the order id. Scope is checked before the status rule so that an
       out-of-scope caller learns nothing about the order's state.
    2. The pre-shipment rule (facts.yaml `cancel_cutoff`): only orders whose
       status is exactly "placed" can be cancelled, for every role. If the
       order is in scope but its status is not "placed", return
       {"ok": False, "error": "not_eligible", "reason": ...} that names the
       current status and states that orders can be cancelled only before
       shipment.

    Args:
        ctx: The caller's auth context.
        order_id: The order to cancel.
        reason: Free-text reason from the user; not validated.

    Returns:
        If no order has this id: {"ok": False, "error": "not_found",
        "reason": ...}.
        On success: {"ok": True, "order_id": order_id, "status": "cancelled"}
        after persisting the new status with agent.db.set_order_status.

    Implementation notes:
        Fetch with agent.db.get_order. Note the argument order of
        can_cancel_order(ctx, order_user_id, order_store_id).

    The Module 4 kill switch is checked first (before the scope and
    status rules and before your code), so that a paused write tool touches
    nothing. It is provided; the default ("off") returns None and falls
    through to your implementation.
    """
    paused = kill_switch("cancel_order")
    if paused is not None:
        return {"ok": False, "error": "paused", "reason": paused}

    conn = db.connect()
    try:
        order = db.get_order(conn, order_id)
        if order is None:
            return {
                "ok": False,
                "error": "not_found",
                "reason": f"No order with id {order_id}"
            }
        if not can_cancel_order(ctx, order.user_id, order.store_id):
            return permission_denied (
                f"{ctx.role} {ctx.user_id} may not cancel order {order_id}"
            )

        if order.status != "placed":
            return {
                "ok": False,
                "error": "not_eligible",
                "reason": (
                    f"Ordwe {order_id} is {order.status};"
                    "orders can be cancelled only before shipment"
                )
            }

        db.set_order_status(conn, order_id, "cancelled")
    finally:
        conn.close()
    return{
        "ok": True,
        "order_id": order_id,
        "status": "cancelled"
    }


def find_order(ctx: AuthContext, query: str) -> dict[str, Any]:
    """Search the caller's orders by product name. Risk tier: read.

    Takes a natural-language query (e.g., "earmuffs I bought last week")
    and searches the authenticated user's orders for products whose name
    matches. Use fuzzy string matching (e.g., thefuzz.fuzz.partial_ratio
    or case-insensitive substring matching) to find orders whose product name is close to the
    query.

    Access rules: a shopper searches only the shopper's own orders, a
    merchant searches orders from the merchant's store, and support staff
    can search any orders. Use agent.db.list_order_search_candidates with
    user_id=ctx.user_id for shoppers, store_id=ctx.store_id for merchants,
    or all_orders=True only for support. Derive the scope from ctx, never
    from the query; reject unsupported roles or missing required identity.
    Use agent.db.list_products to map product IDs to product titles.

    The helper returns the complete authorised scope, newest first with
    order ID descending as the tie-breaker. Match product names first,
    preserve that order, then return at most five matches. Do not search
    only the 20 most recent orders. Convert matches with to_public_dict().

    Args:
        ctx: The caller's auth context.
        query: A natural-language description of the product.

    Returns:
        {"ok": True, "orders": [...]} with a list of matching orders
        (at most 5), each as the dict returned by agent.db. If no orders
        match, return {"ok": True, "orders": []}.
    """
    tokens = query.lower().split()
    if not tokens:
        return {
            "ok": True,
            "orders": []
        }

    if ctx.role == "shopper":
        where, params = "o.user_id = ?", (ctx.user_id,)
    elif ctx.role == "merchant":
        where, params = "o.store_id = ?", (ctx.store_id,)
    else:
        where, params = "1 = 1", ()

    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT o.*, p.title AS product_title "
            "FROM orders o JOIN products p ON o.product_id = p.id "
            f"WHERE {where} ORDER BY o.ordered_at DESC, o.id DESC ",
            params,
        ).fetchall()
    finally:
        conn.close()

    matches = []
    for row in rows:
        title = row["product_title"].lower()
        if all(t in title for t in tokens):
            order = db._order_from_row(row)
            matches.append(order.to_public_dict())
        if len(matches) == 5:
            break
    return {
        "ok": True,
        "orders": matches
    }


def check_return_eligibility(ctx: AuthContext, order_id: int) -> dict[str, Any]:
    """Say whether an order can still be returned for a refund. Risk tier: read."""
    conn = db.connect()
    try:
        order = db.get_order(conn, order_id)
        if order is None:
            return {
                "ok": False,
                "error": "not_found",
                "reason": f"No order with id {order_id}"
            }
        if not can_view_order(ctx, order.user_id, order.store_id):
            return permission_denied(
                f"{ctx.role} {ctx.user_id} may not view order {order_id}"
            )
        store = db.get_store(conn, order.store_id)
        today = db.world_asof(conn)
    finally:
        conn.close()

    facts = load_facts()
    window = effective_return_window_days(
        facts["return_window_days"],
        store.return_window_days_override if store else None
    )
    eligible = is_refund_eligible(
        status=order.status,
        delivered_at=order.delivered_at,
        as_of=today,
        return_window_days=window
    )
    window_ends = None
    days_left = None
    if order.delivered_at is not None:
        window_ends = order.delivered_at + timedelta(days=window)
        days_left = (window_ends - today).days
    fee_pct = facts["restocking_fee_max_percent"] if (store and store.restocking_fee_opt_in) else 0
    return {
        "ok": True,
        "order_id": order_id,
        "eligible": eligible,
        "status": order.status,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "return_window_days": window,
        "window_ends": window_ends.isoformat() if window_ends else None,
        "days_left": days_left,
        "restocking_fee_max_percent": fee_pct,
        "restocking_fee_opened_items_only": facts["restocking_fee_opened_items_only"],
    }


def track_shipment(ctx: AuthContext, order_id: int) -> dict[str, Any]:
    """Report shipment status and expected dates for an order. Risk tier: read."""
    conn = db.connect()
    try:
        order = db.get_order(conn, order_id)
        if order is None:
            return {
                "ok": False,
                "error": "not_found",
                "reason": f"No order with id {order_id}"
            }
        if not can_view_order(ctx, order.user_id, order.store_id):
            return permission_denied(
                f"{ctx.role} {ctx.user_id} may not view order {order_id}"
            )
        
        today = db.world_asof(conn)
    finally:
        conn.close()

    facts = load_facts()
    ship_by = order.ordered_at + timedelta(days=facts["shipping_handling_days_max"])
    deliver_by = ship_by + timedelta(days=facts["shipping_transit_days_max"])

    if order.status == "cancelled":
        stage = "cancelled"
    elif order.delivered_at is not None:
        stage = "delivered"
    elif order.shipped_at is not None:
        stage = "in_transit"
    else:
        stage = "awaiting_shipment"

    late = False
    if stage == "awaiting_shipment" and today > ship_by:
        late = True
    elif stage == "in_transit" and today > deliver_by:
        late = True

    return {
        "ok": True,
        "order_id": order_id,
        "stage": stage,
        "status": order.status,
        "ordered_at": order.ordered_at.isoformat(),
        "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "ship_by": ship_by.isoformat(),
        "deliver_by": deliver_by.isoformat(),
        "late": late,
        "as_of": today.isoformat()
    }

def get_store_info(ctx: AuthContext, store: str) -> dict[str, Any]:
    """Public store profile plus any policy overrides. Risk tier: read."""
    conn = db.connect()
    try:
        found = db.get_store_by_name(conn, store)
    finally:
        conn.close()
    if found is None:
        return {
            "ok": False,
            "error": "not_found",
            "reason": f"No store matches {store!r}"
        }
    facts = load_facts()
    window = effective_return_window_days(
        facts["return_window_days"],
        found.return_window_days_override
    )
    return {
        "ok": True,
        "store_id": found.id,
        "name": found.name,
        "slug": found.slug,
        "category": found.category,
        "return_window_days": window,
        "return_window_is_override": found.return_window_days_override is not None,
        "restocking_fee_opt_in": found.restocking_fee_opt_in,
        "restocking_fee_max_percent": facts["restocking_fee_max_percent"] if found.restocking_fee_opt_in else 0
    }

def order_history_summary(ctx: AuthContext) -> dict[str, Any]:
    """Aggregate the caller's (or their store's) recent orders. Risk tier: read."""
    if ctx.role == "support":
        return {
            "ok": False,
            "error": "invalid_argument",
            "reason": "support staff have no orders of their own; use get_order"
        }
    conn = db.connect()
    try:
        if ctx.role == "shopper":
            orders = db.list_orders_for_user(conn, ctx.user_id, DEFAULT_ORDER_LIMIT)
        else:
            orders = db.list_orders_for_store(conn, ctx.store_id, DEFAULT_ORDER_LIMIT)
    finally:
        conn.close()
    by_status: dict[str, int] = {}
    total_cents = 0
    for o in orders:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        if o.status != "cancelled":
            total_cents += o.total_cents
    return {
        "ok": True,
        "scope": "user" if ctx.role == "shopper" else "store",
        "order_count": len(orders),
        "by_status": by_status,
        "total_spent_usd": total_cents / 100,
        "first_order_at": orders[-1].ordered_at.isoformat() if orders else None,
        "last_order_at": orders[0].ordered_at.isoformat() if orders else None,
        "refund_eligible_count": sum(1 for o in orders if o.refund_eligible),
    }


def get_product(ctx: AuthContext, product_id: int) -> dict[str, Any]:
    """Full detail for one catalog product. Risk tier: read."""
    conn = db.connect()
    try:
        product = db.get_product(conn, product_id)
        store = db.get_store(conn, product.store_id) if product else None
    finally:
        conn.close()
    if product is None:
        return {"ok": False, "error": "not_found", "reason": f"No product with id {product_id}"}
    return {
        "ok": True,
        "product_id": product.id,
        "title": product.title,
        "description": product.description,
        "category": product.category,
        "price_usd": product.price_usd,
        "store_id": product.store_id,
        "store_name": store.name if store else None,
    }


def dispute_window(ctx: AuthContext, order_id: int) -> dict[str, Any]:
    """Whether a charge on this order can still be disputed. Risk tier: read."""
    conn = db.connect()
    try:
        order = db.get_order(conn, order_id)
        if order is None:
            return {"ok": False, "error": "not_found", "reason": f"No order with id {order_id}"}
        if not can_view_order(ctx, order.user_id, order.store_id):
            return permission_denied(
                f"{ctx.role} {ctx.user_id} may not view order {order_id}"
            )
        today = db.world_asof(conn)
    finally:
        conn.close()

    days = load_facts()["dispute_window_days"]
    if order.delivered_at is None:
        return {
            "ok": True,
            "order_id": order_id,
            "disputable": False,
            "dispute_window_days": days,
            "window_ends": None,
            "days_left": None,
            "reason": f"order is {order.status}, not delivered yet; window starts at delivery",
        }
    window_ends = order.delivered_at + timedelta(days=days)
    days_left = (window_ends - today).days
    return {
        "ok": True,
        "order_id": order_id,
        "disputable": 0 <= days_left,
        "dispute_window_days": days,
        "delivered_at": order.delivered_at.isoformat(),
        "window_ends": window_ends.isoformat(),
        "days_left": days_left,
        "as_of": today.isoformat(),
    }
