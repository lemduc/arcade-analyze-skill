"""Application layer — use cases. Depends on domain + infrastructure."""
from app.domain.order import Order
from app.store.orders_repo import OrdersRepo


class OrdersService:
    def __init__(self):
        self._repo = OrdersRepo()

    def list_for_user(self, user_id: int) -> list[Order]:
        return [o for o in self._repo.list_orders() if o.user_id == user_id]
