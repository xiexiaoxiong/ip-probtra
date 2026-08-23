from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import fitz
import pytest

from invalidity.artifacts import ArtifactStore
from invalidity.contracts import SearchQuery
from invalidity.patent_retrieval import EpoFirstPatsnapPdfRetrievalProvider
from invalidity.providers import (
    EvidenceRecord,
    EvidenceStage,
    ProviderContentError,
    ProviderRequestError,
    RetrievedArtifact,
)
from invalidity.workflow import ConfiguredEvidenceGateway


PATENT_ID = "718ead9c-4f3c-4674-8f5a-24e126827269"
PUBLICATION_NUMBER = "EP3456789A1"


def _lead() -> EvidenceRecord:
    return EvidenceRecord(
        provider="patsnap",
        source_type="patent",
        external_id=PATENT_ID,
        title="P002 lead",
        source_url="https://connect.zhihuiya.com/search/patent/query-search-patent/v2",
        publication_number=PUBLICATION_NUMBER,
        raw_metadata={"patent_id": PATENT_ID},
        provenance={"discovery_provider": "patsnap"},
    )


def _pdf() -> bytes:
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), f"P020 {PUBLICATION_NUMBER}")
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


class _Patsnap:
    provider_name = "patsnap"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.last_search_artifact = RetrievedArtifact(
            provider="patsnap",
            kind="provider_search_response",
            source_url="https://connect.zhihuiya.com/search/patent/query-search-patent/v2",
            media_type="application/json",
            content=b'{"status":true,"error_code":0}',
        )
        self.download_calls: list[EvidenceRecord] = []
        self.last_media_artifacts: tuple[RetrievedArtifact, ...] = ()

    @staticmethod
    def _coerce_lead(value):
        assert isinstance(value, EvidenceRecord)
        return value

    def download_pdf(self, record: EvidenceRecord) -> RetrievedArtifact:
        self.download_calls.append(record)
        metadata = RetrievedArtifact(
            provider="patsnap",
            kind="provider_pdf_metadata_response",
            source_url="https://connect.zhihuiya.com/basic-patent-data/pdf-data",
            media_type="application/json",
            content=(
                b'{"status":true,"error_code":0,"data":{"patent_id":"'
                + PATENT_ID.encode()
                + b'"}}'
            ),
        )
        self.last_media_artifacts = (metadata,)
        if self.fail:
            raise ProviderContentError("P020 unavailable")
        return RetrievedArtifact(
            provider="patsnap",
            kind="pdf",
            source_url="https://open.zhihuiya.com/redacted.pdf",
            media_type="application/pdf",
            content=_pdf(),
            source_metadata_artifact=metadata,
        )

    def search(self, _expression: str, **_kwargs) -> list[EvidenceRecord]:
        return [_lead()]

    def close(self) -> None:
        return None


class _Epo:
    provider_name = "epo_ops"

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.calls: list[EvidenceRecord] = []
        self.last_retrieval_artifacts: tuple[RetrievedArtifact, ...] = ()

    def retrieve(self, record: EvidenceRecord) -> EvidenceRecord:
        self.calls.append(record)
        biblio = RetrievedArtifact(
            provider="epo_ops",
            kind="provider_biblio_response",
            source_url="https://ops.epo.org/3.2/rest-services/published-data",
            media_type="application/xml",
            content=b"<exchange-document/>",
        )
        self.last_retrieval_artifacts = (biblio,)
        if self.outcome == "network_error":
            raise ProviderRequestError("EPO OPS HTTP 503")
        if self.outcome == "not_found":
            raise ProviderRequestError("EPO OPS HTTP 404")
        provenance = {
            **dict(record.provenance),
            "retrieval_provider": "epo_ops",
            "retrieval_identity": {
                "requested_publication_number": PUBLICATION_NUMBER,
                "returned_publication_number": PUBLICATION_NUMBER,
                "match": "exact",
            },
            "publication_date_verified": True,
            "public_date_evidence": "EPO OPS biblio",
        }
        if self.outcome == "no_coverage":
            return replace(
                record,
                provider="epo_ops",
                publication_date="2019-01-01",
                provenance=provenance,
                qualification_issues=("ops_fulltext_not_available",),
            )
        artifact = RetrievedArtifact(
            provider="epo_ops",
            kind="pdf",
            source_url="https://ops.epo.org/official.pdf",
            media_type="application/pdf",
            content=_pdf(),
        )
        return replace(
            record,
            provider="epo_ops",
            stage=EvidenceStage.RETRIEVED,
            provenance=provenance,
            primary_artifact=artifact,
        )

    @staticmethod
    def qualify(record: EvidenceRecord, **_kwargs) -> EvidenceRecord:
        return record

    def close(self) -> None:
        return None


def _chain(epo: _Epo, patsnap: _Patsnap) -> EpoFirstPatsnapPdfRetrievalProvider:
    return EpoFirstPatsnapPdfRetrievalProvider(
        epo_provider=epo,  # type: ignore[arg-type]
        patsnap_provider=patsnap,  # type: ignore[arg-type]
        close_patsnap=False,
    )


def test_epo_success_does_not_call_p020() -> None:
    epo = _Epo("success")
    patsnap = _Patsnap()

    result = _chain(epo, patsnap).retrieve(_lead())

    assert result.stage is EvidenceStage.RETRIEVED
    assert result.provider == "epo_ops"
    assert result.provenance["retrieval_provider"] == "epo_ops"
    assert result.provenance["retrieval_attempts"] == [
        {
            "provider": "epo_ops",
            "status": "retrieved",
            "reason_code": "epo_document_retrieved",
        }
    ]
    assert patsnap.download_calls == []


def test_epo_no_coverage_uses_same_p002_lead_for_p020() -> None:
    epo = _Epo("no_coverage")
    patsnap = _Patsnap()
    chain = _chain(epo, patsnap)

    result = chain.retrieve(_lead())

    assert result.stage is EvidenceStage.RETRIEVED
    assert result.provider == "patsnap"
    assert result.publication_number == PUBLICATION_NUMBER
    assert result.raw_metadata["patent_id"] == PATENT_ID
    assert patsnap.download_calls[0].publication_number == PUBLICATION_NUMBER
    assert patsnap.download_calls[0].raw_metadata["patent_id"] == PATENT_ID
    assert result.provenance["retrieval_provider"] == "patsnap_p020"
    assert [item["status"] for item in result.provenance["retrieval_attempts"]] == [
        "no_coverage",
        "retrieved",
    ]
    assert result.provenance["p020_identity"]["match"] == "exact"
    assert "ops_fulltext_not_available" not in result.qualification_issues
    assert result.primary_artifact is not None
    assert result.primary_artifact.content.startswith(b"%PDF")
    assert {item.kind for item in chain.last_retrieval_artifacts} == {
        "provider_biblio_response",
        "provider_pdf_metadata_response",
    }


def test_epo_404_allows_p020_but_transport_failure_does_not() -> None:
    not_found_patsnap = _Patsnap()
    not_found = _chain(_Epo("not_found"), not_found_patsnap).retrieve(_lead())
    assert not_found.provenance["retrieval_provider"] == "patsnap_p020"
    assert not_found.provenance["retrieval_attempts"][0]["status"] == "not_found"
    assert len(not_found_patsnap.download_calls) == 1

    network_patsnap = _Patsnap()
    with pytest.raises(ProviderRequestError, match="HTTP 503"):
        _chain(_Epo("network_error"), network_patsnap).retrieve(_lead())
    assert network_patsnap.download_calls == []


def test_p020_failure_keeps_lead_and_both_attempts() -> None:
    patsnap = _Patsnap(fail=True)
    chain = _chain(_Epo("no_coverage"), patsnap)

    result = chain.retrieve(_lead())

    assert result.stage is EvidenceStage.LEAD
    assert "patsnap_p020_unavailable" in result.qualification_issues
    assert [item["status"] for item in result.provenance["retrieval_attempts"]] == [
        "no_coverage",
        "unavailable",
    ]
    assert "retrieval_provider" not in result.provenance
    assert {item.kind for item in chain.last_retrieval_artifacts} == {
        "provider_biblio_response",
        "provider_pdf_metadata_response",
    }


def test_configured_gateway_freezes_epo_and_p020_attempts(
    tmp_path: Path,
) -> None:
    patsnap = _Patsnap()
    chain = _chain(_Epo("no_coverage"), patsnap)

    class _Npl:
        provider_name = "npl"

        @staticmethod
        def search(*_args, **_kwargs):
            return []

        @staticmethod
        def close() -> None:
            return None

    gateway = ConfiguredEvidenceGateway(
        patent_provider=patsnap,
        patent_retrieval_provider=chain,
        npl_provider=_Npl(),
        artifact_store=ArtifactStore(tmp_path / "invalidity/test", "test"),
        max_candidates_per_query=1,
    )
    query = SearchQuery(
        query_id="epo-p020-chain",
        provider_kind="patent",
        purpose="initial",
        technical_subject="water gun",
        feature_ids=["f1"],
        expression="water gun coupling",
        language="en",
        rationale="exact fallback",
    )

    batch = gateway.search(
        query,
        investigation_id="investigation-epo-p020-chain",
        claim_id="1",
        iteration_number=1,
        critical_date=date(2020, 1, 1),
        target_publication_date=None,
        target_publication_date_verified=False,
    )

    assert batch.complete is True
    assert batch.provider == "patsnap+epo_ops_then_patsnap_p020_retrieval"
    assert len(batch.records) == 1
    result = batch.records[0]
    assert result.stage is EvidenceStage.RETRIEVED
    assert result.provenance["retrieval_provider"] == "patsnap_p020"
    assert [item["status"] for item in result.provenance["retrieval_attempts"]] == [
        "no_coverage",
        "retrieved",
    ]
    primary = next(
        item for item in result.artifacts if item.get("is_primary_source")
    )
    assert Path(str(primary["uri"])).read_bytes().startswith(b"%PDF")
    trace_kinds = {
        item.get("kind")
        for item in result.artifacts
        if item.get("kind", "").startswith("provider_")
    }
    assert "provider_biblio_response" in trace_kinds
    assert "provider_pdf_metadata_response" in trace_kinds
