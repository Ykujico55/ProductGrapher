from shop.orders import OrderService


def main():
    service = OrderService()
    service.submit("demo-001")
    print(service.status("demo-001"))


if __name__ == "__main__":
    main()
