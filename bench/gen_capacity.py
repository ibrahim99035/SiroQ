#!/usr/bin/env python
"""Synthetic sales-like CSV generator for capacity benchmarking."""
import os
import sys

import numpy as np
import pandas as pd

n = int(sys.argv[1])
out = sys.argv[2]

rng = np.random.default_rng(1)
names = np.array(["Aspirin 500", "Panadol Extra", "Amoxil 250", "Vitamin C 1000",
                  "Cough Syrup", "Ibuprofen 200", "Metformin 850", "Cetirizine 10"])
mfrs = np.array(["Nile Pharma", "Cairo Med", "Delta Drugs", "Sinai Lab"])
regions = np.array(["Cairo", "Giza", "Alexandria", "Tanta", "Mansoura", "Aswan"])
cats = np.array(["analgesic", "antibiotic", "vitamin", "respiratory", "antidiabetic"])
pays = np.array(["cash", "card", "insurance"])
staff = np.array(["S1", "S2", "S3", "S4", "S5", "S6"])

dates = pd.to_datetime("2025-01-01") + pd.to_timedelta(rng.integers(0, 365, n), unit="D")
qty = rng.integers(1, 21, n).astype(float)
price = np.round(rng.uniform(1.5, 120.0, n), 2)
amount = np.round(qty * price, 2)
cost = np.round(amount * rng.uniform(0.55, 0.85, n), 2)
profit = np.round(amount - cost, 2)
# a plausible slice of negatives (returns) to exercise the quality checks
neg = rng.random(n) < 0.02
profit = np.where(neg, -np.round(rng.uniform(1, 40, n), 2), profit)
stock = rng.integers(0, 5000, n)

df = pd.DataFrame({
    "sale_date": dates.strftime("%Y-%m-%d"),
    "product_code": rng.integers(1000, 9999, n).astype(str),
    "product_name": rng.choice(names, n),
    "manufacturer": rng.choice(mfrs, n),
    "quantity_sold": qty,
    "selling_price": price,
    "total_amount": amount,
    "total_cost": cost,
    "net_profit": profit,
    "stock_balance": stock,
    "customer": rng.choice(["C"] + list("ABE"), n).astype("U2"),
    "region": rng.choice(regions, n),
    "category": rng.choice(cats, n),
    "payment": rng.choice(pays, n),
    "vat_rate": np.full(n, 0.14),
    "staff": rng.choice(staff, n),
})
df.to_csv(out, index=False)
print(f"wrote {n} rows -> {out} ({os.path.getsize(out)} bytes)")