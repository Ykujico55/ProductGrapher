class OrderMissing(KeyError):
    pass


class MemoryStore:
    def __init__(self):
        self.records = {}


class OrderStore(MemoryStore):
    def save(self, order_id, status):
        self.records[order_id] = status
        return status

    def get(self, order_id):
        if order_id not in self.records:
            raise OrderMissing(order_id)
        return self.records[order_id]
