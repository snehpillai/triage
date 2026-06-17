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


# Stub data. Extend this dict to add more test orders.
# A mix of statuses so specialists encounter realistic scenarios.
_ORDERS: dict[str, OrderInfo] = {
    "ORD-1001": OrderInfo(
        order_id="ORD-1001",
        status="delivered",
        order_date="2026-03-12",
        product_name="Adjustable Aluminum Laptop Stand",
        amount_paid=49.99,
        payment_method="credit_card",
        delivery_date="2026-03-15",
        tracking_number="TRK-8841234567",
    ),
    "ORD-1002": OrderInfo(
        order_id="ORD-1002",
        status="delivered",
        order_date="2026-02-20",
        product_name="Wireless Ergonomic Keyboard",
        amount_paid=89.99,
        payment_method="paypal",
        delivery_date="2026-02-24",
        tracking_number="TRK-7723985641",
    ),
    "ORD-1003": OrderInfo(
        order_id="ORD-1003",
        status="in_transit",
        order_date="2026-06-07",
        product_name="Portable External Hard Drive 2TB",
        amount_paid=129.99,
        payment_method="credit_card",
        tracking_number="TRK-5519274430",
    ),
    "ORD-1004": OrderInfo(
        order_id="ORD-1004",
        status="cancelled",
        order_date="2026-05-30",
        product_name="Ergonomic Office Chair",
        amount_paid=299.99,
        payment_method="credit_card",
    ),
    "ORD-1005": OrderInfo(
        order_id="ORD-1005",
        status="refunded",
        order_date="2026-04-10",
        product_name="HD Webcam 1080p",
        amount_paid=79.99,
        payment_method="paypal",
        delivery_date="2026-04-14",
        tracking_number="TRK-3312876540",
    ),
    "ORD-1006": OrderInfo(
        order_id="ORD-1006",
        status="delivered",
        order_date="2026-04-22",
        product_name="7-Port USB-C Hub",
        amount_paid=34.99,
        payment_method="debit_card",
        delivery_date="2026-04-26",
        tracking_number="TRK-9904512367",
    ),
    "ORD-1007": OrderInfo(
        order_id="ORD-1007",
        status="in_transit",
        order_date="2026-06-05",
        product_name="Dual Monitor Arm (Clamp Mount)",
        amount_paid=69.99,
        payment_method="credit_card",
        tracking_number="TRK-1187643029",
    ),
    "ORD-1008": OrderInfo(
        order_id="ORD-1008",
        status="refunded",
        order_date="2026-05-01",
        product_name="Mechanical Keyboard (Tactile Switches)",
        amount_paid=159.99,
        payment_method="credit_card",
        delivery_date="2026-05-05",
        tracking_number="TRK-6628190034",
    ),
    "ORD-1009": OrderInfo(
        order_id="ORD-1009",
        status="delivered",
        order_date="2026-05-18",
        product_name="LED Desk Lamp with USB Charging",
        amount_paid=54.99,
        payment_method="paypal",
        delivery_date="2026-05-21",
        tracking_number="TRK-4456712890",
    ),
    "ORD-1010": OrderInfo(
        order_id="ORD-1010",
        status="in_transit",
        order_date="2026-06-08",
        product_name="Adjustable Phone and Tablet Stand",
        amount_paid=19.99,
        payment_method="debit_card",
        tracking_number="TRK-2230891456",
    ),
}

_NOT_FOUND = OrderInfo(
    order_id="",
    status="not_found",
    order_date="",
    product_name="",
    amount_paid=0.0,
    payment_method="",
)


@tool
def order_lookup(order_id: str) -> OrderInfo:
    """Look up an order by its ID. Returns status, product details, payment method,
    and delivery information. Returns status='not_found' for unknown order IDs -
    never raises an exception for missing orders.
    """
    result = _ORDERS.get(order_id)
    if result is None:
        return _NOT_FOUND.model_copy(update={"order_id": order_id})
    return result
