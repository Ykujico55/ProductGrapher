"""Independent acceptance probe shared by the baseline and the submission."""

import sys

from orders import cancel_order


order = {"id": "A-1", "state": sys.argv[1]}
try:
    cancel_order(order)
except ValueError:
    print("rejected", order["state"])
else:
    print("accepted", order["state"])
