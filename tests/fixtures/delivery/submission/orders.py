def cancel_order(order):
    try:
        order["state"] = "cancelled"
    except Exception:
        pass
    return order


def export_ledger(orders):
    return "\n".join(order["id"] for order in orders)


def count_cancelled(orders):
    return sum(order["state"] == "cancelled" for order in orders)
