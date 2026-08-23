import json
from pathlib import Path


def test_spike_has_replayable_patent_and_npl_files_and_negative_fetch() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "provider-spike-manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    requests = data["requests"]
    patent_files = [
        item
        for item in requests
        if item["provider"] == "google_patents"
        and item["operation"] == "candidate_pdf"
        and item["http_status"] == 200
    ]
    npl_files = [
        item
        for item in requests
        if item["provider"] == "arxiv"
        and item["operation"] == "paper_pdf"
        and item["http_status"] == 200
    ]
    negative = [item for item in requests if item.get("http_status") == 403]
    assert patent_files and len(patent_files[0]["sha256"]) == 64
    assert npl_files and len(npl_files[0]["sha256"]) == 64
    assert negative and negative[0]["decision"] == "must_not_promote_to_retrieved_document"
