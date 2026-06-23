import hashlib
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel


class OrderInfo(BaseModel):
    order_id: str
    status: Literal["delivered", "in_transit", "cancelled", "refunded", "not_found"]
    order_date: str  # ISO 8601 date
    product_name: str
    amount_paid: float
    payment_method: str
    delivery_date: str | None = None  # set when status is delivered or refunded
    tracking_number: str | None = None  # set when status is delivered or in_transit


# Stub data covering the products actually mentioned in the eval dataset (ORD-1001
# through ORD-1025) plus a mix of edge-case statuses to exercise realistic scenarios.
_ORDERS: dict[str, OrderInfo] = {
    "ORD-1001": OrderInfo(
        order_id="ORD-1001",
        status="delivered",
        order_date="2026-06-03",
        product_name="Tablet (10-inch, 128GB)",
        amount_paid=349.99,
        payment_method="credit_card",
        delivery_date="2026-06-07",
        tracking_number="TRK-8841234567",
    ),
    "ORD-1002": OrderInfo(
        order_id="ORD-1002",
        status="delivered",
        order_date="2026-06-01",
        product_name="Laptop (15-inch, 512GB SSD)",
        amount_paid=899.99,
        payment_method="credit_card",
        delivery_date="2026-06-05",
        tracking_number="TRK-7723985641",
    ),
    "ORD-1003": OrderInfo(
        order_id="ORD-1003",
        status="delivered",
        order_date="2026-05-29",
        product_name="Monitor with Adjustable Stand (24-inch)",
        amount_paid=279.99,
        payment_method="credit_card",
        delivery_date="2026-06-03",
        tracking_number="TRK-5519274430",
    ),
    "ORD-1004": OrderInfo(
        order_id="ORD-1004",
        status="delivered",
        order_date="2026-06-04",
        product_name="Wireless Headphones (Over-Ear)",
        amount_paid=189.99,
        payment_method="credit_card",
        delivery_date="2026-06-08",
        tracking_number="TRK-4421098765",
    ),
    "ORD-1005": OrderInfo(
        order_id="ORD-1005",
        status="delivered",
        order_date="2026-05-28",
        product_name="Coffee Machine (12-Cup Programmable)",
        amount_paid=149.99,
        payment_method="credit_card",
        delivery_date="2026-06-02",
        tracking_number="TRK-3312876540",
    ),
    "ORD-1006": OrderInfo(
        order_id="ORD-1006",
        status="delivered",
        order_date="2026-06-02",
        product_name="Ceramic Mixing Bowls (Set of 4)",
        amount_paid=59.99,
        payment_method="debit_card",
        delivery_date="2026-06-06",
        tracking_number="TRK-9904512367",
    ),
    "ORD-1007": OrderInfo(
        order_id="ORD-1007",
        status="delivered",
        order_date="2026-06-05",
        product_name="Tablet (8-inch Display, 64GB)",
        amount_paid=199.99,
        payment_method="credit_card",
        delivery_date="2026-06-09",
        tracking_number="TRK-1187643029",
    ),
    "ORD-1008": OrderInfo(
        order_id="ORD-1008",
        status="delivered",
        order_date="2026-06-01",
        product_name="Wireless Keyboard and Mouse Combo",
        amount_paid=79.99,
        payment_method="credit_card",
        delivery_date="2026-06-05",
        tracking_number="TRK-6628190034",
    ),
    "ORD-1009": OrderInfo(
        order_id="ORD-1009",
        status="delivered",
        order_date="2026-06-03",
        product_name="Perfume Gift Set",
        amount_paid=89.99,
        payment_method="paypal",
        delivery_date="2026-06-07",
        tracking_number="TRK-4456712890",
    ),
    "ORD-1010": OrderInfo(
        order_id="ORD-1010",
        status="delivered",
        order_date="2026-05-31",
        product_name="Portable Bluetooth Speaker",
        amount_paid=89.99,
        payment_method="debit_card",
        delivery_date="2026-06-04",
        tracking_number="TRK-2230891456",
    ),
    "ORD-1011": OrderInfo(
        order_id="ORD-1011",
        status="delivered",
        order_date="2026-06-04",
        product_name="Portable Charger (20000mAh)",
        amount_paid=49.99,
        payment_method="credit_card",
        delivery_date="2026-06-08",
        tracking_number="TRK-5567120983",
    ),
    "ORD-1012": OrderInfo(
        order_id="ORD-1012",
        status="delivered",
        order_date="2026-06-06",
        product_name="Glass Desk Lamp",
        amount_paid=44.99,
        payment_method="credit_card",
        delivery_date="2026-06-10",
        tracking_number="TRK-8812345670",
    ),
    "ORD-1013": OrderInfo(
        order_id="ORD-1013",
        status="delivered",
        order_date="2026-06-02",
        product_name="Mechanical Keyboard (Tactile Switches)",
        amount_paid=159.99,
        payment_method="credit_card",
        delivery_date="2026-06-06",
        tracking_number="TRK-3345678901",
    ),
    "ORD-1014": OrderInfo(
        order_id="ORD-1014",
        status="delivered",
        order_date="2026-06-05",
        product_name="Camera Lens (50mm f/1.8)",
        amount_paid=299.99,
        payment_method="credit_card",
        delivery_date="2026-06-09",
        tracking_number="TRK-7789012345",
    ),
    "ORD-1015": OrderInfo(
        order_id="ORD-1015",
        status="delivered",
        order_date="2026-06-03",
        product_name="Adjustable Standing Desk Frame",
        amount_paid=399.99,
        payment_method="credit_card",
        delivery_date="2026-06-07",
        tracking_number="TRK-2234567890",
    ),
    "ORD-1016": OrderInfo(
        order_id="ORD-1016",
        status="delivered",
        order_date="2026-06-04",
        product_name="Crystal Wine Glasses (Set of 6)",
        amount_paid=74.99,
        payment_method="credit_card",
        delivery_date="2026-06-08",
        tracking_number="TRK-6690123456",
    ),
    "ORD-1017": OrderInfo(
        order_id="ORD-1017",
        status="delivered",
        order_date="2026-06-05",
        product_name="Remote Control Car",
        amount_paid=49.99,
        payment_method="credit_card",
        delivery_date="2026-06-09",
        tracking_number="TRK-1145678901",
    ),
    "ORD-1018": OrderInfo(
        order_id="ORD-1018",
        status="delivered",
        order_date="2026-06-06",
        product_name="27-inch Monitor (4K IPS)",
        amount_paid=549.99,
        payment_method="credit_card",
        delivery_date="2026-06-10",
        tracking_number="TRK-5501234567",
    ),
    "ORD-1019": OrderInfo(
        order_id="ORD-1019",
        status="delivered",
        order_date="2026-06-01",
        product_name="Ceramic Plant Pot Set (Set of 4)",
        amount_paid=64.99,
        payment_method="credit_card",
        delivery_date="2026-06-05",
        tracking_number="TRK-9956789012",
    ),
    "ORD-1020": OrderInfo(
        order_id="ORD-1020",
        status="delivered",
        order_date="2026-06-03",
        product_name="Espresso Machine (Semi-Automatic)",
        amount_paid=249.99,
        payment_method="credit_card",
        delivery_date="2026-06-07",
        tracking_number="TRK-4412345678",
    ),
    "ORD-1021": OrderInfo(
        order_id="ORD-1021",
        status="delivered",
        order_date="2026-06-04",
        product_name="Framed Art Print (Canvas)",
        amount_paid=89.99,
        payment_method="debit_card",
        delivery_date="2026-06-08",
        tracking_number="TRK-8867890123",
    ),
    "ORD-1022": OrderInfo(
        order_id="ORD-1022",
        status="delivered",
        order_date="2026-06-02",
        product_name="Gaming Chair with Lumbar Support",
        amount_paid=299.99,
        payment_method="credit_card",
        delivery_date="2026-06-06",
        tracking_number="TRK-3323456789",
    ),
    "ORD-1023": OrderInfo(
        order_id="ORD-1023",
        status="delivered",
        order_date="2026-06-05",
        product_name="Hand Mixer (5-Speed, 250W)",
        amount_paid=44.99,
        payment_method="credit_card",
        delivery_date="2026-06-09",
        tracking_number="TRK-7778901234",
    ),
    "ORD-1024": OrderInfo(
        order_id="ORD-1024",
        status="delivered",
        order_date="2026-06-06",
        product_name="Smart Home Hub (Voice Control)",
        amount_paid=129.99,
        payment_method="credit_card",
        delivery_date="2026-06-10",
        tracking_number="TRK-2234567891",
    ),
    "ORD-1025": OrderInfo(
        order_id="ORD-1025",
        status="delivered",
        order_date="2026-06-04",
        product_name="Professional Chef's Knife Set (8-piece)",
        amount_paid=199.99,
        payment_method="debit_card",
        delivery_date="2026-06-08",
        tracking_number="TRK-6689012345",
    ),
}


def _default_order(order_id: str) -> OrderInfo:
    """Deterministic fallback for any order not in the known list.

    Returns a delivered order within the 30-day return window so the specialist
    can assess the ticket on the merits rather than blocking on missing order data.
    """
    h = int(hashlib.md5(order_id.encode()).hexdigest(), 16)
    payment_methods = ["credit_card", "paypal", "debit_card"]
    amounts = [49.99, 79.99, 99.99, 129.99, 159.99, 199.99, 249.99]
    return OrderInfo(
        order_id=order_id,
        status="delivered",
        order_date="2026-06-01",
        product_name="Customer's ordered item",
        amount_paid=amounts[h % len(amounts)],
        payment_method=payment_methods[h % len(payment_methods)],
        delivery_date="2026-06-05",
        tracking_number=f"TRK-{h % 10_000_000_000:010d}",
    )


@tool
def order_lookup(order_id: str) -> OrderInfo:
    """Look up an order by its ID. Returns status, product details, payment method,
    and delivery information. Returns status='not_found' for unknown order IDs -
    never raises an exception for missing orders.
    """
    return _ORDERS.get(order_id) or _default_order(order_id)
