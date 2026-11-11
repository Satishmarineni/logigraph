"""Build a delay profile from the SCMS delivery history (summary statistics only).

Only per-mode summary numbers are saved (late rate and delay quantiles).
No rows from the source dataset are copied into generated documents.
"""
import json
from pathlib import Path

import pandas as pd

SOURCE = Path("data/external/SCMS_Delivery_History_Dataset.csv")
OUTPUT = Path("data/processed/delay_profile.json")
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
DATE_FORMAT = "%d-%b-%y"  # e.g. 2-Jun-06


def load_deliveries(path=SOURCE):
    df = pd.read_csv(
        path,
        encoding="utf-8-sig",  # the file starts with a byte-order mark (BOM)
        usecols=["Shipment Mode", "Scheduled Delivery Date", "Delivered to Client Date"],
    )
    df = df.rename(columns={
        "Shipment Mode": "mode",
        "Scheduled Delivery Date": "scheduled",
        "Delivered to Client Date": "delivered",
    })
    df = df.dropna(subset=["mode"])  # 360 rows have no shipment mode

    df["scheduled"] = pd.to_datetime(df["scheduled"], format=DATE_FORMAT, errors="coerce")
    df["delivered"] = pd.to_datetime(df["delivered"], format=DATE_FORMAT, errors="coerce")
    df["delay_days"] = (df["delivered"] - df["scheduled"]).dt.days
    return df.dropna(subset=["delay_days"])


def build_profile(df):
    profile = {}
    for mode, group in df.groupby("mode"):
        late = group.loc[group["delay_days"] > 0, "delay_days"]
        profile[mode] = {
            "shipments": int(len(group)),
            "late_rate": round(float((group["delay_days"] > 0).mean()), 3),
            "late_days_quantiles": {
                f"p{int(q * 100)}": float(late.quantile(q)) for q in QUANTILES
            },
        }
    return profile


def main():
    df = load_deliveries()
    profile = build_profile(df)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    print(f"Rows used: {len(df)}")
    print(f"{'Mode':<12} {'Shipments':>9} {'Late rate':>9} {'Median late days':>17} {'P90 late days':>14}")
    for mode, p in profile.items():
        q = p["late_days_quantiles"]
        print(f"{mode:<12} {p['shipments']:>9} {p['late_rate']:>9.1%} {q['p50']:>17.0f} {q['p90']:>14.0f}")
    print(f"\nSaved: {OUTPUT}")


if __name__ == "__main__":
    main()