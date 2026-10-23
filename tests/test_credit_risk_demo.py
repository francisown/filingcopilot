from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / "examples" / "credit-risk" / "demo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("credit_risk_demo", DEMO_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo()


def test_ingest_answer_and_verify(tmp_path):
    info = demo.ingest_corpus(home=tmp_path, reset=True)
    assert info["documents"] == 3
    assert info["passages"] >= 30

    result = demo.answer_question(
        "What is the maximum DTI for a prime unsecured loan?", home=tmp_path
    )
    assert result["status"] == "answered"
    assert result["citations"]
    assert result["citations"][0]["document"] == "credit-policy-v3.md"
    assert "43%" in result["answer"]

    package_path = tmp_path / "package.json"
    package = demo.build_package(
        "Who must approve an exception above 10% over the policy limit?",
        home=tmp_path,
        output=package_path,
    )
    assert package["package_sha256"]
    report = demo.verify_package(package_path)
    assert report["status"] == "consistent", report


def test_verifier_rejects_edited_quote(tmp_path):
    demo.ingest_corpus(home=tmp_path, reset=True)
    package_path = tmp_path / "package.json"
    demo.build_package(
        "What is the maximum LTV for a used auto loan?", home=tmp_path, output=package_path
    )
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    payload["citations"][0]["quote"] = "A quote that is not in the source document."
    payload["package_sha256"] = demo.canonical_hash(payload)
    package_path.write_text(json.dumps(payload), encoding="utf-8")

    report = demo.verify_package(package_path)
    assert report["status"] == "invalid"
    assert any("quote not found" in error for error in report["errors"])


def test_eval_retrieval_floor(tmp_path):
    demo.ingest_corpus(home=tmp_path, reset=True)
    summary = demo.run_eval(home=tmp_path)
    assert summary["questions"] == 20
    assert summary["document_hit_at_3"] >= 0.95
    assert summary["mrr"] >= 0.90
