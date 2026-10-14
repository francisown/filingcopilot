"""Read-only ADS input inventory from hash-bound retained SEC originals.

This does not calculate ADS earnings/PE, infer a share ratio/FX rate, or label
semantic support. Finite reported decimals never become unrounded inputs.
"""

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from bs4 import BeautifulSoup

from filingcopilot.domain import DEFAULT_DOMAIN
from filingcopilot.models import Filing
from filingcopilot.periods import period_kind

ROOT = Path(__file__).resolve().parents[1]

CONCEPTS = {
    "us-gaap:NetIncomeLossAvailableToCommonStockholdersDiluted": "diluted_numerator",
    "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic": "basic_numerator_not_diluted",
    "us-gaap:NetIncomeLoss": "net_income_not_diluted_numerator",
    "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding": "diluted_denominator",
    "us-gaap:EarningsPerShareDiluted": "reported_eps_not_underlying_input",
}


def inspect_source(raw, filing, as_of):
    if hashlib.sha256(raw).hexdigest() != filing.content_sha256:
        raise ValueError("Retained source bytes differ from the filing SHA256")
    profile = DEFAULT_DOMAIN.financial_profiles.get(filing.ticker)
    if not profile or profile.cik != filing.cik or profile.taxonomy != "us-gaap":
        raise ValueError("Explicit native US-GAAP issuer profile required")
    if filing.form not in ("20-F", "20-F/A") or filing.filed > as_of:
        raise ValueError("Annual source was not public within the requested scope")
    soup = BeautifulSoup(raw, "html.parser")
    ids = defaultdict(list)
    for n in soup.find_all(id=True):
        ids[n["id"]].append(n)

    def unique(key):
        nodes = ids.get(key, [])
        return nodes[0] if len(nodes) == 1 else None

    rows, rejected = [], []
    for node in soup.find_all("ix:nonfraction"):
        role = CONCEPTS.get(node.get("name"))
        if role is None:
            continue
        reason = None
        context = unique(node.get("contextref"))
        unit = unique(node.get("unitref"))
        identity = context.find("xbrli:identifier") if context else None
        start_node = context.find("xbrli:startdate") if context else None
        end_node = context.find("xbrli:enddate") if context else None
        try:
            start = date.fromisoformat(start_node.get_text().strip())
            end = date.fromisoformat(end_node.get_text().strip())
        except (AttributeError, ValueError):
            start = end = None
        # Restrict the inventory to the annual period used in this filing.
        if end and end != filing.period_end:
            continue
        if (
            context is None or identity is None
            or identity.get("scheme") != "http://www.sec.gov/CIK"
            or identity.get_text().strip().zfill(10) != filing.cik
            or context.find(["xbrli:segment", "xbrli:scenario", "xbrldi:explicitmember", "xbrldi:typedmember"])
            or start is None or end is None or period_kind(start, end, filing.form) != "annual"
            or unique(node.get("id")) is not node
        ):
            reason = "identity_period_dimensions_or_duplicate_id"
        measure = profile.currency if role != "diluted_denominator" else "shares"
        if role == "reported_eps_not_underlying_input":
            numerator = unit.find("xbrli:unitnumerator") if unit else None
            denominator = unit.find("xbrli:unitdenominator") if unit else None
            valid_unit = (
                numerator is not None and denominator is not None
                and numerator.get_text().strip() == "iso4217:" + profile.currency
                and denominator.get_text().strip() == "xbrli:shares"
                and len(unit.find_all("xbrli:measure")) == 2
            )
        else:
            valid_unit = (
                unit is not None and not unit.find("xbrli:divide")
                and len(unit.find_all("xbrli:measure")) == 1
                and unit.get_text().strip() == ("xbrli:shares" if measure == "shares" else "iso4217:" + measure)
            )
        if not valid_unit:
            reason = reason or "wrong_native_unit_or_convenience_translation"
        text = node.get_text().strip().replace("\u00a0", "")
        decimals = node.get("decimals", "")
        try:
            fmt = node.get("format", "").split(":")[-1]
            scale = int(node.get("scale", "0"))
            valid_number = (
                fmt in ("", "num-dot-decimal", "numdotdecimal")
                and re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", text)
                and -12 <= scale <= 12 and node.get("sign", "") in ("", "-")
                and node.get("xsi:nil") not in ("true", "1")
                and (decimals == "INF" or re.fullmatch(r"-?\d{1,2}", decimals) and -12 <= int(decimals) <= 12)
            )
            if not valid_number:
                raise ValueError("Unsupported numeric representation")
            with localcontext() as numeric_context:
                numeric_context.prec = max(50, len(text) + abs(scale) + 4)
                value = Decimal(text.replace(",", "")) * Decimal(10) ** scale
                if node.get("sign") == "-":
                    value = -value
            if role == "diluted_denominator" and value <= 0:
                reason = reason or "nonpositive_diluted_share_denominator"
        except (ValueError, InvalidOperation):
            reason = reason or "malformed_numeric_precision_or_scale"
            value = None
        record = {
            "id": node.get("id"), "concept": node.get("name"), "role": role,
            "raw_text": text, "scale": node.get("scale", "0"), "decimals": decimals,
            "sign": node.get("sign", ""), "context_id": node.get("contextref"),
            "unit_id": node.get("unitref"), "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        }
        if reason:
            rejected.append({**record, "reason": reason})
        else:
            rows.append({**record, "reported_value": str(value), "unit": unit.get_text(" ", strip=True),
                         "reports_infinite_decimals": decimals == "INF"})
    exact_components = {}
    for role in ("diluted_numerator", "diluted_denominator"):
        candidates = [r for r in rows if r["role"] == role]
        exact_components[role] = (
            bool(candidates) and all(r["reports_infinite_decimals"] for r in candidates)
            and len({(Decimal(r["reported_value"]), r["start"], r["end"], r["unit"]) for r in candidates}) == 1
        )
    periods = {
        (r["start"], r["end"]) for r in rows
        if r["role"] in ("diluted_numerator", "diluted_denominator")
    }
    aligned = all(exact_components.values()) and len(periods) == 1
    return {
        "filing": filing.model_dump(mode="json"), "as_of": as_of.isoformat(),
        "candidates": rows, "rejected": rejected,
        "unrounded_component_candidates": exact_components,
        "unrounded_component_periods_align": aligned,
        "ready": False, "ads_eps": None, "ads_pe": None,
        "missing": ["source_bound_effective_ADS_ratio", "dated_sourced_FX_snapshot_with_direction"]
        + [role + "_unrounded" for role, present in exact_components.items() if not present]
        + (["aligned_native_annual_component_periods"] if all(exact_components.values()) and not aligned else []),
        "scope": "Mechanical native annual input inventory only. Reported precision and basic/net income roles are retained; no ADS conversion, source-semantic acceptance or investment assessment.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Do not replace an existing source inventory")
    started, rows = time.perf_counter(), []
    for path in args.replay:
        replay = json.loads(path.read_text(encoding="utf-8-sig"))
        filing = Filing.model_validate(max(replay["sources"], key=lambda f: f["period_end"]))
        source = (ROOT / replay["scope"] / "sec-cache" / (hashlib.sha256(filing.source_url.encode()).hexdigest() + ".cache")).resolve()
        if not source.is_relative_to((ROOT / ".filingcopilot").resolve()):
            raise ValueError("Replay source path must remain inside retained workspace caches")
        rows.append(inspect_source(source.read_bytes(), filing, date.fromisoformat(replay["as_of"])))
    report = {"schema": "filingcopilot.ads-input-inventory/v1", "seconds": round(time.perf_counter() - started, 3),
              "network_calls": 0, "model_calls": 0, "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"issuers": len(rows), "ready": sum(r["ready"] for r in rows), "derived_eps": 0}))


if __name__ == "__main__":
    main()
