from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import load_dotenv


ALLOWED_ENVIRONMENTS = {"test", "prod"}
EXPECTED_SCHEMAS = {"test": "invalidity_test", "prod": "invalidity_prod"}
EXPECTED_PORTS = {"test": 5209, "prod": 5109}
EXPECTED_MODULE1_PORTS = {"test": 5201, "prod": 5101}
PATSNAP_BASE_URL = "https://connect.zhihuiya.com"
PATSNAP_COUNT_PATH = "/search/patent/query-search-count/v2"
PATSNAP_SEARCH_PATH = "/search/patent/query-search-patent/v2"
PATSNAP_COUNT_PATHS = {
    "/search/patent/query-search-count",
    "/search/patent/query-search-count/v2",
}
PATSNAP_SEARCH_PATHS = {
    "/search/patent/query-search-patent",
    "/search/patent/query-search-patent/v2",
}


class ConfigurationError(RuntimeError):
    """Raised when an isolation or runtime requirement is not satisfied."""


def load_service_environment(path: str | Path) -> None:
    """Load one explicitly selected service environment file.

    The invalidity service must never discover Portal or sibling-module dotenv
    files.  Its PM2 wrapper normally exports the environment itself; this
    helper only exists for an explicit local-development file selected by the
    caller.
    """

    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file():
        raise ConfigurationError(f"显式环境文件不存在: {candidate}")
    load_dotenv(candidate, override=False)


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name, "") or "").strip()
    if not value:
        raise ConfigurationError(f"缺少必需环境变量 {name}")
    return value


def _validated_token(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if len(value) < 16 or any(
        marker in value.upper() for marker in ("REPLACE_", "CHANGE_ME")
    ):
        raise ConfigurationError(f"{name} 必须是至少 16 字符的非占位环境专用 token")
    return value


def _positive_int(value: str | None, default: int, name: str) -> int:
    raw = str(value or default).strip()
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return parsed


def _bounded_positive_int(
    value: str | None,
    default: int,
    name: str,
    *,
    maximum: int,
) -> int:
    parsed = _positive_int(value, default, name)
    if parsed > maximum:
        raise ConfigurationError(f"{name} 必须在 1..{maximum} 范围内")
    return parsed


def _optional_patsnap_api_key(env: Mapping[str, str], name: str) -> str | None:
    value = str(env.get(name, "") or "").strip()
    if not value:
        return None
    if (
        len(value) < 16
        or len(value) > 4096
        or not value.startswith("sk-")
        or any(character.isspace() for character in value)
        or any(marker in value.upper() for marker in ("REPLACE_", "CHANGE_ME"))
    ):
        raise ConfigurationError(
            f"{name} 必须是当前环境有效的 sk- 前缀智慧芽 API Key"
        )
    return value


def _exact_patsnap_base_url(value: str, *, name: str) -> str:
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} 不是有效 URL") from exc
    if (
        value != PATSNAP_BASE_URL
        or parsed.scheme != "https"
        or parsed.hostname != "connect.zhihuiya.com"
        or parsed_port not in {None, 443}
        or parsed.path
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(f"{name} 必须严格等于 {PATSNAP_BASE_URL}")
    return value


def _patsnap_path(
    value: str,
    *,
    name: str,
    allowed: set[str],
) -> str:
    if value not in allowed:
        raise ConfigurationError(f"{name} 不在官方候选路径白名单")
    return value


def _exact_loopback_url(
    value: str,
    *,
    name: str,
    port: int,
    path: str,
) -> str:
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{name} 不是有效 URL") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed_port != port
        or parsed.path != path
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        expected = f"http://127.0.0.1:{port}{path}"
        raise ConfigurationError(f"{name} 必须严格等于 {expected}")
    return value


def _isolated_artifact_root(value: str, environment: str) -> Path:
    root = Path(value).expanduser().resolve()
    parts = root.parts
    if not any(
        parts[index : index + 2] == ("invalidity", environment)
        for index in range(max(0, len(parts) - 1))
    ):
        raise ConfigurationError(
            f"{environment} 工件目录必须包含 invalidity/{environment} 隔离前缀"
        )
    return root


def _allowed_source_roots(value: str, environment: str) -> tuple[Path, ...]:
    raw_values: list[str] = []
    for item in value.split(os.pathsep):
        raw_values.extend(item.split(","))
    roots: list[Path] = []
    for raw in raw_values:
        if not raw.strip():
            continue
        root = Path(raw.strip()).expanduser().resolve()
        if root == Path(root.anchor):
            raise ConfigurationError("INVALIDITY_ALLOWED_SOURCE_ROOTS 禁止包含文件系统根目录")
        if len(root.parts) < 3 or root.parts[-3:] != (".data", "uploads", environment):
            raise ConfigurationError(
                "INVALIDITY_ALLOWED_SOURCE_ROOTS 必须严格指向环境专用的 "
                f".data/uploads/{environment} 目录"
            )
        if root not in roots:
            roots.append(root)
    if not roots:
        raise ConfigurationError("缺少必需环境变量 INVALIDITY_ALLOWED_SOURCE_ROOTS")
    return tuple(roots)


@dataclass(frozen=True, slots=True)
class ParserSettings:
    """Least-privilege configuration for the isolated test parser on 5201."""

    environment: str
    parser_port: int
    parser_api_token: str
    artifact_root: Path
    allowed_source_roots: tuple[Path, ...]
    source_fetch_timeout_seconds: int = 30
    source_max_bytes: int = 50 * 1024 * 1024
    image_max_bytes: int = 15 * 1024 * 1024
    source_max_redirects: int = 5

    @classmethod
    def from_environment(
        cls,
        source: Mapping[str, str] | None = None,
        *,
        load_dotenv_files: bool = False,
        dotenv_path: str | Path | None = None,
    ) -> "ParserSettings":
        if load_dotenv_files:
            if dotenv_path is None:
                raise ConfigurationError(
                    "禁止自动扫描共享 .env；必须通过 dotenv_path 显式选择解析服务环境文件"
                )
            load_service_environment(dotenv_path)
        env = source if source is not None else os.environ
        environment = str(env.get("INVALIDITY_ENV", "") or "").strip().lower()
        if environment != "test":
            raise ConfigurationError("隔离解析服务只能使用 INVALIDITY_ENV=test")
        forbidden = sorted(
            key
            for key, value in env.items()
            if str(key).startswith("INVALIDITY_PROD_")
            and str(value or "").strip()
        )
        if forbidden:
            raise ConfigurationError(
                "测试解析服务发现正式环境变量: " + ", ".join(forbidden)
            )
        parser_port = _positive_int(
            env.get("INVALIDITY_PARSER_PORT"), 0, "INVALIDITY_PARSER_PORT"
        )
        if parser_port != 5201:
            raise ConfigurationError("隔离解析服务端口必须是 5201")
        return cls(
            environment=environment,
            parser_port=parser_port,
            parser_api_token=_validated_token(
                env, "INVALIDITY_TEST_PARSER_TOKEN"
            ),
            artifact_root=_isolated_artifact_root(
                _required(env, "INVALIDITY_ARTIFACT_ROOT"), environment
            ),
            allowed_source_roots=_allowed_source_roots(
                _required(env, "INVALIDITY_ALLOWED_SOURCE_ROOTS"), environment
            ),
            source_fetch_timeout_seconds=_positive_int(
                env.get("INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS"),
                30,
                "INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS",
            ),
            source_max_bytes=_positive_int(
                env.get("INVALIDITY_SOURCE_MAX_BYTES"),
                50 * 1024 * 1024,
                "INVALIDITY_SOURCE_MAX_BYTES",
            ),
            image_max_bytes=_positive_int(
                env.get("INVALIDITY_IMAGE_MAX_BYTES"),
                15 * 1024 * 1024,
                "INVALIDITY_IMAGE_MAX_BYTES",
            ),
            source_max_redirects=_positive_int(
                env.get("INVALIDITY_SOURCE_MAX_REDIRECTS"),
                5,
                "INVALIDITY_SOURCE_MAX_REDIRECTS",
            ),
        )


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    service_port: int
    api_base_url: str
    api_token: str
    database_url: str
    database_schema: str
    artifact_root: Path
    allowed_source_roots: tuple[Path, ...]
    module1_api_url: str
    patent_provider: str
    npl_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    patsnap_api_key: str | None = None
    patsnap_base_url: str = PATSNAP_BASE_URL
    patsnap_count_path: str = PATSNAP_COUNT_PATH
    patsnap_search_path: str = PATSNAP_SEARCH_PATH
    epo_ops_consumer_key: str | None = None
    epo_ops_consumer_secret: str | None = None
    module1_api_token: str | None = None
    module1_auth_mode: str = "legacy_unauthenticated"
    parser_api_token: str | None = None
    llm_timeout_seconds: int = 360
    llm_direct_attempt_timeout_seconds: int = 180
    llm_direct_probe_timeout_seconds: int = 5
    llm_i2_timeout_seconds: int = 360
    llm_i2_direct_attempt_timeout_seconds: int = 180
    max_rounds: int = 5
    max_candidates_per_query: int = 5
    worker_concurrency: int = 2
    worker_poll_seconds: int = 2
    job_lease_seconds: int = 900
    parser_port: int | None = None
    source_fetch_timeout_seconds: int = 30
    source_max_bytes: int = 50 * 1024 * 1024
    image_max_bytes: int = 15 * 1024 * 1024
    source_max_redirects: int = 5

    @classmethod
    def from_environment(
        cls,
        source: Mapping[str, str] | None = None,
        *,
        load_dotenv_files: bool = False,
        dotenv_path: str | Path | None = None,
    ) -> "Settings":
        if load_dotenv_files:
            if dotenv_path is None:
                raise ConfigurationError(
                    "禁止自动扫描共享 .env；必须通过 dotenv_path 显式选择服务环境文件"
                )
            load_service_environment(dotenv_path)
        # An explicitly supplied mapping is authoritative even when it is empty.
        # Falling back to the process environment for ``{}`` would let a test or
        # isolated caller accidentally inherit production credentials.
        env = source if source is not None else os.environ
        environment = str(env.get("INVALIDITY_ENV", "") or "").strip().lower()
        if environment not in ALLOWED_ENVIRONMENTS:
            raise ConfigurationError(
                "INVALIDITY_ENV 必须显式设置为 test 或 prod，禁止自动猜测运行环境"
            )

        schema = _required(env, "INVALIDITY_DATABASE_SCHEMA")
        expected_schema = EXPECTED_SCHEMAS[environment]
        if schema != expected_schema:
            raise ConfigurationError(
                f"{environment} 环境只能使用 {expected_schema}，当前为 {schema}"
            )

        prefix = "INVALIDITY_TEST" if environment == "test" else "INVALIDITY_PROD"
        forbidden_prefix = "INVALIDITY_PROD_" if environment == "test" else "INVALIDITY_TEST_"
        forbidden = sorted(
            key for key, value in env.items()
            if str(key).startswith(forbidden_prefix) and str(value or "").strip()
        )
        if forbidden:
            raise ConfigurationError(
                f"{environment} 环境发现跨环境变量 {forbidden_prefix}: {', '.join(forbidden)}"
            )

        expected_port = EXPECTED_PORTS[environment]
        service_port = _positive_int(env.get("INVALIDITY_PORT"), 0, "INVALIDITY_PORT")
        if service_port != expected_port:
            raise ConfigurationError(
                f"{environment} 服务端口必须是 {expected_port}，当前为 {service_port}"
            )
        api_base_url = _exact_loopback_url(
            _required(env, f"{prefix}_API_URL"),
            name=f"{prefix}_API_URL",
            port=expected_port,
            path="",
        )
        api_token = _validated_token(env, f"{prefix}_API_TOKEN")

        module1_url = _exact_loopback_url(
            _required(env, f"{prefix}_MODULE1_API_URL"),
            name=f"{prefix}_MODULE1_API_URL",
            port=EXPECTED_MODULE1_PORTS[environment],
            path="/run",
        )
        parser_api_token: str | None = None
        module1_api_token: str | None = None
        if environment == "test":
            # The isolated 5201 compatibility parser has an independent
            # credential.  Reusing the 5209 API token would couple two trust
            # boundaries and make accidental cross-calls harder to detect.
            parser_api_token = _validated_token(env, "INVALIDITY_TEST_PARSER_TOKEN")
            if parser_api_token == api_token:
                raise ConfigurationError(
                    "INVALIDITY_TEST_PARSER_TOKEN 必须与 INVALIDITY_TEST_API_TOKEN 不同"
                )
            module1_api_token = parser_api_token
            module1_auth_mode = "bearer"
        else:
            candidate_token = str(
                env.get("INVALIDITY_PROD_MODULE1_API_TOKEN", "") or ""
            ).strip()
            if candidate_token:
                module1_api_token = _validated_token(
                    env, "INVALIDITY_PROD_MODULE1_API_TOKEN"
                )
                if module1_api_token == api_token:
                    raise ConfigurationError(
                        "INVALIDITY_PROD_MODULE1_API_TOKEN 必须与 "
                        "INVALIDITY_PROD_API_TOKEN 不同"
                    )
                module1_auth_mode = "bearer"
            else:
                module1_auth_mode = _required(
                    env, "INVALIDITY_PROD_MODULE1_AUTH_MODE"
                ).lower()
                if module1_auth_mode != "legacy_unauthenticated":
                    raise ConfigurationError(
                        "正式 5101 未配置独立 Bearer token 时，必须显式设置 "
                        "INVALIDITY_PROD_MODULE1_AUTH_MODE=legacy_unauthenticated"
                    )
        patent_provider = _required(env, f"{prefix}_PATENT_PROVIDER")
        patent_provider = patent_provider.lower().replace("-", "_")
        if patent_provider not in {"google_patents", "epo_ops", "patsnap"}:
            raise ConfigurationError(
                f"{prefix}_PATENT_PROVIDER 不是已批准的 live provider"
            )
        epo_ops_consumer_key = str(
            env.get(f"{prefix}_EPO_OPS_KEY", "") or ""
        ).strip()
        epo_ops_consumer_secret = str(
            env.get(f"{prefix}_EPO_OPS_SECRET", "") or ""
        ).strip()
        if bool(epo_ops_consumer_key) != bool(epo_ops_consumer_secret):
            raise ConfigurationError(
                f"{prefix}_EPO_OPS_KEY 与 {prefix}_EPO_OPS_SECRET 必须同时配置"
            )
        if patent_provider == "epo_ops" and not epo_ops_consumer_key:
            raise ConfigurationError(
                f"{prefix}_PATENT_PROVIDER=epo_ops 时，专利检索与取回必须配置当前环境的 "
                f"{prefix}_EPO_OPS_KEY 与 {prefix}_EPO_OPS_SECRET"
            )
        patsnap_api_key = _optional_patsnap_api_key(
            env,
            f"{prefix}_PATSNAP_API_KEY",
        )
        patsnap_base_url = _exact_patsnap_base_url(
            str(env.get(f"{prefix}_PATSNAP_BASE_URL") or PATSNAP_BASE_URL).strip(),
            name=f"{prefix}_PATSNAP_BASE_URL",
        )
        patsnap_count_path = _patsnap_path(
            str(env.get(f"{prefix}_PATSNAP_COUNT_PATH") or PATSNAP_COUNT_PATH).strip(),
            name=f"{prefix}_PATSNAP_COUNT_PATH",
            allowed=PATSNAP_COUNT_PATHS,
        )
        patsnap_search_path = _patsnap_path(
            str(env.get(f"{prefix}_PATSNAP_SEARCH_PATH") or PATSNAP_SEARCH_PATH).strip(),
            name=f"{prefix}_PATSNAP_SEARCH_PATH",
            allowed=PATSNAP_SEARCH_PATHS,
        )
        if patent_provider == "patsnap" and not patsnap_api_key:
            raise ConfigurationError(
                f"{prefix}_PATENT_PROVIDER=patsnap 时必须配置当前环境的 "
                f"{prefix}_PATSNAP_API_KEY"
            )
        if patent_provider == "patsnap" and not epo_ops_consumer_key:
            raise ConfigurationError(
                f"{prefix}_PATENT_PROVIDER=patsnap 时，候选发现后的官方原文取回"
                f"必须配置当前环境的 {prefix}_EPO_OPS_KEY 与 "
                f"{prefix}_EPO_OPS_SECRET"
            )
        npl_provider = _required(env, f"{prefix}_NPL_PROVIDER")
        if npl_provider.lower() not in {
            "arxiv",
            "arxiv_atom",
            "composite_npl",
            "arxiv_openalex_crossref",
            "arxiv_openalex_crossref_web",
        }:
            raise ConfigurationError(
                f"{prefix}_NPL_PROVIDER 不是已批准的 live provider 组合"
            )
        llm_base_url = _required(env, f"{prefix}_LLM_BASE_URL")
        try:
            llm_url = urlsplit(llm_base_url)
        except ValueError as exc:
            raise ConfigurationError(f"{prefix}_LLM_BASE_URL 不是有效 URL") from exc
        if llm_url.scheme not in {"http", "https"} or not llm_url.hostname:
            raise ConfigurationError(f"{prefix}_LLM_BASE_URL 必须是 http(s) URL")
        llm_api_key = _required(env, f"{prefix}_LLM_API_KEY")
        llm_model = _required(env, f"{prefix}_LLM_MODEL")

        if not llm_model.lower().startswith("glm-4.6v"):
            raise ConfigurationError(
                f"无效检索必须使用 glm-4.6v 系列多模态模型，当前为 {llm_model}"
            )

        artifact_root = _isolated_artifact_root(
            _required(env, "INVALIDITY_ARTIFACT_ROOT"), environment
        )
        allowed_source_roots = _allowed_source_roots(
            _required(env, "INVALIDITY_ALLOWED_SOURCE_ROOTS"), environment
        )

        max_rounds = _positive_int(
            env.get("INVALIDITY_MAX_ROUNDS"), 5, "INVALIDITY_MAX_ROUNDS"
        )
        if max_rounds > 5:
            raise ConfigurationError(
                "INVALIDITY_MAX_ROUNDS 表示首轮后的 gap 检索次数，只能是 1..5"
            )

        parser_port: int | None = None
        if str(env.get("INVALIDITY_PARSER_PORT", "") or "").strip():
            parser_port = _positive_int(
                env.get("INVALIDITY_PARSER_PORT"), 5201, "INVALIDITY_PARSER_PORT"
            )
            if environment != "test" or parser_port != 5201:
                raise ConfigurationError(
                    "隔离解析兼容服务只允许 test 环境的 5201 端口"
                )

        return cls(
            environment=environment,
            service_port=service_port,
            api_base_url=api_base_url,
            api_token=api_token,
            database_url=_required(env, "INVALIDITY_DATABASE_URL"),
            database_schema=schema,
            artifact_root=artifact_root,
            allowed_source_roots=allowed_source_roots,
            module1_api_url=module1_url,
            patent_provider=patent_provider,
            npl_provider=npl_provider,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            patsnap_api_key=patsnap_api_key,
            patsnap_base_url=patsnap_base_url,
            patsnap_count_path=patsnap_count_path,
            patsnap_search_path=patsnap_search_path,
            epo_ops_consumer_key=epo_ops_consumer_key or None,
            epo_ops_consumer_secret=epo_ops_consumer_secret or None,
            module1_api_token=module1_api_token,
            module1_auth_mode=module1_auth_mode,
            parser_api_token=parser_api_token,
            llm_timeout_seconds=_positive_int(
                env.get("INVALIDITY_LLM_TIMEOUT_SECONDS"),
                360,
                "INVALIDITY_LLM_TIMEOUT_SECONDS",
            ),
            llm_direct_attempt_timeout_seconds=_positive_int(
                env.get("INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS"),
                180,
                "INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS",
            ),
            llm_direct_probe_timeout_seconds=_positive_int(
                env.get("INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS"),
                5,
                "INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS",
            ),
            llm_i2_timeout_seconds=_positive_int(
                env.get("INVALIDITY_I2_LLM_TIMEOUT_SECONDS"),
                360,
                "INVALIDITY_I2_LLM_TIMEOUT_SECONDS",
            ),
            llm_i2_direct_attempt_timeout_seconds=_positive_int(
                env.get("INVALIDITY_I2_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS"),
                180,
                "INVALIDITY_I2_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS",
            ),
            max_rounds=max_rounds,
            max_candidates_per_query=_positive_int(
                env.get("INVALIDITY_MAX_CANDIDATES_PER_QUERY"),
                5,
                "INVALIDITY_MAX_CANDIDATES_PER_QUERY",
            ),
            worker_concurrency=_bounded_positive_int(
                env.get("INVALIDITY_WORKER_CONCURRENCY"),
                2,
                "INVALIDITY_WORKER_CONCURRENCY",
                maximum=4,
            ),
            worker_poll_seconds=_positive_int(
                env.get("INVALIDITY_WORKER_POLL_SECONDS"),
                2,
                "INVALIDITY_WORKER_POLL_SECONDS",
            ),
            job_lease_seconds=_positive_int(
                env.get("INVALIDITY_JOB_LEASE_SECONDS"),
                900,
                "INVALIDITY_JOB_LEASE_SECONDS",
            ),
            parser_port=parser_port,
            source_fetch_timeout_seconds=_positive_int(
                env.get("INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS"),
                30,
                "INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS",
            ),
            source_max_bytes=_positive_int(
                env.get("INVALIDITY_SOURCE_MAX_BYTES"),
                50 * 1024 * 1024,
                "INVALIDITY_SOURCE_MAX_BYTES",
            ),
            image_max_bytes=_positive_int(
                env.get("INVALIDITY_IMAGE_MAX_BYTES"),
                15 * 1024 * 1024,
                "INVALIDITY_IMAGE_MAX_BYTES",
            ),
            source_max_redirects=_positive_int(
                env.get("INVALIDITY_SOURCE_MAX_REDIRECTS"),
                5,
                "INVALIDITY_SOURCE_MAX_REDIRECTS",
            ),
        )

    def isolation_summary(self) -> dict[str, str | int]:
        return {
            "environment": self.environment,
            "service_port": self.service_port,
            "database_schema": self.database_schema,
            "artifact_root": str(self.artifact_root),
            "module1_api_url": self.module1_api_url,
            "module1_auth_mode": self.module1_auth_mode,
            "patent_provider": self.patent_provider,
            "npl_provider": self.npl_provider,
            "llm_model": self.llm_model,
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_direct_attempt_timeout_seconds": (
                self.llm_direct_attempt_timeout_seconds
            ),
            "llm_direct_probe_timeout_seconds": self.llm_direct_probe_timeout_seconds,
            "llm_i2_timeout_seconds": self.llm_i2_timeout_seconds,
            "llm_i2_direct_attempt_timeout_seconds": (
                self.llm_i2_direct_attempt_timeout_seconds
            ),
            "max_rounds": self.max_rounds,
            "worker_concurrency": self.worker_concurrency,
        }
