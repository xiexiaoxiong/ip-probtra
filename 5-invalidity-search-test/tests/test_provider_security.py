from __future__ import annotations

from datetime import date
import json
from typing import Any, Mapping
from urllib.parse import quote

import httpx
import pytest

import invalidity.providers as provider_module
from invalidity.providers import (
    ArxivProvider,
    CompositeNplProvider,
    CrossrefProvider,
    EpoOpsProvider,
    EvidenceRecord,
    EvidenceStage,
    GooglePatentsProvider,
    OpenAlexProvider,
    ProviderContentError,
    ProviderRequestError,
    QueryValidationError,
    WebEvidenceProvider,
)


PUBLIC_IP = "93.184.216.34"


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
        self.content = content.encode() if isinstance(content, str) else content
        self.text = self.content.decode("utf-8", errors="replace")
        self.headers = dict(headers or {})
        self._json_data = json_data

    def json(self) -> Any:
        return self._json_data if self._json_data is not None else json.loads(self.text)


class FixtureTransport:
    def __init__(self, routes: Mapping[str, FakeResponse]) -> None:
        self.routes = dict(routes)
        self.calls: list[str] = []

    @staticmethod
    def resolve(_hostname: str) -> list[str]:
        return [PUBLIC_IP]

    def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        return self.routes[url]


def test_fake_ip_dns_uses_trusted_doh_but_real_private_dns_stays_blocked(
    monkeypatch: Any,
) -> None:
    lookups: list[str] = []

    def fake_getaddrinfo(hostname: str, *_args: Any, **_kwargs: Any) -> list[Any]:
        assert hostname == "public.example"
        return [(2, 1, 6, "", ("198.18.1.42", 0))]

    def fake_doh(hostname: str) -> tuple[str, ...]:
        lookups.append(hostname)
        return (PUBLIC_IP,)

    monkeypatch.setattr(provider_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(provider_module, "_resolve_via_trusted_doh", fake_doh)
    assert provider_module._resolve_public_host("public.example") == (PUBLIC_IP,)
    assert lookups == ["public.example"]

    monkeypatch.setattr(
        provider_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("10.0.0.8", 0))],
    )
    lookups.clear()
    assert provider_module._resolve_public_host("public.example") == ("10.0.0.8",)
    assert lookups == []


def test_fake_ip_tun_bypass_is_limited_to_exact_trusted_provider_host() -> None:
    query_url = "https://patents.google.com/xhr/query"
    transport = FixtureTransport(
        {
            query_url: FakeResponse(
                url=query_url,
                json_data={"results": {"cluster": []}},
                content='{"results":{"cluster":[]}}',
                headers={"content-type": "application/json"},
            )
        }
    )
    provider = GooglePatentsProvider(
        transport=transport,
        resolver=lambda _hostname: ["198.18.1.42"],
    )
    assert (
        provider.search(
            {
                "text": "robot cleaner lifting pad",
                "subject_terms": ["robot cleaner"],
                "feature_terms": ["lifting pad"],
            },
            max_results=1,
        )
        == []
    )

    arxiv_pdf_url = "https://arxiv.org/pdf/2401.00001"
    arxiv = ArxivProvider(
        transport=FixtureTransport(
            {
                arxiv_pdf_url: FakeResponse(
                    url=arxiv_pdf_url,
                    content=b"%PDF-1.7\ntrusted arxiv\n%%EOF",
                    headers={"content-type": "application/pdf"},
                )
            }
        ),
        resolver=lambda _hostname: ["198.18.1.44"],
    )
    assert arxiv.download_pdf(arxiv_pdf_url).kind == "pdf"

    untrusted_url = "https://manuals.example.com/robot-cleaner"
    untrusted = WebEvidenceProvider(
        transport=FixtureTransport(
            {
                untrusted_url: FakeResponse(
                    url=untrusted_url,
                    content="<main>must not be requested</main>",
                    headers={"content-type": "text/html"},
                )
            }
        ),
        resolver=lambda _hostname: ["198.18.1.43"],
    )
    with pytest.raises(ProviderRequestError, match="公网"):
        untrusted.retrieve(
            EvidenceRecord(
                provider="web_search",
                source_type="manual",
                external_id="untrusted-fake-ip",
                title="untrusted fake ip",
                source_url=untrusted_url,
            )
        )


def test_epo_fake_ip_tun_bypass_is_limited_to_exact_official_host() -> None:
    trusted = EpoOpsProvider(
        consumer_key="fixture-key",
        consumer_secret="fixture-secret",
        transport=FixtureTransport({}),
        resolver=lambda hostname: (
            ["198.18.1.45"] if hostname == "ops.epo.org" else ["198.18.1.46"]
        ),
    )
    assert trusted._validate_public_url(
        "https://ops.epo.org/3.2/auth/accesstoken"
    ) == ("198.18.1.45",)

    with pytest.raises(ProviderRequestError, match="公网"):
        trusted._validate_public_url(
            "https://ops.epo.org.attacker.example/3.2/auth/accesstoken"
        )


def test_public_search_curl_is_fixed_https_and_keeps_query_off_argv_and_disk(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}
    query = "site:patents.google.com/patent/ robot cleaner lifting pad"

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return type(
            "CurlResult",
            (),
            {
                "returncode": 0,
                "stdout": (
                    b"<html><body>result</body></html>"
                    b"\n__INVALIDITY_CURL_STATUS__=200|text/html; charset=utf-8"
                ),
            },
        )()

    monkeypatch.setattr(provider_module.subprocess, "run", fake_run)
    html = provider_module._curl_get_search_html(
        provider_slug="yahoo_web_fallback",
        endpoint=provider_module.YAHOO_SEARCH_URL,
        query_parameter="p",
        query=query,
        timeout_seconds=7.5,
        max_response_bytes=4096,
    )

    args = captured["args"]
    kwargs = captured["kwargs"]
    assert html == "<html><body>result</body></html>"
    assert args[:2] == ["/usr/bin/curl", "--disable"]
    assert ["--proxy", ""] == args[args.index("--proxy") : args.index("--proxy") + 2]
    assert ["--proto", "=https"] == args[
        args.index("--proto") : args.index("--proto") + 2
    ]
    assert ["--max-redirs", "0"] == args[
        args.index("--max-redirs") : args.index("--max-redirs") + 2
    ]
    assert "p@-" in args
    assert query not in " ".join(args)
    assert kwargs["input"] == query.encode("utf-8")
    assert kwargs["env"] == {
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "NO_PROXY": "*",
        "PATH": "/usr/bin:/bin",
    }

    with pytest.raises(ProviderRequestError, match="固定允许列表"):
        provider_module._curl_get_search_html(
            provider_slug="yahoo_web_fallback",
            endpoint="https://attacker.example/search",
            query_parameter="p",
            query=query,
            timeout_seconds=7.5,
            max_response_bytes=4096,
        )


@pytest.mark.parametrize(
    ("stdout", "returncode", "error_type", "match"),
    [
        (
            b"<html></html>\n__INVALIDITY_CURL_STATUS__=302|text/html",
            0,
            ProviderRequestError,
            "HTTP 302",
        ),
        (
            b"partial\n__INVALIDITY_CURL_STATUS__=200|text/html",
            63,
            ProviderContentError,
            "大小上限",
        ),
        (
            b"{}\n__INVALIDITY_CURL_STATUS__=200|application/json",
            0,
            ProviderContentError,
            "MIME",
        ),
        (b"missing marker", 0, ProviderRequestError, "curl 失败"),
    ],
)
def test_public_search_curl_fails_closed_on_bad_transport_result(
    monkeypatch: Any,
    stdout: bytes,
    returncode: int,
    error_type: type[Exception],
    match: str,
) -> None:
    monkeypatch.setattr(
        provider_module.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "CurlResult",
            (),
            {"returncode": returncode, "stdout": stdout},
        )(),
    )
    with pytest.raises(error_type, match=match):
        provider_module._curl_get_search_html(
            provider_slug="yahoo_web_fallback",
            endpoint=provider_module.YAHOO_SEARCH_URL,
            query_parameter="p",
            query="robot cleaner lifting pad",
            timeout_seconds=5,
            max_response_bytes=4096,
        )


def test_openalex_and_crossref_are_lead_only_even_when_pdf_url_is_present() -> None:
    openalex_url = "https://api.openalex.org/works"
    crossref_url = "https://api.crossref.org/works"
    transport = FixtureTransport(
        {
            openalex_url: FakeResponse(
                url=openalex_url,
                json_data={
                    "results": [
                        {
                            "id": "https://openalex.org/W123",
                            "doi": "https://doi.org/10.1000/example",
                            "title": "Robotic cleaner lifting pad mechanism",
                            "publication_date": "2018-05-04",
                            "primary_location": {
                                "landing_page_url": "https://publisher.example/article",
                                "pdf_url": "https://publisher.example/article.pdf",
                            },
                        }
                    ]
                },
                headers={"content-type": "application/json"},
            ),
            crossref_url: FakeResponse(
                url=crossref_url,
                json_data={
                    "message": {
                        "items": [
                            {
                                "DOI": "10.1000/example-two",
                                "title": ["Cleaner actuator control"],
                                "URL": "https://doi.org/10.1000/example-two",
                                "published-print": {"date-parts": [[2017, 2, 3]]},
                                "link": [
                                    {
                                        "URL": "https://publisher.example/two.pdf",
                                        "content-type": "application/pdf",
                                    }
                                ],
                            }
                        ]
                    }
                },
                headers={"content-type": "application/json"},
            ),
        }
    )

    openalex = OpenAlexProvider(transport=transport).search(
        "robotic cleaner lifting pad"
    )
    crossref = CrossrefProvider(transport=transport).search(
        "cleaner actuator control"
    )

    assert openalex[0].stage is EvidenceStage.LEAD
    assert openalex[0].pdf_url == "https://publisher.example/article.pdf"
    assert not openalex[0].content_sha256
    assert crossref[0].stage is EvidenceStage.LEAD
    assert crossref[0].publication_date == "2017-02-03"
    assert not crossref[0].content_sha256


def test_openalex_prefers_best_oa_location_pdf_over_metadata_only_primary() -> None:
    openalex_url = "https://api.openalex.org/works"
    transport = FixtureTransport(
        {
            openalex_url: FakeResponse(
                url=openalex_url,
                json_data={
                    "results": [
                        {
                            "id": "https://openalex.org/W456",
                            "doi": "https://doi.org/10.1000/open-copy",
                            "title": "Position-selective coupling for liquid devices",
                            "publication_date": "2016-04-03",
                            "open_access": {
                                "is_oa": True,
                                "oa_status": "green",
                            },
                            "primary_location": {
                                "landing_page_url": "https://publisher.example/article",
                                "pdf_url": None,
                            },
                            "best_oa_location": {
                                "landing_page_url": "https://repository.example/item/456",
                                "pdf_url": "https://repository.example/item/456.pdf",
                            },
                        }
                    ]
                },
                headers={"content-type": "application/json"},
            )
        }
    )

    lead = OpenAlexProvider(transport=transport).search(
        "position selective coupling liquid device"
    )[0]

    assert lead.stage is EvidenceStage.LEAD
    assert lead.source_url == "https://repository.example/item/456"
    assert lead.pdf_url == "https://repository.example/item/456.pdf"
    assert lead.raw_metadata["selected_fulltext_location_kind"] == (
        "best_oa_location"
    )
    assert lead.provenance["open_access_declared"] is True
    assert lead.provenance["fulltext_route"] == "openalex_declared_pdf"
    assert not lead.content_sha256


def test_web_search_snippet_is_never_evidence_and_real_html_is_retrieved() -> None:
    search_url = "https://html.duckduckgo.com/html/"
    page_url = "https://manuals.example.com/robot-cleaner-manual"
    redirect = quote(page_url, safe="")
    transport = FixtureTransport(
        {
            search_url: FakeResponse(
                url=search_url,
                content=f"""
                <html><body><div class='result'>
                  <a class='result__a' href='//duckduckgo.com/l/?uddg={redirect}'>
                    Robot cleaner service manual
                  </a>
                  <a class='result__snippet'>Search-engine summary only</a>
                </div></body></html>
                """,
                headers={"content-type": "text/html"},
            ),
            page_url: FakeResponse(
                url=page_url,
                content="""
                <html><head>
                  <meta property='article:published_time' content='2018-04-03'>
                  <title>Robot cleaner service manual</title>
                </head><body><main>
                  This service manual describes the robotic cleaner chassis,
                  lifting actuator, cleaning pad support, controller and carpet
                  sensor in sufficient technical detail for later comparison.
                </main></body></html>
                """,
                headers={"content-type": "text/html; charset=utf-8"},
            ),
        }
    )
    provider = WebEvidenceProvider(transport=transport)

    leads = provider.search("robot cleaner lifting actuator manual")
    assert leads[0].stage is EvidenceStage.LEAD
    assert leads[0].snippet == "Search-engine summary only"
    assert not leads[0].content_sha256

    retrieved = provider.retrieve(leads[0])
    assert retrieved.stage is EvidenceStage.RETRIEVED
    assert retrieved.content_sha256
    assert retrieved.publication_date == "2018-04-03"
    assert "lifting actuator" in (retrieved.full_text or "")


def test_web_search_access_shell_is_not_silent_empty_result() -> None:
    search_url = "https://html.duckduckgo.com/html/"
    provider = WebEvidenceProvider(
        transport=FixtureTransport(
            {
                search_url: FakeResponse(
                    url=search_url,
                    content="<html><title>Verify you are human</title><body>captcha</body></html>",
                    headers={"content-type": "text/html"},
                )
            }
        )
    )

    with pytest.raises(ProviderContentError, match="访问受限"):
        provider.search("robot cleaner lifting actuator")


def test_web_search_duckduckgo_anomaly_challenge_is_access_restricted() -> None:
    search_url = "https://html.duckduckgo.com/html/"
    provider = WebEvidenceProvider(
        transport=FixtureTransport(
            {
                search_url: FakeResponse(
                    url=search_url,
                    content="""
                    <html><title>DuckDuckGo</title><body>
                      <form id="challenge-form" action="/anomaly.js?sv=html">
                        <div class="anomaly-modal__modal">
                          Unfortunately, bots use DuckDuckGo too.
                          Select all squares containing a duck.
                        </div>
                      </form>
                    </body></html>
                    """,
                    headers={"content-type": "text/html"},
                )
            }
        )
    )

    with pytest.raises(ProviderContentError, match="访问受限"):
        provider.search("robot cleaner lifting actuator")


def test_web_search_unknown_dom_is_partial_failure_but_explicit_zero_is_allowed() -> None:
    search_url = "https://html.duckduckgo.com/html/"
    unknown = WebEvidenceProvider(
        transport=FixtureTransport(
            {
                search_url: FakeResponse(
                    url=search_url,
                    content="<html><body><div>new unrecognised layout</div></body></html>",
                    headers={"content-type": "text/html"},
                )
            }
        )
    )
    with pytest.raises(ProviderContentError, match="结构不可识别"):
        unknown.search("robot cleaner lifting actuator")

    explicit_zero = WebEvidenceProvider(
        transport=FixtureTransport(
            {
                search_url: FakeResponse(
                    url=search_url,
                    content="<html><body><div class='no-results'>No results found</div></body></html>",
                    headers={"content-type": "text/html"},
                )
            }
        )
    )
    assert explicit_zero.search("robot cleaner lifting actuator") == []


def test_unknown_web_date_stays_retrieved_and_requires_review() -> None:
    page_url = "https://forum.example.com/thread/cleaner-lift"
    transport = FixtureTransport(
        {
            page_url: FakeResponse(
                url=page_url,
                content=(
                    "<html><body><article>Forum discussion of a robot cleaner "
                    "lifting pad actuator and controller, without a stable post date."
                    "</article></body></html>"
                ),
                headers={"content-type": "text/html"},
            )
        }
    )
    provider = WebEvidenceProvider(transport=transport)
    lead = EvidenceRecord(
        provider="web_search",
        source_type="forum",
        external_id="forum-thread",
        title="Cleaner lift thread",
        source_url=page_url,
    )

    retrieved = provider.retrieve(lead)
    qualified = provider.qualify(
        retrieved,
        critical_date=date(2020, 1, 1),
        content_relevance_verified=True,
        source_chain_verified=False,
        public_date_verified=False,
    )

    assert retrieved.stage is EvidenceStage.RETRIEVED
    assert retrieved.publication_date is None
    assert qualified.stage is EvidenceStage.RETRIEVED
    assert "public_date_evidence_missing" in qualified.qualification_issues
    assert "date_eligibility_unknown" in qualified.qualification_issues


def test_plain_text_access_shell_is_rejected() -> None:
    page_url = "https://manuals.example.com/robot-cleaner.txt"
    provider = WebEvidenceProvider(
        transport=FixtureTransport(
            {
                page_url: FakeResponse(
                    url=page_url,
                    content=("Verify you are human captcha " * 10),
                    headers={"content-type": "text/plain"},
                )
            }
        )
    )
    with pytest.raises(ProviderContentError, match="访问受限"):
        provider.retrieve(
            EvidenceRecord(
                provider="web_search",
                source_type="manual",
                external_id="blocked-text",
                title="blocked text",
                source_url=page_url,
            )
        )


def test_safe_fetch_rejects_private_redirect_403_oversize_and_fake_pdf() -> None:
    dns_url = "https://internal-alias.example.com/manual"
    dns_transport = FixtureTransport(
        {
            dns_url: FakeResponse(
                url=dns_url,
                content="<html><body>must never be requested</body></html>",
                headers={"content-type": "text/html"},
            )
        }
    )
    with pytest.raises(ProviderRequestError, match="公网"):
        WebEvidenceProvider(
            transport=dns_transport,
            resolver=lambda _hostname: ["10.0.0.8"],
        ).retrieve(
            EvidenceRecord(
                provider="web_search",
                source_type="manual",
                external_id="private-dns",
                title="private dns",
                source_url=dns_url,
            )
        )
    assert dns_transport.calls == []

    public_url = "https://manuals.example.com/start"
    private_url = "http://169.254.169.254/latest/meta-data"
    redirect_transport = FixtureTransport(
        {
            public_url: FakeResponse(
                url=public_url,
                status_code=302,
                headers={"location": private_url, "content-type": "text/html"},
            )
        }
    )
    provider = WebEvidenceProvider(transport=redirect_transport)
    lead = EvidenceRecord(
        provider="web_search",
        source_type="manual",
        external_id="redirect",
        title="redirect",
        source_url=public_url,
    )
    with pytest.raises(ProviderRequestError, match="公网"):
        provider.retrieve(lead)
    assert redirect_transport.calls == [public_url]

    forbidden_url = "https://manuals.example.com/forbidden"
    with pytest.raises(ProviderRequestError, match="HTTP 403"):
        WebEvidenceProvider(
            transport=FixtureTransport(
                {
                    forbidden_url: FakeResponse(
                        url=forbidden_url,
                        status_code=403,
                        headers={"content-type": "text/html"},
                    )
                }
            )
        ).retrieve(
            EvidenceRecord(
                provider="web_search",
                source_type="manual",
                external_id="403",
                title="403",
                source_url=forbidden_url,
            )
        )

    pdf_url = "https://arxiv.org/pdf/fixture"
    oversized = FixtureTransport(
        {
            pdf_url: FakeResponse(
                url=pdf_url,
                content=b"%PDF" + b"x" * 40,
                headers={"content-type": "application/pdf", "content-length": "44"},
            )
        }
    )
    with pytest.raises(ProviderContentError, match="大小上限"):
        ArxivProvider(transport=oversized, max_response_bytes=32).download_pdf(pdf_url)

    fake_pdf = FixtureTransport(
        {
            pdf_url: FakeResponse(
                url=pdf_url,
                content="<html>login wall</html>",
                headers={"content-type": "application/pdf"},
            )
        }
    )
    with pytest.raises(ProviderContentError, match="真实 PDF"):
        ArxivProvider(transport=fake_pdf).download_pdf(pdf_url)


def test_image_download_rejects_arbitrary_riff_and_pixel_bomb_header() -> None:
    riff_url = "https://patents.example.com/arbitrary.webp"
    riff_provider = GooglePatentsProvider(
        transport=FixtureTransport(
            {
                riff_url: FakeResponse(
                    url=riff_url,
                    content=b"RIFF" + b"x" * 40,
                    headers={"content-type": "image/webp"},
                )
            }
        )
    )
    with pytest.raises(ProviderContentError, match="PNG/JPEG/GIF/WebP"):
        riff_provider.download_image(riff_url)

    png_url = "https://patents.example.com/huge.png"
    huge_png = (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (100_000).to_bytes(4, "big")
        + (100_000).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    pixel_provider = GooglePatentsProvider(
        transport=FixtureTransport(
            {
                png_url: FakeResponse(
                    url=png_url,
                    content=huge_png,
                    headers={"content-type": "image/png"},
                )
            }
        )
    )
    with pytest.raises(ProviderContentError, match="尺寸|像素"):
        pixel_provider.download_image(png_url)


def test_injected_httpx_client_cannot_pre_follow_redirects() -> None:
    client = httpx.Client(follow_redirects=True)
    try:
        with pytest.raises(ValueError, match="follow_redirects=False"):
            WebEvidenceProvider(transport=client)
    finally:
        client.close()


def test_injected_httpx_client_is_rejected_when_proxy_is_not_auditable() -> None:
    client = httpx.Client(follow_redirects=False, trust_env=False)
    try:
        with pytest.raises(ValueError, match="禁止注入 httpx.Client"):
            WebEvidenceProvider(transport=client)
    finally:
        client.close()


def test_owned_http_client_disables_env_proxy_and_pins_validated_ip() -> None:
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=(
                "<html><body><main>A robot cleaner service manual describes "
                "a lifting actuator, pad support, controller, sensor, and "
                "mechanical linkage in verifiable technical detail.</main></body></html>"
            ),
        )

    provider = WebEvidenceProvider(resolver=lambda _hostname: [PUBLIC_IP])
    assert getattr(provider.transport, "_trust_env") is False
    provider.transport.close()
    provider.transport = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        record = provider.retrieve(
            EvidenceRecord(
                provider="web_search",
                source_type="manual",
                external_id="pinned",
                title="pinned",
                source_url="https://manuals.example.com/cleaner",
            )
        )
    finally:
        provider.close()

    assert record.stage is EvidenceStage.RETRIEVED
    assert requested[0].url.host == PUBLIC_IP
    assert requested[0].headers["host"] == "manuals.example.com"
    assert requested[0].extensions["sni_hostname"] == "manuals.example.com"


def test_dns_change_after_pinned_request_fails_closed() -> None:
    calls = 0

    def resolver(_hostname: str) -> list[str]:
        nonlocal calls
        calls += 1
        return [PUBLIC_IP] if calls == 1 else ["127.0.0.1"]

    provider = WebEvidenceProvider(resolver=resolver)
    provider.transport.close()
    provider.transport = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=(
                    "<html><body><main>A sufficiently long robot cleaner manual "
                    "with lifting actuator and controller details.</main></body></html>"
                ),
            )
        ),
        follow_redirects=False,
        trust_env=False,
    )
    try:
        with pytest.raises(ProviderRequestError, match="公网"):
            provider.retrieve(
                EvidenceRecord(
                    provider="web_search",
                    source_type="manual",
                    external_id="rebind",
                    title="rebind",
                    source_url="https://manuals.example.com/rebind",
                )
            )
    finally:
        provider.close()


def test_composite_npl_continues_after_one_source_failure_and_reports_partial() -> None:
    class FailingSource:
        provider_name = "blocked_source"

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            raise ProviderRequestError("blocked_source HTTP 403")

        def close(self) -> None:
            return None

    class LeadSource:
        provider_name = "lead_source"

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            return [
                EvidenceRecord(
                    provider="lead_source",
                    source_type="paper",
                    external_id="paper-1",
                    title="Robot cleaner actuator",
                    source_url="https://publisher.example/paper-1",
                )
            ]

        def close(self) -> None:
            return None

    composite = CompositeNplProvider([FailingSource(), LeadSource()])

    result = composite.search("robot cleaner lifting actuator")

    assert result.complete is False
    assert [item.external_id for item in result.records] == ["paper-1"]
    assert result.providers_attempted == ("blocked_source", "lead_source")
    assert result.providers_succeeded == ("lead_source",)
    assert "HTTP 403" in result.errors[0]


def test_composite_npl_rejects_one_invalid_query_before_provider_fanout() -> None:
    class RecordingSource:
        provider_name = "recording_source"

        def __init__(self) -> None:
            self.calls = 0

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            self.calls += 1
            return []

        def close(self) -> None:
            return None

    first = RecordingSource()
    second = RecordingSource()
    composite = CompositeNplProvider([first, second])

    with pytest.raises(QueryValidationError, match="孤立宽词"):
        composite.search("水枪")

    assert first.calls == 0
    assert second.calls == 0


def test_composite_npl_deduplicates_cross_source_doi_leads() -> None:
    class DoiSource:
        def __init__(self, name: str, source_url: str) -> None:
            self.provider_name = name
            self.source_url = source_url

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            return [
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="paper",
                    external_id=f"{self.provider_name}-id",
                    title="Same paper",
                    source_url=self.source_url,
                    raw_metadata={"doi": "https://doi.org/10.1000/SAME"},
                )
            ]

        def close(self) -> None:
            return None

    result = CompositeNplProvider(
        [
            DoiSource("openalex", "https://publisher.example/a"),
            DoiSource("crossref", "https://doi.org/10.1000/same"),
        ]
    ).search("robot cleaner lifting actuator")

    assert result.complete is True
    assert len(result.records) == 1
    assert result.providers_succeeded == ("openalex", "crossref")


def test_composite_npl_uses_hardened_web_adapter_only_for_declared_http_pdf() -> None:
    class MetadataSource:
        provider_name = "openalex"

        def search(self, *_args: Any, **_kwargs: Any) -> list[EvidenceRecord]:
            return []

        def close(self) -> None:
            return None

    web = WebEvidenceProvider(transport=FixtureTransport({}))
    composite = CompositeNplProvider([MetadataSource(), web])
    lead = EvidenceRecord(
        provider="openalex",
        source_type="paper",
        external_id="W123",
        title="Cleaner actuator paper",
        source_url="https://openalex.org/W123",
        pdf_url="https://publisher.example/W123.pdf",
        provenance={"metadata_only": True},
    )

    adapter, retrieval_lead = composite.retrieval_plan_for(lead)

    assert adapter is web
    assert retrieval_lead.provider == "openalex"
    assert retrieval_lead.source_url == "https://publisher.example/W123.pdf"
    assert retrieval_lead.provenance["bibliographic_source_url"] == lead.source_url
    assert retrieval_lead.stage is EvidenceStage.LEAD
    assert not retrieval_lead.content_sha256

    unsafe = EvidenceRecord(
        provider="openalex",
        source_type="paper",
        external_id="W124",
        title="Unsafe URL",
        source_url="https://openalex.org/W124",
        pdf_url="file:///private/secret.pdf",
    )
    unsafe_adapter, unsafe_lead = composite.retrieval_plan_for(unsafe)
    assert unsafe_adapter is composite.provider_for(unsafe)
    assert unsafe_lead is unsafe


def test_composite_npl_enforces_one_global_result_budget() -> None:
    class ManySource:
        def __init__(self, name: str) -> None:
            self.provider_name = name
            self.requested_limits: list[int] = []

        def search(self, *_args: Any, **kwargs: Any) -> list[EvidenceRecord]:
            limit = int(kwargs["max_results"])
            self.requested_limits.append(limit)
            return [
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="paper",
                    external_id=f"{self.provider_name}-{index}",
                    title=f"paper {index}",
                    source_url=f"https://{self.provider_name}.example/{index}",
                )
                for index in range(limit)
            ]

        def close(self) -> None:
            return None

    first = ManySource("first")
    second = ManySource("second")
    third = ManySource("third")
    result = CompositeNplProvider([first, second, third]).search(
        "robot cleaner lifting actuator",
        max_results=4,
    )

    assert len(result.records) == 4
    assert [item.provider for item in result.records[:3]] == [
        "first",
        "second",
        "third",
    ]
    assert first.requested_limits == [2]
    assert second.requested_limits == [2]
    assert third.requested_limits == [2]
