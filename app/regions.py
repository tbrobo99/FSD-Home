"""Country and state presets: interface language, currency, electricity prices
and the grid emission factor used by the savings report (FR-7).

Choosing a region in Settings switches the interface language and the values
below together. Prices are per kWh only (no fixed or standing charges), so
money figures are conservative. Electricity is priced very differently inside
one country -- a kWh in California costs about twice one in Washington, and
the time-of-use plans differ by utility -- so the United States is offered per
state as well as on average.

``peak`` / ``offpeak`` are the two prices of a common residential time-of-use
plan in that region. Only their difference is used: it is what moving a load
out of the peak band saves. Where no such plan is in common use they are None,
and a shifted load is counted in kWh but earns no money.

Adding a region is one entry here plus its name in the web UI (app.js).
"""
from __future__ import annotations

DEFAULT = "KR"

# 1 lb/MWh = 0.000453592 kg/kWh (eGRID publishes lb/MWh).
_LB = 0.000453592


def _us_state(code: str, price: float, egrid_lb: float, **tou) -> dict:
    return {
        "code": f"US-{code}", "parent": "US", "group": "us", "lang": "en", "locale": "en-US",
        "currency": "USD", "symbol": "$", "decimals": 2,
        # EIA Electric Power Monthly, Table 5.6.A: average residential price,
        # August 2026 (the latest month published).
        "price": price,
        "peak": tou.get("peak"), "offpeak": tou.get("offpeak"), "peak_hours": tou.get("hours"),
        # EPA eGRID2023 state total output emission rate (CO2e).
        "co2_kg_per_kwh": round(egrid_lb * _LB, 4),
        "tariff_source": "EIA" + (f" / {tou['src']}" if tou.get("src") else ""),
        "co2_source": "EPA eGRID2023",
    }


REGIONS: dict[str, dict] = {
    # ------------------------------------------------------------ Asia
    "KR": {
        "code": "KR", "group": "asia", "lang": "ko", "locale": "ko-KR",
        "currency": "KRW", "symbol": "₩", "decimals": 0,
        # KEPCO tariff table (effective 2023-05-16): residential low voltage,
        # 2nd progressive tier -- where a typical household's last kWh falls.
        "price": 214.6,
        # KEPCO residential time-of-use (Jeju), summer/winter: max load
        # 16-22 h, light load 22-08 h. Mainland homes have no TOU rate yet.
        "peak": 220.5, "offpeak": 138.7, "peak_hours": "16-22",
        # 2023 national grid factor, 0.4173 tCO2eq/MWh (Ministry of Climate,
        # Energy and Environment, announced 2025-12-18).
        "co2_kg_per_kwh": 0.4173,
        "tariff_source": "KEPCO", "co2_source": "MCEE 2023",
    },
    # ------------------------------------------------------------ United States
    "US": {
        "code": "US", "group": "us", "lang": "en", "locale": "en-US",
        "currency": "USD", "symbol": "$", "decimals": 2,
        # EIA Table 5.6.A, U.S. total residential, August 2026: 18.16 cents.
        "price": 0.1816,
        # There is no national time-of-use plan: pick a state for one.
        "peak": None, "offpeak": None, "peak_hours": None,
        # EPA eGRID2023 U.S. total output rate, 770.9 lb CO2e/MWh.
        "co2_kg_per_kwh": 0.3497,
        "tariff_source": "EIA", "co2_source": "EPA eGRID2023",
    },
    # California -- PG&E E-TOU-C (effective 2026-03-01), summer, below
    # baseline: peak 4-9 pm 44 cents, off-peak 32 cents.
    "US-CA": _us_state("CA", 0.2975, 394.8, peak=0.44, offpeak=0.32, hours="16-21", src="PG&E E-TOU-C"),
    # Texas -- retail choice; plans differ by retailer, no common TOU plan.
    "US-TX": _us_state("TX", 0.1591, 771.2),
    # New York -- Con Edison residential time-of-use, June-September delivery
    # charges: peak 8 am-midnight 27.86 cents, off-peak 5.22 cents. Supply is
    # billed separately, so only the delivery spread is counted.
    "US-NY": _us_state("NY", 0.2865, 466.6, peak=0.2786, offpeak=0.0522, hours="08-24", src="Con Edison TOU"),
    # Florida -- FPL RTR-1 rider on RS-1 (rates effective October 2026):
    # RS-1 first 1,000 kWh 12.26 cents all-in; RTR-1 adds 14.64 cents on-peak
    # and credits 6.26 cents off-peak (base + fuel). On-peak April-October is
    # weekdays noon-9 pm.
    "US-FL": _us_state("FL", 0.1499, 789.1, peak=0.2689, offpeak=0.0600, hours="12-21", src="FPL RTR-1"),
    # Arizona -- APS "Time of Use 4pm-7pm Weekdays" plan, summer (May-Oct):
    # on-peak 14.227 cents, off-peak 5.943 cents (plus a demand charge).
    "US-AZ": _us_state("AZ", 0.1505, 689.1, peak=0.14227, offpeak=0.05943, hours="16-19", src="APS TOU 4-7pm"),
    # Washington -- mostly hydro; no common residential TOU plan modelled.
    "US-WA": _us_state("WA", 0.1463, 266.6),
    # ------------------------------------------------------------ Europe
    "GB": {
        "code": "GB", "group": "europe", "lang": "en", "locale": "en-GB",
        "currency": "GBP", "symbol": "£", "decimals": 2,
        # Ofgem price cap 1 Oct-31 Dec 2026, average Direct Debit unit rate
        # 26.32p/kWh (no VAT on electricity this quarter).
        "price": 0.2632,
        # Time-of-use tariffs exist (e.g. a 4-7 pm peak) but are priced by
        # supplier and region, not by the cap.
        "peak": None, "offpeak": None, "peak_hours": None,
        # DESNZ 2026 conversion factors: UK electricity 26 % below the 2025
        # factor (0.177), i.e. about 0.131 kg CO2e/kWh.
        "co2_kg_per_kwh": 0.131,
        "tariff_source": "Ofgem", "co2_source": "DESNZ 2026",
    },
    "DE": {
        "code": "DE", "group": "europe", "lang": "de", "locale": "de-DE",
        "currency": "EUR", "symbol": "€", "decimals": 2,
        # BDEW Strompreisanalyse 10/2026: household average 2026, 37.3 ct/kWh
        # (3,500 kWh/year, standing charge apportioned).
        "price": 0.373,
        # Time-variable grid fees (Section 14a EnWG, module 3) differ by grid
        # operator; no national time-of-use price.
        "peak": None, "offpeak": None, "peak_hours": None,
        # Umweltbundesamt: German electricity mix 2025, 344 g CO2/kWh (estimate).
        "co2_kg_per_kwh": 0.344,
        "tariff_source": "BDEW", "co2_source": "UBA 2025",
    },
    "FR": {
        "code": "FR", "group": "europe", "lang": "fr", "locale": "fr-FR",
        "currency": "EUR", "symbol": "€", "decimals": 2,
        # Tarif Bleu (regulated), from 1 August 2026, taxes included, 6 kVA:
        # Base 20.01 c€/kWh; Heures Creuses option HP 21.42 / HC 15.89 c€
        # (CRE deliberation 2026-147: 14.79 / 10.18 c€ before tax). The eight
        # off-peak hours are set locally by Enedis, typically 22-06 h.
        "price": 0.2001, "peak": 0.2142, "offpeak": 0.1589, "peak_hours": "06-22",
        # RTE Bilan électrique 2025: production carbon intensity 19.6 g/kWh.
        "co2_kg_per_kwh": 0.0196,
        "tariff_source": "CRE Tarif Bleu", "co2_source": "RTE 2025",
    },
    "ES": {
        "code": "ES", "group": "europe", "lang": "es", "locale": "es-ES",
        "currency": "EUR", "symbol": "€", "decimals": 2,
        # PVPC (2.0TD) energy term, taxes included, as reported by FACUA for
        # June, August, November and December 2025 (mean of the four months):
        # punta 25.55, llano 16.72, valle 14.97 c€/kWh; weighted by FACUA's
        # average household (106 / 95 / 165 kWh) = 18.5 c€/kWh. Punta is
        # weekdays 10-14 h and 18-22 h, valle 0-8 h and weekends.
        "price": 0.185, "peak": 0.2555, "offpeak": 0.1497, "peak_hours": "10-14,18-22",
        # National mix 2025, 107 g CO2/kWh (CNMC electricity label, as
        # published by a retailer).
        "co2_kg_per_kwh": 0.107,
        "tariff_source": "PVPC (FACUA)", "co2_source": "CNMC 2025",
    },
    "IT": {
        "code": "IT", "group": "europe", "lang": "it", "locale": "it-IT",
        "currency": "EUR", "symbol": "€", "decimals": 2,
        # ARERA, Maggior Tutela from 1 July 2026: reference price for the
        # typical household 31.63 c€/kWh, taxes included.
        "price": 0.3163,
        # Two-band (F1 / F23) prices are not published as a single reference;
        # not modelled.
        "peak": None, "offpeak": None, "peak_hours": None,
        # ISPRA report 413/2025: national factor for total electricity
        # production 2023, 256.3 g CO2/kWh.
        "co2_kg_per_kwh": 0.2563,
        "tariff_source": "ARERA", "co2_source": "ISPRA 2023",
    },
}

# Interface languages the web UI ships, by region default.
LANGS = ("ko", "en", "de", "fr", "es", "it")


def get(code: str | None) -> dict:
    return REGIONS.get(str(code or "").upper(), REGIONS[DEFAULT])


def has_tou(region: dict) -> bool:
    return region.get("peak") is not None and region.get("offpeak") is not None
