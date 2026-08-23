from pathlib import Path
import json
import sys


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from build_sample_manifest import build_manifest  # noqa: E402


def test_real_sample_directory_is_fully_classified() -> None:
    root = Path("/Users/xiexiaoxiong/Documents/Patent项目配套/实例专利")
    oracle = Path(__file__).resolve().parents[1] / "fixtures" / "sample-oracle.json"
    manifest = build_manifest(root, oracle)
    discovered_pdfs = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    expected_names = set(json.loads(oracle.read_text(encoding="utf-8"))["patents"])
    manifest_patents = [
        item for item in manifest["files"] if item["category"] == "patent_pdf"
    ]
    assert manifest["patent_pdf_count"] == len(discovered_pdfs)
    assert sorted(item["relative_path"] for item in manifest_patents) == discovered_pdfs
    assert all(item["byte_size"] > 0 for item in manifest_patents)
    assert all(len(item["sha256"]) == 64 for item in manifest_patents)
    assert manifest["missing_oracle_files"] == []
    assert {Path(relative).name for relative in discovered_pdfs} == expected_names
    assert manifest["unexpected_patent_files"] == []
