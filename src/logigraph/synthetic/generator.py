"""Step 2c-1: Generate synthetic export shipments (the ground truth).

One shipment record drives all three documents: HAWB, SLI and the
EDI 214 status report. Every company, person and address is fake (Faker).
Delays follow the real per-mode patterns in data/processed/delay_profile.json.
"""
import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

PROFILE_PATH = Path("data/processed/delay_profile.json")
OUTPUT_PATH = Path("data/processed/synthetic/shipments.jsonl")

# ---------------------------------------------------------------------------
# Reference lists (assumptions for synthetic data, not real business rules)
# ---------------------------------------------------------------------------
US_ORIGINS = [  # (airport/port code, city, state)
    ("IAH", "Houston", "TX"), ("ORD", "Chicago", "IL"), ("LAX", "Los Angeles", "CA"),
    ("JFK", "New York", "NY"), ("ATL", "Atlanta", "GA"), ("DFW", "Dallas", "TX"),
    ("MIA", "Miami", "FL"),
]
DESTINATIONS = [  # (code, city, country, Faker locale for consignee names)
    ("FRA", "Frankfurt", "Germany", "de_DE"), ("LHR", "London", "United Kingdom", "en_GB"),
    ("NRT", "Tokyo", "Japan", "en_US"), ("BOM", "Mumbai", "India", "en_IN"),
    ("GRU", "Sao Paulo", "Brazil", "pt_BR"), ("MEX", "Mexico City", "Mexico", "es_MX"),
    ("AMS", "Amsterdam", "Netherlands", "nl_NL"), ("SIN", "Singapore", "Singapore", "en_US"),
    ("DXB", "Dubai", "United Arab Emirates", "en_US"), ("SYD", "Sydney", "Australia", "en_AU"),
]
# Illustrative commodities. Schedule B codes are examples, not verified for customs use.
COMMODITIES = [
    ("Portable computers (laptops)", "8471300100", 2.5, 900),
    ("Industrial valve assemblies", "8481809000", 12.0, 450),
    ("Medical diagnostic instruments", "9018190000", 8.0, 2500),
    ("Auto parts - brake components", "8708300000", 6.0, 120),
    ("Printed circuit board assemblies", "8473301100", 1.2, 300),
    ("Laboratory glassware", "7017100000", 3.0, 60),
    ("Hydraulic pump units", "8413500000", 25.0, 1800),
]
# Dangerous goods commodity, added to about 10% of shipments.
LITHIUM_BATTERIES = ("Lithium-ion battery packs", "8507600020", 4.0, 350)
INCOTERMS = ["EXW", "FCA", "CPT", "CIP", "DAP", "DDP", "FOB", "CIF"]
PAYMENT_TERMS = ["prepaid", "collect", "third_party", "cod"]
CONSIGNEE_TYPES = ["direct_consumer", "reseller", "government_entity", "other_unknown"]
# Business-level delay reasons; mapped to X12 AT7 reason codes in the 214 step.
DELAY_REASONS = ["weather", "customs_hold", "missed_connection",
                 "capacity_shortage", "documentation_issue", "consignee_unavailable"]
# Planned transit time in days per mode (assumption).
TRANSIT_DAYS = {"Air": (3, 6), "Air Charter": (1, 3), "Ocean": (20, 40), "Truck": (2, 7)}
# Ocean shipments use seaport codes (UN/LOCODE) instead of airport codes.
OCEAN_PORTS = {
    "IAH": "USHOU", "DFW": "USHOU", "LAX": "USLAX", "JFK": "USNYC", "MIA": "USMIA",
    "ATL": "USSAV", "ORD": "USNYC",
    "FRA": "DEHAM", "LHR": "GBFXT", "NRT": "JPTYO", "BOM": "INNSA", "GRU": "BRSSZ",
    "MEX": "MXVER", "AMS": "NLRTM", "SIN": "SGSIN", "DXB": "AEJEA", "SYD": "AUSYD",
}


@dataclass
class Party:
    name: str
    address: str
    city: str
    state_or_country: str
    postal_code: str
    phone: str
    contact: str
    account_number: str


@dataclass
class LineItem:
    description: str
    schedule_b: str
    pieces: int
    gross_weight_kg: float
    dimensions_in: str      # L x W x H, inches
    value_usd: int


@dataclass
class Milestone:
    status: str             # picked_up, departed, arrived, delivered, delayed
    date: str
    location: str
    reason: str | None = None


@dataclass
class Shipment:
    shipment_id: str
    hawb_number: str
    sli_quote_number: str
    ship_date: str
    mode: str
    service_level: str
    origin_code: str
    destination_code: str
    destination_country: str
    incoterm: str
    payment_terms: str
    consignee_type: str
    routed_export: bool
    shipper: Party
    consignee: Party
    line_items: list[LineItem]
    declared_value_usd: int
    insurance_usd: int
    eccn: str = "EAR99"
    license: str = "NLR"
    dangerous_goods_un: str | None = None
    scheduled_delivery: str = ""
    actual_delivery: str = ""
    delay_days: int = 0
    delay_reason: str | None = None
    milestones: list[Milestone] = field(default_factory=list)
    synthetic: bool = True


def sample_delay_days(rng, quantiles):
    """Sample a delay from the per-mode quantiles (piecewise-linear inverse CDF)."""
    points = [(0.0, 1.0)] + [(q, quantiles[f"p{int(q * 100)}"])
                             for q in (0.10, 0.25, 0.50, 0.75, 0.90)]
    points.append((1.0, quantiles["p90"] * 1.5))
    u = rng.random()
    for (q0, d0), (q1, d1) in zip(points, points[1:]):
        if u <= q1:
            return max(1, round(d0 + (d1 - d0) * (u - q0) / (q1 - q0)))
    return max(1, round(points[-1][1]))


def make_party(fake, rng, city, region, prefix):
    return Party(
        name=f"{fake.company()} (SYNTHETIC)",
        address=fake.street_address(),
        city=city,
        state_or_country=region,
        postal_code=fake.postcode(),
        phone=fake.phone_number(),
        contact=fake.name(),
        account_number=f"{prefix}{rng.randint(100000, 999999)}",
    )


def make_shipment(n, rng, fake_us, fakes, profile):
    modes = list(profile)
    mode = rng.choices(modes, weights=[profile[m]["shipments"] for m in modes])[0]

    origin_code, origin_city, origin_state = rng.choice(US_ORIGINS)
    if mode == "Truck":
        # Trucks only cross the land border (transborder to Mexico).
        dest_code, dest_city, dest_country, locale = DESTINATIONS[5]
    else:
        dest_code, dest_city, dest_country, locale = rng.choice(DESTINATIONS)
    if mode == "Ocean":
        origin_code, dest_code = OCEAN_PORTS[origin_code], OCEAN_PORTS[dest_code]

    shipper = make_party(fake_us, rng, origin_city, origin_state, "SH")
    consignee = make_party(fakes[locale], rng, dest_city, dest_country, "CN")

    picked = rng.sample(COMMODITIES, k=rng.randint(1, 3))
    if rng.random() < 0.10:
        picked.append(LITHIUM_BATTERIES)

    items = []
    for desc, sched_b, kg_each, usd_each in picked:
        pieces = rng.randint(1, 40)
        items.append(LineItem(
            description=desc, schedule_b=sched_b, pieces=pieces,
            gross_weight_kg=round(pieces * kg_each * rng.uniform(0.9, 1.1), 1),
            dimensions_in=f"{rng.randint(12, 48)}x{rng.randint(12, 40)}x{rng.randint(8, 36)}",
            value_usd=round(pieces * usd_each * rng.uniform(0.8, 1.2)),
        ))
    value = sum(i.value_usd for i in items)
    has_batteries = any(i.description.startswith("Lithium") for i in items)

    ship_date = date(2025, 1, 1) + timedelta(days=rng.randint(0, 600))
    planned = rng.randint(*TRANSIT_DAYS[mode])
    scheduled = ship_date + timedelta(days=planned)

    p = profile[mode]
    delay = sample_delay_days(rng, p["late_days_quantiles"]) if rng.random() < p["late_rate"] else 0
    reason = rng.choice(DELAY_REASONS) if delay else None
    actual = scheduled + timedelta(days=delay)

    departed = ship_date + timedelta(days=1)
    arrived = actual - timedelta(days=1) if (actual - departed).days > 1 else actual
    milestones = [
        Milestone("picked_up", ship_date.isoformat(), origin_city),
        Milestone("departed", departed.isoformat(), origin_code),
    ]
    if delay:
        milestones.append(Milestone("delayed", (departed + timedelta(days=1)).isoformat(),
                                    rng.choice([origin_code, dest_code]), reason))
    milestones += [
        Milestone("arrived", arrived.isoformat(), dest_code),
        Milestone("delivered", actual.isoformat(), dest_city),
    ]

    service = {"Air": "intl_air.door_to_door", "Air Charter": "intl_air.charter",
               "Ocean": "ocean.port_to_door", "Truck": "domestic.truckload"}[mode]

    return Shipment(
        shipment_id=f"SYN-{n:06d}",
        hawb_number=f"SYN{rng.randint(10000000, 99999999)}",
        sli_quote_number=f"Q-SYN-{n:06d}",
        ship_date=ship_date.isoformat(),
        mode=mode,
        service_level=service,
        origin_code=origin_code,
        destination_code=dest_code,
        destination_country=dest_country,
        incoterm=rng.choice(INCOTERMS),
        payment_terms=rng.choices(PAYMENT_TERMS, weights=[60, 25, 12, 3])[0],
        consignee_type=rng.choice(CONSIGNEE_TYPES),
        routed_export=rng.random() < 0.2,
        shipper=shipper,
        consignee=consignee,
        line_items=items,
        declared_value_usd=value,
        insurance_usd=value if rng.random() < 0.4 else 0,
        dangerous_goods_un="UN3480" if has_batteries else None,
        scheduled_delivery=scheduled.isoformat(),
        actual_delivery=actual.isoformat(),
        delay_days=delay,
        delay_reason=reason,
        milestones=milestones,
    )


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic export shipments.")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    Faker.seed(args.seed)
    fake_us = Faker("en_US")
    fakes = {loc: Faker(loc) for loc in {d[3] for d in DESTINATIONS}}
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

    shipments = [make_shipment(n, rng, fake_us, fakes, profile)
                 for n in range(1, args.count + 1)]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for s in shipments:
            f.write(json.dumps(asdict(s), ensure_ascii=False) + "\n")

    late = [s for s in shipments if s.delay_days]
    print(f"Generated {len(shipments)} shipments -> {OUTPUT_PATH}")
    print(f"Late: {len(late)} ({len(late) / len(shipments):.1%})")
    for mode in sorted({s.mode for s in shipments}):
        group = [s for s in shipments if s.mode == mode]
        print(f"  {mode:<12} {len(group):>4} shipments, {sum(1 for s in group if s.delay_days):>3} late")
    print(f"Dangerous goods (UN3480): {sum(1 for s in shipments if s.dangerous_goods_un)}")
    print("\nFirst shipment:")
    s = shipments[0]
    print(f"  {s.shipment_id}  HAWB {s.hawb_number}  {s.mode}  {s.origin_code}->{s.destination_code}")
    print(f"  Shipper:   {s.shipper.name}")
    print(f"  Consignee: {s.consignee.name}, {s.consignee.city}")
    print(f"  Incoterm {s.incoterm}, payment {s.payment_terms}, delay {s.delay_days} days ({s.delay_reason})")


if __name__ == "__main__":
    main()