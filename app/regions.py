"""Country presets: interface language, currency, electricity prices and the
grid emission factor used by the savings report (FR-7).

Choosing a country in Settings switches the interface language and the
values below together. Prices are the energy charge per kWh only (no fixed
charges, surcharges or tax), so money figures are conservative. Adding a
country is one entry here plus its language in the web UI.
"""
from __future__ import annotations

DEFAULT = "KR"

REGIONS: dict[str, dict] = {
    "KR": {
        "code": "KR",
        "lang": "ko",
        "currency": "KRW",
        "symbol": "₩",
        "decimals": 0,
        # KEPCO tariff table (effective 2023-05-16): residential low voltage,
        # 2nd progressive tier -- where a typical household's last kWh falls.
        "price": 214.6,
        # KEPCO residential time-of-use (Jeju), summer/winter: max load
        # 16-22 h, light load 22-08 h. Mainland homes have no TOU rate yet.
        "peak": 220.5,
        "offpeak": 138.7,
        "peak_hours": "16-22",
        # 2023 national grid factor, 0.4173 tCO2eq/MWh (Ministry of Climate,
        # Energy and Environment, announced 2025-12-18).
        "co2_kg_per_kwh": 0.4173,
        "tariff_source": "KEPCO",
        "co2_source": "MCEE 2023",
    },
    "US": {
        "code": "US",
        "lang": "en",
        "currency": "USD",
        "symbol": "$",
        "decimals": 2,
        # EIA: 2025 U.S. average residential retail price, 17.30 cents/kWh.
        "price": 0.173,
        # PG&E E-TOU-C (effective 2026-03-01), summer, below baseline:
        # peak 4-9 pm 44 cents, off-peak 32 cents. No national TOU rate exists;
        # this is a widely used residential one with an evening peak.
        "peak": 0.44,
        "offpeak": 0.32,
        "peak_hours": "16-21",
        # EPA eGRID2023 U.S. total output rate, 770.884 lb CO2e/MWh.
        "co2_kg_per_kwh": 0.3497,
        "tariff_source": "EIA / PG&E",
        "co2_source": "EPA eGRID2023",
    },
}


def get(code: str | None) -> dict:
    return REGIONS.get(str(code or "").upper(), REGIONS[DEFAULT])
