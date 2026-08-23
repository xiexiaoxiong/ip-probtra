from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from invalidity.providers import (
    ArxivProvider,
    EvidenceStage,
    GooglePatentsProvider,
    ManualProvider,
    ProviderContentError,
    QueryValidationError,
    SearchQuery,
)


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(
        self,
        *,
        url: str,
        content: bytes | str = b"",
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        json_data: Any = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.content = content.encode("utf-8") if isinstance(content, str) else content
        self.text = self.content.decode("utf-8", errors="replace")
        self.headers = dict(headers or {})
        self._json_data = json_data

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


class FixtureTransport:
    def __init__(self, routes: Mapping[str, FakeResponse]) -> None:
        self.routes = dict(routes)
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def resolve(_hostname: str) -> list[str]:
        return ["93.184.216.34"]

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "params": dict(params or {}),
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        try:
            return self.routes[url]
        except KeyError as exc:
            raise AssertionError(f"unexpected URL: {url}") from exc


def _google_detail_html() -> str:
    return """
    <!doctype html>
    <html><head>
      <meta name="DC.title" content="Robot cleaner with lifting cleaning pad">
      <meta name="citation_pdf_url"
            content="https://patentimages.storage.googleapis.com/example/CN112345678A.pdf">
    </head><body>
      <dd itemprop="publicationNumber">CN 112345678 A</dd>
      <time itemprop="priorityDate" datetime="2019-03-01"></time>
      <time itemprop="filingDate" datetime="2020-02-10"></time>
      <time itemprop="publicationDate" datetime="2021-01-02"></time>
      <section itemprop="abstract">
        A mobile robot includes a cleaning pad that can be raised above a floor.
      </section>
      <section itemprop="description">
        The chassis carries an actuator connected to the pad support. A controller
        commands the actuator after a floor sensor detects carpet, so the cleaning
        pad moves from a working position to a lifted position.
        <img id="imgf0001" class="patent-full-image" src="/images/CN112345678A-fig1.png">
      </section>
      <section itemprop="claims">
        1. A cleaning robot comprising a mobile chassis, a cleaning pad, and an
        actuator configured to selectively raise the cleaning pad.
      </section>
    </body></html>
    """


def test_query_guard_rejects_isolated_terms_and_accepts_anchored_dict() -> None:
    with pytest.raises(QueryValidationError):
        SearchQuery.from_input("中空孔")
    with pytest.raises(QueryValidationError):
        SearchQuery.from_input(
            {"text": "扫地机器人", "subject_terms": ["扫地机器人"]}
        )

    query = SearchQuery.from_input(
        {
            "text": "扫地机器人 升降拖布",
            "subject_terms": ["扫地机器人"],
            "feature_terms": ["升降拖布"],
        }
    )
    assert query.text == "扫地机器人 升降拖布"


def test_query_guard_allows_only_internal_trusted_fixed_plan_expression() -> None:
    trusted = SearchQuery(
        text="(AN:(示例公司)) AND (TTL:(水枪) OR ABST:(水枪))",
        trusted_fixed_plan=True,
    ).validated()
    assert trusted.trusted_fixed_plan is True

    with pytest.raises(QueryValidationError):
        SearchQuery.from_input(
            {
                "text": "水枪",
                "subject_terms": ["水枪"],
                "trusted_fixed_plan": True,
            }
        )


def test_google_xhr_search_returns_leads_and_preserves_untrusted_before_filter() -> None:
    payload = json.loads((FIXTURES / "google_patents_search.json").read_text())
    xhr_url = "https://patents.google.com/xhr/query"
    transport = FixtureTransport(
        {
            xhr_url: FakeResponse(
                url=xhr_url,
                json_data=payload,
                content=json.dumps(payload),
                headers={"content-type": "application/json"},
            )
        }
    )
    provider = GooglePatentsProvider(transport=transport, timeout_seconds=7.5)

    records = provider.search(
        {
            "text": "robot cleaner lifting mop",
            "subject_terms": ["robot cleaner"],
            "feature_terms": ["lifting mop"],
        },
        server_before="2020-06-15",
    )

    # The fixture intentionally returns a 2021 document despite server_before.
    # It must remain visible for local classification rather than being trusted
    # or silently discarded by the adapter.
    assert len(records) == 2
    assert records[0].stage is EvidenceStage.LEAD
    assert records[0].publication_date == "2021-01-02"
    assert records[0].publication_number == "CN112345678A"
    assert records[0].provenance["provider_server_before_is_discovery_only"] is True
    assert "language=ENGLISH" in transport.calls[0]["params"]["url"]
    assert "before=publication%3A20200615" in transport.calls[0]["params"]["url"]
    assert transport.calls[0]["params"]["exp"] == ""
    assert transport.calls[0]["timeout"] == 7.5


def test_google_search_uses_auditable_yahoo_link_fallback_after_xhr_5xx(
    monkeypatch: Any,
) -> None:
    xhr_url = "https://patents.google.com/xhr/query"
    yahoo_html = """
        <html><body><div class="dd algo">
          <div class="compTitle">
            <a href="https://r.search.yahoo.com/RU=https%3a%2f%2fpatents.google.com%2fpatent%2fUS20110202175A1%2fen/RK=2/RS=opaque">
              <h3>US20110202175A1 - Mobile robot for cleaning</h3>
            </a>
          </div>
          <div class="compText">
            A mobile cleaning robot with a lifting cleaning pad.
          </div>
        </div></body></html>
    """
    transport = FixtureTransport(
        {
            xhr_url: FakeResponse(
                url=xhr_url,
                status_code=503,
                content="<html>temporarily unavailable</html>",
                headers={"content-type": "text/html"},
            ),
        }
    )
    monkeypatch.setattr(
        "invalidity.providers._curl_get_search_html",
        lambda **_kwargs: yahoo_html,
    )

    records = GooglePatentsProvider(transport=transport).search(
        {
            "text": "robot cleaner lifting pad",
            "subject_terms": ["robot cleaner"],
            "feature_terms": ["lifting pad"],
        },
        max_results=1,
        country="CN",
        server_after="2019-01-01",
        server_before="2020-06-15",
        filing_before="2020-01-01",
    )

    assert len(records) == 1
    assert records[0].publication_number == "US20110202175A1"
    assert records[0].source_url == (
        "https://patents.google.com/patent/US20110202175A1/en"
    )
    assert records[0].stage is EvidenceStage.LEAD
    assert records[0].provenance["discovery_provider"] == "yahoo_web_fallback"
    assert records[0].provenance["fallback_reason"] == "ProviderRequestError"
    assert records[0].provenance["search_snippet_is_lead_only"] is True
    assert records[0].provenance["fallback_transport"] == "curl_trusted_host"
    assert records[0].provenance["fallback_country"] == "CN"
    assert (
        "site:patents.google.com/patent/CN"
        in records[0].provenance["fallback_query"]
    )
    assert "after:2019-01-01" in records[0].provenance["fallback_query"]
    assert "before:2020-06-15" in records[0].provenance["fallback_query"]
    assert records[0].provenance["fallback_scope_degraded"] is True
    assert records[0].provenance["fallback_scope_degraded_reasons"] == [
        "filing_date_filter_not_supported"
    ]
    assert len(records[0].provenance["fallback_response_sha256"]) == 64
    assert records[0].provenance["fallback_response_bytes"] > 0
    assert [item["url"] for item in transport.calls] == [xhr_url]

    monkeypatch.setattr(
        "invalidity.providers._curl_get_search_html",
        lambda **_kwargs: "<html><div class='cf-turnstile'>challenge</div></html>",
    )
    with pytest.raises(ProviderContentError, match="访问验证页"):
        GooglePatentsProvider(transport=transport).search(
            {
                "text": "robot cleaner lifting pad",
                "subject_terms": ["robot cleaner"],
                "feature_terms": ["lifting pad"],
            },
            max_results=1,
        )


def test_google_conflicting_application_discovery_preserves_both_window_bounds() -> None:
    payload = json.loads((FIXTURES / "google_patents_search.json").read_text())
    xhr_url = "https://patents.google.com/xhr/query"
    transport = FixtureTransport(
        {
            xhr_url: FakeResponse(
                url=xhr_url,
                json_data=payload,
                content=json.dumps(payload),
                headers={"content-type": "application/json"},
            )
        }
    )

    records = GooglePatentsProvider(transport=transport).search(
        {
            "text": "robot cleaner lifting mop",
            "subject_terms": ["robot cleaner"],
            "feature_terms": ["lifting mop"],
        },
        country="CN",
        server_after="2020-06-15",
        server_before="2021-06-01",
        filing_before="2020-06-15",
    )

    encoded = transport.calls[0]["params"]["url"]
    assert "country=CN" in encoded
    assert "after=publication%3A20200615" in encoded
    assert "before=publication%3A20210601" in encoded
    assert "before=filing%3A20200615" in encoded
    assert records[0].provenance["provider_server_after"] == "2020-06-15"
    assert records[0].provenance["provider_filing_before"] == "2020-06-15"


def test_google_detail_parses_full_text_pdf_images_and_hash() -> None:
    detail_url = "https://patents.google.com/patent/CN112345678A/en"
    transport = FixtureTransport(
        {
            detail_url: FakeResponse(
                url=detail_url,
                content=_google_detail_html(),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        }
    )
    provider = GooglePatentsProvider(transport=transport)

    record = provider.retrieve("CN112345678A")

    assert record.stage is EvidenceStage.RETRIEVED
    assert "selectively raise the cleaning pad" in (record.claims or "")
    assert "controller" in (record.description or "")
    assert record.pdf_url == (
        "https://patentimages.storage.googleapis.com/example/CN112345678A.pdf"
    )
    assert record.image_urls == (
        "https://patents.google.com/images/CN112345678A-fig1.png",
    )
    assert record.content_sha256 and len(record.content_sha256) == 64
    assert record.provenance["public_date_evidence"]
    assert record.model_dump()["stage"] == "retrieved_document"


def test_google_local_qualification_does_not_trust_server_before() -> None:
    record = ManualProvider().ingest(
        {
            "provider": "google_patents",
            "source_type": "patent",
            "external_id": "US20210001234A1",
            "title": "Late document",
            "source_url": "https://patents.google.com/patent/US20210001234A1/en",
            "publication_number": "US20210001234A1",
            "publication_date": "2021-01-02",
            "filing_date": "2019-01-01",
            "content": "Verified full patent content " * 10,
            "public_date_evidence": "Google Patents detail publicationDate",
        }
    )
    # Preserve the Google identity; the evidence DTO itself is provider-neutral.
    record = replace(record, provider="google_patents")
    provider = GooglePatentsProvider(transport=FixtureTransport({}))

    qualified = provider.qualify(
        record,
        critical_date="2020-06-15",
        content_relevance_verified=True,
    )

    assert qualified.stage is EvidenceStage.RETRIEVED
    assert qualified.eligibility["category"] == "post_date_lead"
    assert "candidate_not_date_eligible" in qualified.qualification_issues


def test_google_pdf_download_is_separate_and_rejects_html_shell() -> None:
    pdf_url = "https://example.test/patent.pdf"
    good_transport = FixtureTransport(
        {
            pdf_url: FakeResponse(
                url=pdf_url,
                content=b"%PDF-1.7\nfixture patent file\n%%EOF",
                headers={"content-type": "application/pdf"},
            )
        }
    )
    artifact = GooglePatentsProvider(transport=good_transport).download_pdf(pdf_url)
    assert artifact.kind == "pdf"
    assert len(artifact.content_sha256) == 64

    bad_transport = FixtureTransport(
        {
            pdf_url: FakeResponse(
                url=pdf_url,
                content="<html>login</html>",
                headers={"content-type": "text/html"},
            )
        }
    )
    with pytest.raises(ProviderContentError):
        GooglePatentsProvider(transport=bad_transport).download_pdf(pdf_url)


def test_arxiv_atom_search_stays_lead_until_pdf_is_downloaded() -> None:
    atom_url = "https://export.arxiv.org/api/query"
    pdf_url = "https://arxiv.org/pdf/1803.00001v2"
    transport = FixtureTransport(
        {
            atom_url: FakeResponse(
                url=atom_url,
                content=(FIXTURES / "arxiv_atom.xml").read_bytes(),
                headers={"content-type": "application/atom+xml"},
            ),
            pdf_url: FakeResponse(
                url=pdf_url,
                content=b"%PDF-1.7\narxiv fixture\n%%EOF",
                headers={"content-type": "application/pdf"},
            ),
        }
    )
    provider = ArxivProvider(transport=transport, timeout_seconds=9)

    leads = provider.search("robot cleaner lifting mop")
    assert len(leads) == 1
    assert leads[0].stage is EvidenceStage.LEAD
    assert leads[0].external_id == "1803.00001"
    assert leads[0].publication_date == "2018-03-01"

    retrieved = provider.retrieve(leads[0])
    assert retrieved.stage is EvidenceStage.RETRIEVED
    assert retrieved.artifacts[0]["kind"] == "pdf"
    assert retrieved.content_sha256

    qualified = provider.qualify(
        retrieved,
        critical_date="2020-01-01",
        content_relevance_verified=True,
    )
    assert qualified.stage is EvidenceStage.QUALIFIED
    assert qualified.eligibility["inventive_step_eligible"] is True


def test_manual_provider_uses_same_retrieval_and_qualification_gates() -> None:
    provider = ManualProvider()
    lead = provider.ingest(
        {
            "title": "Product manual",
            "source_url": "https://example.test/manual",
            "publication_date": "2018-02-03",
        }
    )
    assert lead.stage is EvidenceStage.LEAD

    retrieved = provider.ingest(
        {
            "title": "Product manual",
            "source_url": "https://example.test/manual.pdf",
            "publication_date": "2018-02-03",
            "public_date_evidence": "archived catalog dated 2018-02-03",
            "content": b"real manual bytes with a lifting cleaning assembly",
        }
    )
    assert retrieved.stage is EvidenceStage.RETRIEVED

    blocked = provider.qualify(
        retrieved,
        critical_date="2020-01-01",
        content_relevance_verified=False,
        source_chain_verified=True,
        public_date_verified=True,
    )
    assert blocked.stage is EvidenceStage.RETRIEVED
    assert "technical_disclosure_not_verified" in blocked.qualification_issues

    qualified = provider.qualify(
        retrieved,
        critical_date="2020-01-01",
        content_relevance_verified=True,
        source_chain_verified=True,
        public_date_verified=True,
    )
    assert qualified.stage is EvidenceStage.QUALIFIED
    assert qualified.model_dump()["stage"] == "qualified_evidence"
