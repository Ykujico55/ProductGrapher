import sqlite3


class Rejected(Exception):
    pass


class Desk:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS inventory (
            warehouse TEXT, sku TEXT,
            available INTEGER NOT NULL DEFAULT 0,
            damaged INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (warehouse, sku)
        );
        CREATE TABLE IF NOT EXISTS orders (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL, region TEXT NOT NULL,
            policy TEXT NOT NULL, state TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
            order_id TEXT, sku TEXT, qty INTEGER NOT NULL,
            PRIMARY KEY (order_id, sku),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS allocations (
            order_id TEXT, warehouse TEXT, sku TEXT, qty INTEGER NOT NULL,
            shipped INTEGER NOT NULL DEFAULT 0,
            returned_sellable INTEGER NOT NULL DEFAULT 0,
            returned_damaged INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (order_id, warehouse, sku),
            FOREIGN KEY (order_id) REFERENCES orders(id)
        );
        """)

    def close(self):
        self.db.close()

    def get_order(self, oid):
        row = self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if row is None:
            raise Rejected("order not found")
        return row

    def allocate(self, order):
        plans = []
        for item in self.db.execute(
            "SELECT sku,qty FROM items WHERE order_id=? ORDER BY sku", (order["id"],)
        ).fetchall():
            rows = self.db.execute(
                "SELECT warehouse,available FROM inventory WHERE sku=? AND available>0",
                (item["sku"],),
            ).fetchall()
            if order["policy"] == "near":
                rows = sorted(
                    rows,
                    key=lambda r: (r["warehouse"] != order["region"], r["warehouse"]),
                )
            else:
                rows = sorted(rows, key=lambda r: (-r["available"], r["warehouse"]))
            remaining = item["qty"]
            for row in rows:
                q = min(remaining, row["available"])
                if q:
                    plans.append((row["warehouse"], item["sku"], q))
                    remaining -= q
                if not remaining:
                    break
            if remaining:
                return False
        for warehouse, sku, qty in plans:
            self.db.execute(
                "UPDATE inventory SET available=available-? WHERE warehouse=? AND sku=?",
                (qty, warehouse, sku),
            )
            self.db.execute(
                "INSERT INTO allocations(order_id,warehouse,sku,qty) VALUES(?,?,?,?)",
                (order["id"], warehouse, sku, qty),
            )
        self.db.execute("UPDATE orders SET state='reserved' WHERE id=?", (order["id"],))
        return True

    def retry(self):
        for order in self.db.execute(
            "SELECT * FROM orders WHERE state='waiting' ORDER BY seq"
        ).fetchall():
            self.allocate(order)

    def stock(self, warehouse, sku, qty):
        with self.db:
            self.db.execute(
                """INSERT INTO inventory(warehouse, sku, available) VALUES (?, ?, ?)
                   ON CONFLICT(warehouse, sku)
                   DO UPDATE SET available=available+excluded.available""",
                (warehouse, sku, qty),
            )
            self.retry()
        return {"ok": True}

    def order(self, oid, region, items, policy):
        with self.db:
            if self.db.execute("SELECT 1 FROM orders WHERE id=?", (oid,)).fetchone():
                raise Rejected("order id already used")
            self.db.execute(
                "INSERT INTO orders(id,region,policy,state) VALUES(?,?,?,'waiting')",
                (oid, region, policy),
            )
            self.db.executemany(
                "INSERT INTO items VALUES(?,?,?)",
                [(oid, sku, qty) for sku, qty in items.items()],
            )
            self.allocate(self.get_order(oid))
        return self.status(oid)

    def ship(self, oid, warehouse):
        with self.db:
            order = self.get_order(oid)
            if order["state"] not in ("reserved", "partial"):
                raise Rejected("order cannot be shipped")
            rows = self.db.execute(
                "SELECT * FROM allocations WHERE order_id=? AND warehouse=?",
                (oid, warehouse),
            ).fetchall()
            if not rows or any(r["shipped"] for r in rows):
                raise Rejected("warehouse has no unshipped allocation")
            self.db.execute(
                "UPDATE allocations SET shipped=1 WHERE order_id=? AND warehouse=?",
                (oid, warehouse),
            )
            pending = self.db.execute(
                "SELECT 1 FROM allocations WHERE order_id=? AND shipped=0", (oid,)
            ).fetchone()
            self.db.execute(
                "UPDATE orders SET state=? WHERE id=?",
                ("partial" if pending else "shipped", oid),
            )
        return self.status(oid)

    def cancel(self, oid):
        with self.db:
            order = self.get_order(oid)
            if order["state"] not in ("waiting", "reserved"):
                raise Rejected("order cannot be cancelled")
            for row in self.db.execute(
                "SELECT * FROM allocations WHERE order_id=?", (oid,)
            ).fetchall():
                self.db.execute(
                    "UPDATE inventory SET available=available+? WHERE warehouse=? AND sku=?",
                    (row["qty"], row["warehouse"], row["sku"]),
                )
            self.db.execute("DELETE FROM allocations WHERE order_id=?", (oid,))
            self.db.execute("UPDATE orders SET state='cancelled' WHERE id=?", (oid,))
            self.retry()
        return self.status(oid)

    def return_goods(self, oid, warehouse, sku, qty, quality):
        with self.db:
            self.get_order(oid)
            row = self.db.execute(
                "SELECT * FROM allocations WHERE order_id=? AND warehouse=? AND sku=?",
                (oid, warehouse, sku),
            ).fetchone()
            if (
                row is None
                or not row["shipped"]
                or qty > row["qty"] - row["returned_sellable"] - row["returned_damaged"]
            ):
                raise Rejected("return exceeds shipped quantity")
            column = (
                "returned_sellable" if quality == "sellable" else "returned_damaged"
            )
            self.db.execute(
                f"UPDATE allocations SET {column}={column}+? WHERE order_id=? AND warehouse=? AND sku=?",
                (qty, oid, warehouse, sku),
            )
            column = "available" if quality == "sellable" else "damaged"
            self.db.execute(
                f"UPDATE inventory SET {column}={column}+? WHERE warehouse=? AND sku=?",
                (qty, warehouse, sku),
            )
            if quality == "sellable":
                self.retry()
        return self.status(oid)

    def status(self, oid):
        order = self.get_order(oid)
        return {
            "id": oid,
            "region": order["region"],
            "policy": order["policy"],
            "state": order["state"],
            "items": [
                dict(r)
                for r in self.db.execute(
                    "SELECT sku,qty FROM items WHERE order_id=? ORDER BY sku", (oid,)
                )
            ],
            "allocations": [
                dict(r)
                for r in self.db.execute(
                    """SELECT warehouse, sku, qty, shipped, returned_sellable,
                              returned_damaged
                       FROM allocations WHERE order_id=? ORDER BY warehouse, sku""",
                    (oid,),
                )
            ],
        }

    def report(self):
        rows = self.db.execute(
            """SELECT i.warehouse, i.sku, i.available,
                      COALESCE((SELECT SUM(a.qty) FROM allocations a
                                WHERE a.warehouse=i.warehouse AND a.sku=i.sku
                                  AND a.shipped=0), 0) AS reserved,
                      i.damaged
               FROM inventory i ORDER BY i.warehouse, i.sku"""
        )
        return {"inventory": [dict(r) for r in rows]}
