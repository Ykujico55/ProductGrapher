from shop.storage import OrderStore


store = OrderStore()


class OrderService:
    def submit(self, order_id):
        return store.save(order_id, "submitted")

    def cancel(self, order_id):
        store.get(order_id)
        return store.save(order_id, "cancelled")

    def status(self, order_id):
        return store.get(order_id)
