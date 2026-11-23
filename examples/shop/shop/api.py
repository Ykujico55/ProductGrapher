from fastapi import FastAPI

from shop.orders import OrderService


app = FastAPI()
orders = OrderService()


@app.post("/orders/{order_id}", tags=["订单"])
def submit_order(order_id: str):
    return orders.submit(order_id)


@app.delete("/orders/{order_id}", tags=["订单"])
def cancel_order(order_id: str):
    return orders.cancel(order_id)


@app.get("/orders/{order_id}", tags=["订单"])
def get_order(order_id: str):
    return orders.status(order_id)
