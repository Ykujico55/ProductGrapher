"""Exercise the public CLI in fresh processes and report observed business state."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def observe(directory):
    db = str(directory / "warehouses.db")
    steps = []

    def cli(*args, require_success=True):
        run = subprocess.run([sys.executable, "-B", "-X", "utf8", "-m", "dispatchdesk", "--db", db, *args],
                             capture_output=True, text=True, encoding="utf-8", check=require_success)
        steps.append({"argv": args, "exit": run.returncode, "stderr": run.stderr.strip()})
        return run.returncode, json.loads(run.stdout) if run.stdout.strip() else None

    def order_snapshot(value):
        return {"state": value["state"], "allocations": [
            [row["warehouse"], row["sku"], row["qty"], row["shipped"]] for row in value["allocations"]]}

    cli("stock", "N", "A", "1")
    cli("stock", "W", "A", "1")
    cli("order", "O1", "N", "A=2")
    _, initial = cli("status", "O1")
    first, _ = cli("ship", "O1", "N", require_success=False)
    _, after_first = cli("status", "O1")
    _, inventory_before = cli("report")
    duplicate, _ = cli("ship", "O1", "N", require_success=False)
    error = steps[-1]["stderr"]
    try:
        domain_error = json.loads(error).get("error")
    except (ValueError, AttributeError):
        domain_error = None
    _, after_duplicate = cli("status", "O1")
    _, inventory_after = cli("report")
    last, _ = cli("ship", "O1", "W", require_success=False)
    _, after_last = cli("status", "O1")
    return {"initial": order_snapshot(initial), "first": {"exit": first, **order_snapshot(after_first)},
            "duplicate": {"exit": duplicate, "rejected": duplicate != 0 and bool(domain_error),
                          "orderUnchanged": after_duplicate == after_first,
                          "inventoryUnchanged": inventory_after == inventory_before},
            "last": {"exit": last, **order_snapshot(after_last)}, "steps": steps}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        print(json.dumps(observe(Path(tmp)), ensure_ascii=False))
