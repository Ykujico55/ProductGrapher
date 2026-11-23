import argparse
import json
import sys
from .store import Desk, Rejected


def positive(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("quantity must be a positive integer")
    if n <= 0:
        raise argparse.ArgumentTypeError("quantity must be a positive integer")
    return n


def main():
    p = argparse.ArgumentParser(prog="dispatchdesk")
    p.add_argument("--db", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("stock")
    s.add_argument("warehouse")
    s.add_argument("sku")
    s.add_argument("qty", type=positive)
    s = sub.add_parser("order")
    s.add_argument("id")
    s.add_argument("region")
    s.add_argument("items", nargs="+")
    s.add_argument("--policy", choices=("near", "bulk"), default="near")
    s = sub.add_parser("ship")
    s.add_argument("id")
    s.add_argument("warehouse")
    s = sub.add_parser("cancel")
    s.add_argument("id")
    s = sub.add_parser("return")
    s.add_argument("id")
    s.add_argument("warehouse")
    s.add_argument("sku")
    s.add_argument("qty", type=positive)
    s.add_argument("--quality", required=True, choices=("sellable", "damaged"))
    s = sub.add_parser("status")
    s.add_argument("id")
    sub.add_parser("report")
    args = p.parse_args()
    try:
        items = {}
        if args.command == "order":
            for item in args.items:
                sku, sep, quantity = item.partition("=")
                if not sep or not sku:
                    raise Rejected("items must be SKU=Q")
                try:
                    qty = positive(quantity)
                except argparse.ArgumentTypeError as e:
                    raise Rejected(str(e))
                items[sku] = items.get(sku, 0) + qty
        desk = Desk(args.db)
        try:
            if args.command == "stock":
                result = desk.stock(args.warehouse, args.sku, args.qty)
            elif args.command == "order":
                result = desk.order(args.id, args.region, items, args.policy)
            elif args.command == "ship":
                result = desk.ship(args.id, args.warehouse)
            elif args.command == "cancel":
                result = desk.cancel(args.id)
            elif args.command == "return":
                result = desk.return_goods(
                    args.id, args.warehouse, args.sku, args.qty, args.quality
                )
            elif args.command == "status":
                result = desk.status(args.id)
            else:
                result = desk.report()
        finally:
            desk.close()
        print(json.dumps(result, ensure_ascii=False))
    except Rejected as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
