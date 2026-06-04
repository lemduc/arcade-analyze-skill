"""Infrastructure layer — persistence. May depend on the domain only."""
from app.domain.order import Order


class OrdersRepo:
    def __init__(self):
        self._orders = [Order(1, 100, 9.99), Order(2, 100, 4.50), Order(3, 200, 1.0)]

    def list_orders(self) -> list[Order]:
        return list(self._orders)

    def get(self, order_id: int) -> Order | None:
        return next((o for o in self._orders if o.id == order_id), None)
