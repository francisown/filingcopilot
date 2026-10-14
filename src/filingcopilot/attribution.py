"""Compare two frozen packages without an LLM, network calls or mutable runs.

Each valuation counterfactual starts at the old case and changes exactly one
assumption. Interactions and changed EPS stay in residual. This original module
uses FilingCopilot's package protocol rather than a competitor's implementation.
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal, localcontext

from .audit import Package, audit_package, collect_evidence, digest

SCHEMA = "filingcopilot.research-attribution/v1"
FACTORS = ("growth_pct", "terminal_pe", "discount_pct", "years")


def _key(item: dict) -> str | None:
    kind = item["kind"]
    if kind in ("fact", "statement_fact"):
        key = [kind, item["ticker"], item["metric"], item["concept"], item["unit"],
               item["fiscal_year"], item.get("start"), item["end"], item.get("dimensions", {})]
    elif kind == "calculation":
        key = [kind, item["ticker"], item["operation"], item["unit"],
               [_key(child) for child in item.get("inputs", [])]]
    elif kind == "market":
        snapshot = item["snapshot"]
        key = [kind, item["ticker"], snapshot["currency"], snapshot["basis"], snapshot["period"]]
    elif kind == "valuation":
        key = [kind, item["ticker"], item["snapshot"]["currency"], "supplier_ttm_eps"]
    else:
        return None
    return json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _observation(item: dict) -> dict:
    filing = item.get("filing", {})
    snapshot = item.get("snapshot", {})
    value = snapshot.get("quote_value") if item["kind"] == "market" else (
        snapshot.get("value") if item["kind"] == "valuation" else item["value"]
    )
    return {
        "evidence_id": item["id"], "value": str(value), "evidence_sha256": digest(item),
        "filing_id": filing.get("id"), "accession": filing.get("accession"),
        "filed": filing.get("filed"), "content_sha256": filing.get("content_sha256"),
        "source_url": item.get("source_url") or snapshot.get("source_url"),
        "locator": item.get("locator"), "retrieved_at": snapshot.get("retrieved_at"),
    }


def _numeric_diff(old: dict, new: dict) -> list[dict]:
    def groups(ledger):
        grouped = {}
        for item in ledger.values():
            key = _key(item)
            if key is not None:
                grouped.setdefault(key, []).append(_observation(item))
        return {key: sorted(rows, key=lambda row: row["evidence_id"]) for key, rows in grouped.items()}

    before, after = groups(old), groups(new)
    result = []
    for key in sorted(before.keys() | after.keys()):
        left, right = before.get(key, []), after.get(key, [])
        if left == right:
            continue
        item = {"key": key, "status": "changed" if left and right else "added" if right else "removed",
                "before": left, "after": right}
        if len(left) == len(right) == 1:
            with localcontext() as ctx:
                ctx.prec = 40
                item["delta"] = str(Decimal(right[0]["value"]) - Decimal(left[0]["value"]))
        else:
            item["note"] = "未匹配为唯一的同口径观察，保留全部版本；不猜测变化量。"
        result.append(item)
    return result


def _narrative_diff(before: dict, after: dict, old: dict, new: dict) -> list[dict]:
    def claims(package):
        return {c["index"]: c for c in package["research"]["answer"]
                if c["kind"] in ("analysis", "narrative", "excerpt")}

    left_claims, right_claims = claims(before), claims(after)
    result = []
    for index in sorted(left_claims.keys() | right_claims.keys()):
        left, right = left_claims.get(index), right_claims.get(index)
        left_ids = sorted((left or {}).get("evidence_ids", []))
        right_ids = sorted((right or {}).get("evidence_ids", []))
        versions_changed = left_ids != right_ids or any(
            digest(old.get(key)) != digest(new.get(key)) for key in set(left_ids + right_ids)
        )
        text_changed = (left or {}).get("text") != (right or {}).get("text")
        if not text_changed and not versions_changed:
            continue
        result.append({
            "index": index, "matching_basis": "saved_claim_index",
            "status": "needs_review" if left and right and text_changed and not versions_changed
            else "added" if left is None else "removed" if right is None else "evidence_changed",
            "before_text": (left or {}).get("text"), "after_text": (right or {}).get("text"),
            "before_evidence_ids": left_ids, "after_evidence_ids": right_ids,
            "evidence_versions_changed": versions_changed, "text_changed": text_changed,
        })
    return result


def _scenario(package):
    traces = [t["result"] for t in package["research"]["trace"] if t.get("stage") == "valuation_scenarios"]
    return traces[0] if traces else None


def _value(eps: Decimal, assumptions: dict) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 40
        return (eps * (1 + Decimal(assumptions["growth_pct"]) / 100) ** assumptions["years"]
                * Decimal(assumptions["terminal_pe"])
                / (1 + Decimal(assumptions["discount_pct"]) / 100) ** assumptions["years"])


def _valuation(before: dict, after: dict) -> dict:
    left, right = _scenario(before), _scenario(after)
    if not left or not right:
        return {"status": "not_available", "cases": [], "note": "两次研究未同时保存估值情景，无法归因。"}
    if left["currency"] != right["currency"] or left["request"]["basis"] != right["request"]["basis"]:
        return {"status": "incompatible", "cases": [], "note": "币种或 EPS 口径不同，未计算跨口径贡献。"}
    old_rows = {row["name"]: row for row in left["rows"]}
    new_rows = {row["name"]: row for row in right["rows"]}
    result = []
    for name in sorted(old_rows.keys() | new_rows.keys()):
        if name not in old_rows or name not in new_rows:
            result.append({"name": name, "status": "added" if name in new_rows else "removed"})
            continue
        old_row, new_row = old_rows[name], new_rows[name]
        old = {"growth_pct": old_row["growth_pct"], "terminal_pe": old_row["terminal_pe"],
               "discount_pct": left["request"]["discount_pct"], "years": left["request"]["years"]}
        new = {"growth_pct": new_row["growth_pct"], "terminal_pe": new_row["terminal_pe"],
               "discount_pct": right["request"]["discount_pct"], "years": right["request"]["years"]}
        with localcontext() as ctx:
            ctx.prec = 40
            base = Decimal(left["base_eps"])
            old_value, new_value = Decimal(old_row["discounted_value"]), Decimal(new_row["discounted_value"])
            contributions = []
            for factor in FACTORS:
                if Decimal(str(old[factor])) == Decimal(str(new[factor])):
                    continue
                assumptions = {**old, factor: new[factor]}
                counterfactual = _value(base, assumptions)
                contributions.append({"factor": factor, "from": old[factor], "to": new[factor],
                                      "assumptions": assumptions, "counterfactual_value": str(counterfactual),
                                      "contribution": str(counterfactual - old_value)})
            total = new_value - old_value
            residual = total - sum((Decimal(c["contribution"]) for c in contributions), Decimal(0))
            result.append({"name": name, "before_value": str(old_value), "after_value": str(new_value),
                           "total_change": str(total), "old_assumptions": old, "new_assumptions": new,
                           "assumption_changes": contributions, "residual": str(residual),
                           "before_eps_id": left["eps_id"], "after_eps_id": right["eps_id"],
                           "base_eps_changed": Decimal(left["base_eps"]) != Decimal(right["base_eps"]),
                           "status": "changed" if contributions or total else "unchanged"})
    return {"status": "available", "currency": left["currency"], "cases": result,
            "formula": left["formula"],
            "note": "每个反事实只替换旧情景的一个假设；EPS 变化、假设交互及未解释部分保留在 residual。"}


def _body(before: dict, after: dict) -> dict:
    old, new = Package.model_validate(before), Package.model_validate(after)
    if not old.focus_ticker or old.focus_ticker != new.focus_ticker:
        raise ValueError("版本归因需要明确且相同的公司代码。")
    if old.research.as_of > new.research.as_of:
        raise ValueError("后一次研究的知悉日期不能早于前一次。")
    old_ledger, new_ledger = collect_evidence(old.research.evidence), collect_evidence(new.research.evidence)
    return {"schema_version": SCHEMA, "before_run_id": old.research.id, "after_run_id": new.research.id,
            "before_package_sha256": old.manifest["payload_sha256"],
            "after_package_sha256": new.manifest["payload_sha256"], "focus_ticker": old.focus_ticker,
            "before_as_of": old.research.as_of.isoformat(), "after_as_of": new.research.as_of.isoformat(),
            "numeric_changes": _numeric_diff(old_ledger, new_ledger),
            "narrative_changes": _narrative_diff(before, after, old_ledger, new_ledger),
            "valuation": _valuation(before, after),
            "before_package": copy.deepcopy(before), "after_package": copy.deepcopy(after),
            "limitations": ["归因只覆盖两个研究包内已冻结的观察，不推测未入账资料。",
                            "叙述按已保存断言序号对照；重排可能改变对应关系，完整语义需人工复核。",
                            "needs_review 不自动裁决哪个结论正确；估值贡献不是预测或投资建议。"]}


def build_attribution(before: dict, after: dict) -> dict:
    for package in (before, after):
        if audit_package(package)["status"] != "consistent":
            raise ValueError("归因输入研究包未通过离线复核。")
    body = _body(before, after)
    return {**body, "manifest": {"algorithm": "sha256", "payload_sha256": digest(body)}}


def audit_attribution(payload: dict, **source_options) -> dict:
    checks, package_reports = [], {}
    try:
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA:
            raise ValueError("Unknown attribution schema")
        body = {key: value for key, value in payload.items() if key != "manifest"}
        checks.append({"code": "manifest", "status": "pass" if payload.get("manifest") == {
            "algorithm": "sha256", "payload_sha256": digest(body)} else "fail"})
        for side in ("before", "after"):
            package_reports[side] = audit_package(payload[side + "_package"], **source_options)
            checks.append({"code": side + "_package", "status": "pass" if package_reports[side]["status"] == "consistent" else "fail"})
        expected = _body(payload["before_package"], payload["after_package"])
        checks.append({"code": "attribution_replay", "status": "pass" if body == expected else "fail",
                       "detail": "从冻结原包重新计算全部数字、单因素反事实、residual 和叙述证据版本。"})
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        checks.append({"code": "structure", "status": "fail", "detail": str(exc)})
    failed = sum(c["status"] == "fail" for c in checks)
    return {"status": "invalid" if failed else "consistent", "checks_failed": failed, "checks": checks,
            "source_packages": package_reports,
            "limitations": ["无独立语料/原件时只证明包内一致性，哈希不能证明来源真实性。",
                            "此复核未重新审查叙述完整语义或投资判断。"]}
