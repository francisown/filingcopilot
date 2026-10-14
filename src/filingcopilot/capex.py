"""Separate cash PP&E purchases from source-bound sale/incentive receipts."""

from datetime import date
from decimal import Decimal, localcontext

from .models import Calculation, Fact
from .periods import evidence_period
from .ttm import trailing_calculation, trailing_window

CAPEX_NOTE = "现金 PP&E 购置毛额减同期间出售及激励收款为购置净额。毛额与净额的同比各用同口径两期；均不等同全部资本投入（total capital investment），不含非现金取得资产、融资租赁本金、收购及其他投资。"


def net_capex_calculation(tools, inputs):
    if len(inputs) != 2 or any(not e for e in inputs):
        raise ValueError("Net purchases need observed gross purchases and receipts")
    gross, receipts = inputs
    kinds = [e["kind"] for e in inputs]
    if kinds == ["calculation", "calculation"]:
        if any(e.get("operation") != "trailing_sum" for e in inputs):
            raise ValueError("Net purchases need two aligned TTM component flows")
        for e in inputs:
            if trailing_calculation(tools, e["inputs"])["evidence"] != e:
                raise ValueError("TTM net purchase input differs from its source-bound calculation")
        windows = [trailing_window(e) for e in inputs]
        if not windows[0] or windows[0] != windows[1]:
            raise ValueError("TTM net purchases need the same exact period")
        if len(gross["inputs"]) != len(receipts["inputs"]):
            raise ValueError("TTM net purchases need matching source components")
        pairs = zip(gross["inputs"], receipts["inputs"])
    elif kinds == ["fact", "fact"]:
        pairs = [(gross, receipts)]
    else:
        raise ValueError("Net purchases need same-type facts or TTM component flows")
    for a, b in pairs:
        if a["kind"] != "fact" or b["kind"] != "fact":
            raise ValueError("Net purchase components must be source facts")
        for e in (a, b):
            Fact.model_validate({k: v for k, v in e.items() if k not in ("kind", "filing")})
        if (
            a["metric"] != "cash_capex"
            or b["metric"] != "ppe_sale_incentive_proceeds"
            or b["concept"] != "amzn:ProceedsFromPropertyPlantAndEquipmentSalesAndIncentives"
            or a["ticker"] != "AMZN"
            or b["ticker"] != a["ticker"]
            or any(e["filing"]["cik"] != "0001018724" for e in (a, b))
            or a["unit"] != b["unit"]
            or a["start"] is None
            or (a["start"], a["end"], a["filing_id"]) != (b["start"], b["end"], b["filing_id"])
            or evidence_period(a) is None
            or any(Decimal(e["value"]) < 0 for e in (a, b))
            or any(date.fromisoformat(e["filing"]["filed"]) > tools.as_of for e in (a, b))
            or any(date.fromisoformat(e["end"]) > tools.as_of for e in (a, b))
        ):
            raise ValueError(
                "Net purchases require disclosed same-company, filing and period receipts"
            )
    if gross["ticker"] != receipts["ticker"] or gross["unit"] != receipts["unit"]:
        raise ValueError("Net purchases need the same company and unit")
    from .corpus import stable_id

    with localcontext() as context:
        context.prec = 28
        value = Decimal(gross["value"]) - Decimal(receipts["value"])
    result = Calculation(
        id=stable_id("calc", "net_cash_capex", gross["id"], receipts["id"]),
        operation="net_cash_capex",
        input_ids=[gross["id"], receipts["id"]],
        value=value,
        unit=gross["unit"],
        label="现金PP&E购置净额（毛额−出售及激励收款）",
        formula="cash_capex - ppe_sale_incentive_proceeds",
        ticker=gross["ticker"],
        fiscal_year=gross["fiscal_year"],
    ).model_dump(mode="json")
    result.update(kind="calculation", inputs=inputs)
    tools.ledger[result["id"]] = result
    return {"evidence": result}


def capex_basis(tools, current, previous=None, component_ids=None):
    parts = [tools.ledger[i] for i in component_ids] if component_ids else current["inputs"]
    gross = parts[1]
    receipts = parts[2] if len(parts) == 3 else None
    net = (
        tools.calculate("net_cash_capex", gross["id"], receipts["id"])["evidence"]
        if receipts
        else None
    )
    prior = growth = None
    if net and previous and len(previous["inputs"]) == 3:
        a, b = previous["inputs"][1:]
        prior = tools.calculate("net_cash_capex", a["id"], b["id"])["evidence"]
        try:
            growth = tools.calculate("growth", net["id"], prior["id"])["evidence"]
        except ValueError:
            pass  # A nonpositive base or mismatched period is not percentage growth.
    return {
        "scope": "net_ppe_purchases" if net else "gross_ppe_purchases",
        "gross_id": gross["id"],
        "receipts_id": receipts["id"] if receipts else None,
        "net_id": net["id"] if net else None,
        "prior_net_id": prior["id"] if prior else None,
        "net_growth_id": growth["id"] if growth else None,
        "note": CAPEX_NOTE
        if net
        else CAPEX_NOTE + " 未取得完整收款调整，净额未计算；不把缺值当零。",
    }
