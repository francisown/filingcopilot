import json
import sys
from datetime import date

import pytest

from filingcopilot import cli
from filingcopilot.audit import audit_package, create_package
from filingcopilot.domain import Company, load_domain
from filingcopilot.models import ResearchAnswer
from filingcopilot.service import FilingCopilot, directory_enriched_companies
from filingcopilot.settings import Settings
from filingcopilot.stock_directory import StockDirectory

# Integration, persistence, subprocess, or concurrency scope; included in full runs.
pytestmark = pytest.mark.slow


def test_directory_aliases_are_shared_by_live_and_audit_domain(tmp_path):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.corpus.register_company(
        "AMZN",
        Company(cik="0001018724", name="Amazon.com, Inc.", aliases=[]),
    )
    service.refresh_domain()
    assert "亚马逊" in service.domain.companies["AMZN"].aliases

    answer = ResearchAnswer(
        id="run-alias",
        question="亚马逊资料",
        as_of=date.today(),
        mode="reference",
        status="abstained",
        answer=[],
        gaps=["测试没有加载申报原件"],
        evidence=[],
        trace=[],
        usage={"model_calls": 0},
        elapsed_ms=0,
    )
    package = create_package(answer, service.domain, "AMZN")
    base = load_domain()
    trusted = load_domain(
        companies=directory_enriched_companies(
            {**base.model_dump()["companies"], **service.corpus.registered_companies()},
            StockDirectory(),
        )
    )
    assert audit_package(package, trusted_domain=trusted)["status"] == "consistent"


def test_cli_audit_against_corpus_rebuilds_the_enriched_domain(tmp_path, monkeypatch, capsys):
    service = FilingCopilot(Settings(home=tmp_path, embedding_mode="off"))
    service.corpus.register_company(
        "AMZN",
        Company(cik="0001018724", name="Amazon.com, Inc.", aliases=[]),
    )
    service.refresh_domain()
    answer = ResearchAnswer(
        id="run-cli-alias",
        question="亚马逊资料",
        as_of=date.today(),
        mode="reference",
        status="abstained",
        answer=[],
        gaps=["测试没有加载申报原件"],
        evidence=[],
        trace=[],
        usage={"model_calls": 0},
        elapsed_ms=0,
    )
    package_path = tmp_path / "package.json"
    package_path.write_text(
        json.dumps(create_package(answer, service.domain, "AMZN"), ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setenv("FC_HOME", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "argv",
        ["filingcopilot", "audit", str(package_path), "--against-corpus"],
    )
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "consistent"


def test_enrichment_does_not_add_ambiguous_aliases():
    result = directory_enriched_companies(
        {"TSM": {"cik": "0001046179", "name": "TSMC", "aliases": []}},
        StockDirectory(),
    )
    assert result["TSM"]["aliases"] == []
