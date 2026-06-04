"""Presentation layer — HTTP handlers. Must go through the service layer only."""
from app.service.orders_service import OrdersService


class OrdersApi:
    def __init__(self):
        self._service = OrdersService()

    def get_user_orders(self, user_id: int) -> list[dict]:
        return [{"id": o.id, "total": o.total} for o in self._service.list_for_user(user_id)]
