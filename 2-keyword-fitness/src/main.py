import argparse
import asyncio
import json
import threading
import traceback
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, AsyncIterable, AsyncGenerator, Optional
import cozeloop
import uvicorn
import time
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from coze_coding_utils.runtime_ctx.context import new_context, Context
from coze_coding_utils.helper import graph_helper
from coze_coding_utils.log.node_log import LOG_FILE
from coze_coding_utils.log.write_log import setup_logging, request_context
from coze_coding_utils.log.config import LOG_LEVEL
from coze_coding_utils.error.classifier import ErrorClassifier, classify_error
from coze_coding_utils.helper.stream_runner import AgentStreamRunner, WorkflowStreamRunner,agent_stream_handler,workflow_stream_handler, RunOpt

setup_logging(
    log_file=LOG_FILE,
    max_bytes=100 * 1024 * 1024, # 100MB
    backup_count=5,
    log_level=LOG_LEVEL,
    use_json_format=True,
    console_output=True
)

logger = logging.getLogger(__name__)


def bootstrap_local_env() -> None:
    import os
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None

    env_candidates = [
        Path(__file__).resolve().parents[2] / "IP-protral" / ".env.local",
        Path(__file__).resolve().parents[1] / ".env.local",
        Path.cwd() / ".env.local",
    ]
    if load_dotenv:
        for env_path in env_candidates:
            if env_path.exists():
                load_dotenv(env_path, override=False)

    if not os.getenv("PGDATABASE_URL") and os.getenv("DATABASE_URL"):
        os.environ["PGDATABASE_URL"] = os.getenv("DATABASE_URL", "")

    local_llm_api_key = os.getenv("LOCAL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    local_llm_base_url = os.getenv("LOCAL_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    local_search_base_url = os.getenv("LOCAL_SEARCH_BASE_URL")

    if local_llm_api_key and not os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY"):
        os.environ["COZE_WORKLOAD_IDENTITY_API_KEY"] = local_llm_api_key
    if local_llm_base_url and not os.getenv("COZE_INTEGRATION_MODEL_BASE_URL"):
        os.environ["COZE_INTEGRATION_MODEL_BASE_URL"] = local_llm_base_url
    if local_search_base_url and not os.getenv("COZE_INTEGRATION_BASE_URL"):
        os.environ["COZE_INTEGRATION_BASE_URL"] = local_search_base_url
    elif local_llm_base_url and not os.getenv("COZE_INTEGRATION_BASE_URL"):
        os.environ["COZE_INTEGRATION_BASE_URL"] = local_llm_base_url


def resolve_local_model_alias(model_name: Optional[str]) -> str:
    import os

    requested = str(model_name or "").strip()
    default_model = os.getenv("LOCAL_LLM_DEFAULT_MODEL", "glm-4.7").strip() or "glm-4.7"
    fast_model = os.getenv("LOCAL_LLM_FAST_MODEL", "glm-4.5-air").strip() or default_model
    vision_model = os.getenv("LOCAL_LLM_VISION_MODEL", "glm-4.5v").strip() or default_model

    if not requested:
        return default_model

    lower = requested.lower()
    last_segment = lower.rsplit("-", 1)[-1]

    if lower.startswith("glm-") and not last_segment.isdigit():
        return requested
    if "vision" in lower:
        return vision_model
    if lower.startswith("doubao-"):
        if "pro" in lower:
            return default_model
        if "mini" in lower or "lite" in lower:
            return fast_model
        return default_model
    if lower.startswith("glm-5-0-"):
        return fast_model
    if lower.startswith("glm-") and last_segment.isdigit():
        return default_model
    if "mini" in lower or "lite" in lower or "flash" in lower or "air" in lower:
        return fast_model
    return default_model


def should_use_local_http_invoke() -> bool:
    import os

    flag = (os.getenv("LOCAL_LLM_FORCE_HTTP_INVOKE") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True

    base_url = (
        os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
        or os.getenv("LOCAL_LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or ""
    ).lower()
    return "bigmodel.cn" in base_url


def convert_message_content_for_openai(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    converted: list[Any] = []
    for item in content:
        if isinstance(item, str):
            converted.append({"type": "text", "text": item})
            continue
        if not isinstance(item, dict):
            converted.append({"type": "text", "text": str(item)})
            continue

        item_type = item.get("type")
        if item_type == "text":
            converted.append({"type": "text", "text": str(item.get("text", ""))})
        elif item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, str):
                converted.append({"type": "image_url", "image_url": {"url": image_url}})
            elif isinstance(image_url, dict):
                converted.append({"type": "image_url", "image_url": image_url})
        else:
            converted.append(item)

    return converted


def convert_message_role_for_openai(message: Any) -> str:
    role = str(getattr(message, "type", "user") or "user").lower()
    if role == "human":
        return "user"
    if role == "ai":
        return "assistant"
    return role


def should_use_bigmodel_direct_route(request_url: str) -> bool:
    import os
    import socket
    from urllib.parse import urlparse

    force_flag = (os.getenv("LOCAL_LLM_FORCE_DIRECT_ROUTE") or "").strip().lower()
    if force_flag in {"1", "true", "yes", "on"}:
        return True

    disable_flag = (os.getenv("LOCAL_LLM_DISABLE_DIRECT_ROUTE") or "").strip().lower()
    if disable_flag in {"1", "true", "yes", "on"}:
        return False

    host = (urlparse(request_url).hostname or "").strip().lower()
    if host != "open.bigmodel.cn":
        return False

    try:
        resolved = socket.gethostbyname(host)
    except Exception:
        return True
    return resolved.startswith("198.18.")


def invoke_bigmodel_via_direct_route(
    request_url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    import os
    import subprocess
    from urllib.parse import urlparse

    parsed = urlparse(request_url)
    host = parsed.hostname or "open.bigmodel.cn"
    port = parsed.port or 443
    interface = (os.getenv("LOCAL_LLM_DIRECT_INTERFACE") or "en0").strip() or "en0"
    ip_candidates = [
        item.strip()
        for item in (
            os.getenv("LOCAL_LLM_BIGMODEL_DIRECT_IPS")
            or "122.10.144.213,156.59.96.30,129.227.65.212"
        ).split(",")
        if item.strip()
    ]
    if not ip_candidates:
        raise RuntimeError("未配置可用的智谱直连IP")

    request_body = json.dumps(payload, ensure_ascii=False)
    errors: list[str] = []

    for ip in ip_candidates:
        command = [
            "curl",
            "-sS",
            "--fail-with-body",
            "--interface",
            interface,
            "--connect-timeout",
            "20",
            "--max-time",
            str(int(timeout_seconds)),
            "--connect-to",
            f"{host}:{port}:{ip}:{port}",
            "-X",
            "POST",
            request_url,
            "-H",
            f"Authorization: Bearer {api_key}",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            request_body,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return json.loads(result.stdout or "{}")

        detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
        errors.append(f"{ip}: {detail}")

    raise RuntimeError("智谱直连兜底失败: " + " | ".join(errors))


def payload_uses_image_inputs(payload: Dict[str, Any]) -> bool:
    for message in payload.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                return True
    return False


def should_prefer_fallback_for_text(payload: Dict[str, Any]) -> bool:
    import os

    provider = (os.getenv("LOCAL_LLM_TEXT_PROVIDER") or "").strip().lower()
    return provider == "fallback" and not payload_uses_image_inputs(payload)


def should_prefer_fallback_for_vision(payload: Dict[str, Any]) -> bool:
    import os

    provider = (os.getenv("LOCAL_LLM_VISION_PROVIDER") or "").strip().lower()
    return provider == "fallback" and payload_uses_image_inputs(payload)


def resolve_fallback_model_alias(model_name: str) -> str:
    import os

    fallback_default = os.getenv("LOCAL_LLM_FALLBACK_DEFAULT_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro"
    fallback_fast = os.getenv("LOCAL_LLM_FALLBACK_FAST_MODEL", "deepseek-v4-flash").strip() or fallback_default
    fallback_vision = (os.getenv("LOCAL_LLM_FALLBACK_VISION_MODEL") or "").strip()

    requested = str(model_name or "").strip().lower()
    if "vision" in requested:
        return fallback_vision
    if requested.startswith("glm-5-0-") or any(tag in requested for tag in ("mini", "lite", "flash", "air")):
        return fallback_fast
    return fallback_default


def invoke_openai_compatible_via_urllib(
    request_url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout_seconds: float,
    max_attempts: int,
    retry_interval: float,
) -> Dict[str, Any]:
    import json
    import ssl
    import time
    import urllib.error
    import urllib.request

    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ssl_context = ssl.create_default_context()
    if hasattr(ssl, "TLSVersion"):
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(
            request_url,
            data=request_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl_context) as http_response:
                raw_response = http_response.read().decode("utf-8")
            return json.loads(raw_response or "{}")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            last_error = RuntimeError(
                f"HTTP {error.code}: {(body or str(error.reason)).strip()[:1000]}"
            )
        except Exception as error:
            last_error = error

        if attempt < max_attempts:
            time.sleep(retry_interval * attempt)

    detail = str(last_error).strip()[:1000] if last_error else "未知错误"
    raise RuntimeError(detail)


def invoke_local_llm_via_http(
    messages: list[Any],
    model: str,
    temperature: Optional[float] = None,
    frequency_penalty: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
    max_completion_tokens: Optional[int] = None,
):
    import os
    from langchain_core.messages import AIMessage

    base_url = (
        os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")
        or os.getenv("LOCAL_LLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or ""
    ).rstrip("/")
    api_key = (
        os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
        or os.getenv("LOCAL_LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )

    if not base_url:
        raise RuntimeError("本地模型调用失败: 缺少 LOCAL_LLM_BASE_URL")
    if not api_key:
        raise RuntimeError("本地模型调用失败: 缺少 LOCAL_LLM_API_KEY")

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": convert_message_role_for_openai(message),
                "content": convert_message_content_for_openai(getattr(message, "content", "")),
            }
            for message in messages
        ],
        "stream": False,
    }

    if temperature is not None:
        payload["temperature"] = temperature
    if frequency_penalty is not None:
        payload["frequency_penalty"] = frequency_penalty
    if top_p is not None:
        payload["top_p"] = top_p

    token_limit = max_tokens or max_completion_tokens
    if token_limit:
        payload["max_tokens"] = token_limit

    timeout_seconds = float(os.getenv("LOCAL_LLM_HTTP_TIMEOUT_SECONDS", "300") or "300")
    max_attempts = max(1, int(os.getenv("LOCAL_LLM_HTTP_RETRIES", "3") or "3"))
    retry_interval = float(os.getenv("LOCAL_LLM_HTTP_RETRY_INTERVAL_SECONDS", "2") or "2")
    request_url = f"{base_url}/chat/completions"

    last_error: Exception | None = None
    response: Dict[str, Any] | None = None
    fallback_base_url = (os.getenv("LOCAL_LLM_FALLBACK_BASE_URL") or "").rstrip("/")
    fallback_api_key = os.getenv("LOCAL_LLM_FALLBACK_API_KEY") or ""
    fallback_model = resolve_fallback_model_alias(model)

    if (
        (should_prefer_fallback_for_text(payload) or should_prefer_fallback_for_vision(payload))
        and fallback_base_url
        and fallback_api_key
        and fallback_model
    ):
        try:
            logger.info("Prefer fallback LLM request, model=%s", fallback_model)
            fallback_payload = dict(payload)
            fallback_payload["model"] = fallback_model
            response = invoke_openai_compatible_via_urllib(
                request_url=f"{fallback_base_url}/chat/completions",
                api_key=fallback_api_key,
                payload=fallback_payload,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                retry_interval=retry_interval,
            )
        except Exception as error:
            last_error = error

    if response is None and should_use_bigmodel_direct_route(request_url):
        try:
            response = invoke_bigmodel_via_direct_route(
                request_url=request_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            last_error = error

    if response is None:
        try:
            response = invoke_openai_compatible_via_urllib(
                request_url=request_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                retry_interval=retry_interval,
            )
        except Exception as error:
            last_error = error

    if response is None:
        if fallback_base_url and fallback_api_key and fallback_model:
            try:
                logger.warning("Primary LLM failed, fallback to secondary provider model=%s", fallback_model)
                fallback_payload = dict(payload)
                fallback_payload["model"] = fallback_model
                response = invoke_openai_compatible_via_urllib(
                    request_url=f"{fallback_base_url}/chat/completions",
                    api_key=fallback_api_key,
                    payload=fallback_payload,
                    timeout_seconds=timeout_seconds,
                    max_attempts=max_attempts,
                    retry_interval=retry_interval,
                )
            except Exception as error:
                last_error = error

    if response is None:
        detail = str(last_error).strip()[:1000] if last_error else "未知错误"
        raise RuntimeError(f"本地模型 HTTP 调用失败: {detail}")

    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content", "")

    return AIMessage(
        content=content,
        response_metadata={
            "id": response.get("id"),
            "model": response.get("model"),
            "usage": response.get("usage"),
        },
    )


def install_local_llm_model_alias_patch() -> None:
    try:
        from coze_coding_dev_sdk import LLMClient
    except Exception as exc:
        logger.warning("Skip LLM model alias patch: %s", exc)
        return

    if getattr(LLMClient.invoke, "__local_model_alias_patched__", False):
        return

    original_invoke = LLMClient.invoke

    def patched_invoke(self, *args, **kwargs):
        requested_model = kwargs.get("model")
        resolved_model = resolve_local_model_alias(requested_model)
        if resolved_model != requested_model:
            logger.info("Rewrite LLM model from %s to %s", requested_model, resolved_model)
            kwargs["model"] = resolved_model
        elif requested_model is None:
            kwargs["model"] = resolved_model
        if should_use_local_http_invoke():
            logger.info("Use local HTTP LLM invoke for model %s", kwargs["model"])
            return invoke_local_llm_via_http(
                messages=kwargs.get("messages") or (args[0] if args else []),
                model=kwargs["model"],
                temperature=kwargs.get("temperature"),
                frequency_penalty=kwargs.get("frequency_penalty"),
                top_p=kwargs.get("top_p"),
                max_tokens=kwargs.get("max_tokens"),
                max_completion_tokens=kwargs.get("max_completion_tokens"),
            )
        return original_invoke(self, *args, **kwargs)

    patched_invoke.__local_model_alias_patched__ = True
    LLMClient.invoke = patched_invoke


bootstrap_local_env()
install_local_llm_model_alias_patch()
from coze_coding_utils.helper.agent_helper import to_stream_input
from coze_coding_utils.openai.handler import OpenAIChatHandler
from coze_coding_utils.log.parser import LangGraphParser
from coze_coding_utils.log.err_trace import extract_core_stack
from coze_coding_utils.log.loop_trace import init_run_config, init_agent_config


# 超时配置常量
TIMEOUT_SECONDS = 900  # 15分钟

class GraphService:
    def __init__(self):
        # 用于跟踪正在运行的任务（使用asyncio.Task）
        self.running_tasks: Dict[str, asyncio.Task] = {}
        # 错误分类器
        self.error_classifier = ErrorClassifier()
        # stream runner
        self._agent_stream_runner = AgentStreamRunner()
        self._workflow_stream_runner = WorkflowStreamRunner()
        self._graph = None
        self._graph_lock = threading.Lock()

    def _get_graph(self, ctx=Context):
        if graph_helper.is_agent_proj():
            return graph_helper.get_agent_instance("agents.agent", ctx)

        if self._graph is not None:
            return self._graph
        with self._graph_lock:
            if self._graph is not None:
                return self._graph
            self._graph = graph_helper.get_graph_instance("graphs.graph")
            return self._graph

    @staticmethod
    def _sse_event(data: Any, event_id: Any = None) -> str:
        id_line = f"id: {event_id}\n" if event_id else ""
        return f"{id_line}event: message\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

    def _get_stream_runner(self):
        if graph_helper.is_agent_proj():
            return self._agent_stream_runner
        else:
            return self._workflow_stream_runner

    # 流式运行（原始迭代器）：本地调用使用
    def stream(self, payload: Dict[str, Any], run_config: RunnableConfig, ctx=Context) -> Iterable[Any]:
        graph = self._get_graph(ctx)
        stream_runner = self._get_stream_runner()
        for chunk in stream_runner.stream(payload, graph, run_config, ctx):
            yield chunk

    # 同步运行：本地/HTTP 通用
    async def run(self, payload: Dict[str, Any], ctx=None) -> Dict[str, Any]:
        if ctx is None:
            ctx = new_context("run")

        run_id = ctx.run_id
        logger.info(f"Starting run with run_id: {run_id}")

        try:
            graph = self._get_graph(ctx)
            # custom tracer
            run_config = init_run_config(graph, ctx)
            run_config["configurable"] = {"thread_id": ctx.run_id}

            # 直接调用，LangGraph会在当前任务上下文中执行
            # 如果当前任务被取消，LangGraph的执行也会被取消
            return await graph.ainvoke(payload, config=run_config, context=ctx)

        except asyncio.CancelledError:
            logger.info(f"Run {run_id} was cancelled")
            return {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        except Exception as e:
            # 使用错误分类器分类错误
            err = self.error_classifier.classify(e, {"node_name": "run", "run_id": run_id})
            # 记录详细的错误信息和堆栈跟踪
            logger.error(
                f"Error in GraphService.run: [{err.code}] {err.message}\n"
                f"Category: {err.category.name}\n"
                f"Traceback:\n{extract_core_stack()}"
            )
            # 保留原始异常堆栈，便于上层返回真正的报错位置
            raise
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)

    # 流式运行（SSE 格式化）：HTTP 路由使用
    async def stream_sse(self, payload: Dict[str, Any], ctx=None, run_opt: Optional[RunOpt] = None) -> AsyncGenerator[str, None]:
        if ctx is None:
            ctx = new_context(method="stream_sse")
        if run_opt is None:
            run_opt = RunOpt()

        run_id = ctx.run_id
        logger.info(f"Starting stream with run_id: {run_id}")
        graph = self._get_graph(ctx)
        if graph_helper.is_agent_proj():
            run_config = init_agent_config(graph, ctx)
        else:
            run_config = init_run_config(graph, ctx)  # vibeflow

        is_workflow = not graph_helper.is_agent_proj()

        try:
            async for chunk in self.astream(payload, graph, run_config=run_config, ctx=ctx, run_opt=run_opt):
                if is_workflow and isinstance(chunk, tuple):
                    event_id, data = chunk
                    yield self._sse_event(data, event_id)
                else:
                    yield self._sse_event(chunk)
        finally:
            # 清理任务记录
            self.running_tasks.pop(run_id, None)
            cozeloop.flush()

    # 取消执行 - 使用asyncio的标准方式
    def cancel_run(self, run_id: str, ctx: Optional[Context] = None) -> Dict[str, Any]:
        """
        取消指定run_id的执行

        使用asyncio.Task.cancel()来取消任务,这是标准的Python异步取消机制。
        LangGraph会在节点之间检查CancelledError,实现优雅的取消。
        """
        logger.info(f"Attempting to cancel run_id: {run_id}")

        # 查找对应的任务
        if run_id in self.running_tasks:
            task = self.running_tasks[run_id]
            if not task.done():
                # 使用asyncio的标准取消机制
                # 这会在下一个await点抛出CancelledError
                task.cancel()
                logger.info(f"Cancellation requested for run_id: {run_id}")
                return {
                    "status": "success",
                    "run_id": run_id,
                    "message": "Cancellation signal sent, task will be cancelled at next await point"
                }
            else:
                logger.info(f"Task already completed for run_id: {run_id}")
                return {
                    "status": "already_completed",
                    "run_id": run_id,
                    "message": "Task has already completed"
                }
        else:
            logger.warning(f"No active task found for run_id: {run_id}")
            return {
                "status": "not_found",
                "run_id": run_id,
                "message": "No active task found with this run_id. Task may have already completed or run_id is invalid."
            }

    # 运行指定节点：本地/HTTP 通用
    async def run_node(self, node_id: str, payload: Dict[str, Any], ctx=None) -> Any:
        if ctx is None or Context.run_id == "":
            ctx = new_context(method="node_run")

        _graph = self._get_graph()
        node_func, input_cls, output_cls = graph_helper.get_graph_node_func_with_inout(_graph.get_graph(), node_id)
        if node_func is None or input_cls is None:
            raise KeyError(f"node_id '{node_id}' not found")

        parser = LangGraphParser(_graph)
        metadata = parser.get_node_metadata(node_id) or {}

        _g = StateGraph(input_cls, input_schema=input_cls, output_schema=output_cls)
        _g.add_node("sn", node_func, metadata=metadata)
        _g.set_entry_point("sn")
        _g.add_edge("sn", END)
        _graph = _g.compile()

        run_config = init_run_config(_graph, ctx)
        return await _graph.ainvoke(payload, config=run_config)

    def graph_inout_schema(self) -> Any:
        if graph_helper.is_agent_proj():
            return {"input_schema": {}, "output_schema": {}}
        builder = getattr(self._get_graph(), 'builder', None)
        if builder is not None:
            input_cls = getattr(builder, 'input_schema', None) or self.graph.get_input_schema()
            output_cls = getattr(builder, 'output_schema', None) or self.graph.get_output_schema()
        else:
            logger.warning(f"No builder input schema found for graph_inout_schema, using graph input schema instead")
            input_cls = self.graph.get_input_schema()
            output_cls = self.graph.get_output_schema()

        return {
            "input_schema": input_cls.model_json_schema(), 
            "output_schema": output_cls.model_json_schema(),
            "code":0,
            "msg":""
        }

    async def astream(self, payload: Dict[str, Any], graph: CompiledStateGraph, run_config: RunnableConfig, ctx=Context, run_opt: Optional[RunOpt] = None) -> AsyncIterable[Any]:
        stream_runner = self._get_stream_runner()
        async for chunk in stream_runner.astream(payload, graph, run_config, ctx, run_opt):
            yield chunk


service = GraphService()
app = FastAPI()

# OpenAI 兼容接口处理器
openai_handler = OpenAIChatHandler(service)


@app.post("/run")
async def http_run(request: Request) -> Dict[str, Any]:
    global result
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {traceback.format_exc()}, error: {e}")

    ctx = new_context(method="run", headers=request.headers)
    run_id = ctx.run_id
    request_context.set(ctx)

    logger.info(
        f"Received request for /run: "
        f"run_id={run_id}, "
        f"query={dict(request.query_params)}, "
        f"body={body_text}"
    )

    try:
        payload = await request.json()

        # 创建任务并记录 - 这是关键，让我们可以通过run_id取消任务
        task = asyncio.create_task(service.run(payload, ctx))
        service.running_tasks[run_id] = task

        try:
            result = await asyncio.wait_for(task, timeout=float(TIMEOUT_SECONDS))
        except asyncio.TimeoutError:
            logger.error(f"Run execution timeout after {TIMEOUT_SECONDS}s for run_id: {run_id}")
            task.cancel()
            try:
                result = await task
            except asyncio.CancelledError:
                return {
                    "status": "timeout",
                    "run_id": run_id,
                    "message": f"Execution timeout: exceeded {TIMEOUT_SECONDS} seconds"
                }

        if not result:
            result = {}
        if isinstance(result, dict):
            result["run_id"] = run_id
        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format, {extract_core_stack()}")

    except asyncio.CancelledError:
        logger.info(f"Request cancelled for run_id: {run_id}")
        result = {"status": "cancelled", "run_id": run_id, "message": "Execution was cancelled"}
        return result

    except Exception as e:
        # 使用错误分类器获取错误信息
        error_response = service.error_classifier.get_error_response(e, {"node_name": "http_run", "run_id": run_id})
        logger.error(
            f"Unexpected error in http_run: [{error_response['error_code']}] {error_response['error_message']}, "
            f"traceback: {traceback.format_exc()}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
                "stack_trace": extract_core_stack(),
            }
        )
    finally:
        cozeloop.flush()


HEADER_X_WORKFLOW_STREAM_MODE = "x-workflow-stream-mode"


def _register_task(run_id: str, task: asyncio.Task):
    service.running_tasks[run_id] = task


@app.post("/stream_run")
async def http_stream_run(request: Request):
    ctx = new_context(method="stream_run", headers=request.headers)
    workflow_stream_mode = request.headers.get(HEADER_X_WORKFLOW_STREAM_MODE, "").lower()
    workflow_debug = workflow_stream_mode == "debug"
    request_context.set(ctx)
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except Exception as e:
        body_text = str(raw_body)
        raise HTTPException(status_code=400,
                            detail=f"Invalid JSON format: {body_text}, traceback: {extract_core_stack()}, error: {e}")
    run_id = ctx.run_id
    is_agent = graph_helper.is_agent_proj()
    logger.info(
        f"Received request for /stream_run: "
        f"run_id={run_id}, "
        f"is_agent_project={is_agent}, "
        f"query={dict(request.query_params)}, "
        f"body={body_text}"
    )
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_stream_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format:{extract_core_stack()}")

    if is_agent:
        stream_generator = agent_stream_handler(
            payload=payload,
            ctx=ctx,
            run_id=run_id,
            stream_sse_func=service.stream_sse,
            sse_event_func=service._sse_event,
            error_classifier=service.error_classifier,
            register_task_func=_register_task,
        )
    else:
        stream_generator = workflow_stream_handler(
            payload=payload,
            ctx=ctx,
            run_id=run_id,
            stream_sse_func=service.stream_sse,
            sse_event_func=service._sse_event,
            error_classifier=service.error_classifier,
            register_task_func=_register_task,
            run_opt=RunOpt(workflow_debug=workflow_debug),
        )

    response = StreamingResponse(stream_generator, media_type="text/event-stream")
    return response

@app.post("/cancel/{run_id}")
async def http_cancel(run_id: str, request: Request):
    """
    取消指定run_id的执行

    使用asyncio.Task.cancel()实现取消,这是Python标准的异步任务取消机制。
    LangGraph会在节点之间的await点检查CancelledError,实现优雅取消。
    """
    ctx = new_context(method="cancel", headers=request.headers)
    request_context.set(ctx)
    logger.info(f"Received cancel request for run_id: {run_id}")
    result = service.cancel_run(run_id, ctx)
    return result


@app.post(path="/node_run/{node_id}")
async def http_node_run(node_id: str, request: Request):
    raw_body = await request.body()
    try:
        body_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        body_text = str(raw_body)
        raise HTTPException(status_code=400, detail=f"Invalid JSON format: {body_text}")
    ctx = new_context(method="node_run", headers=request.headers)
    request_context.set(ctx)
    logger.info(
        f"Received request for /node_run/{node_id}: "
        f"query={dict(request.query_params)}, "
        f"body={body_text}",
    )

    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in http_node_run: {e}, traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON format:{extract_core_stack()}")
    try:
        return await service.run_node(node_id, payload, ctx)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"node_id '{node_id}' not found or input miss required fields, traceback: {extract_core_stack()}")
    except Exception as e:
        # 使用错误分类器获取错误信息
        error_response = service.error_classifier.get_error_response(e, {"node_name": node_id})
        logger.error(
            f"Unexpected error in http_node_run: [{error_response['error_code']}] {error_response['error_message']}, "
            f"traceback: {traceback.format_exc()}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": error_response["error_code"],
                "error_message": error_response["error_message"],
                "stack_trace": extract_core_stack(),
            }
        )
    finally:
        cozeloop.flush()


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    """OpenAI Chat Completions API 兼容接口"""
    ctx = new_context(method="openai_chat", headers=request.headers)
    request_context.set(ctx)

    logger.info(f"Received request for /v1/chat/completions: run_id={ctx.run_id}")

    try:
        payload = await request.json()
        return await openai_handler.handle(payload, ctx)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in openai_chat_completions: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    finally:
        cozeloop.flush()


@app.get("/health")
async def health_check():
    try:
        # 这里可以添加更多的健康检查逻辑
        return {
            "status": "ok",
            "message": "Service is running",
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get(path="/graph_parameter")
async def http_graph_inout_parameter(request: Request):
    return service.graph_inout_schema()

def parse_args():
    parser = argparse.ArgumentParser(description="Start FastAPI server")
    parser.add_argument("-m", type=str, default="http", help="Run mode, support http,flow,node")
    parser.add_argument("-n", type=str, default="", help="Node ID for single node run")
    parser.add_argument("-p", type=int, default=5000, help="HTTP server port")
    parser.add_argument("-i", type=str, default="", help="Input JSON string for flow/node mode")
    return parser.parse_args()


def parse_input(input_str: str) -> Dict[str, Any]:
    """Parse input string, support both JSON string and plain text"""
    if not input_str:
        return {"text": "你好"}

    # Try to parse as JSON first
    try:
        return json.loads(input_str)
    except json.JSONDecodeError:
        # If not valid JSON, treat as plain text
        return {"text": input_str}

def start_http_server(port):
    import os
    workers = 1
    reload = False
    if graph_helper.is_dev_env():
        reload = True

    host = (os.getenv("WORKFLOW_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    logger.info(f"Start HTTP Server, Port: {port}, Workers: {workers}")
    uvicorn.run("main:app", host=host, port=port, reload=reload, workers=workers)

if __name__ == "__main__":
    args = parse_args()
    if args.m == "http":
        start_http_server(args.p)
    elif args.m == "flow":
        payload = parse_input(args.i)
        result = asyncio.run(service.run(payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "node" and args.n:
        payload = parse_input(args.i)
        result = asyncio.run(service.run_node(args.n, payload))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.m == "agent":
        agent_ctx = new_context(method="agent")
        for chunk in service.stream(
                {
                    "type": "query",
                    "session_id": "1",
                    "message": "你好",
                    "content": {
                        "query": {
                            "prompt": [
                                {
                                    "type": "text",
                                    "content": {"text": "现在几点了？请调用工具获取当前时间"},
                                }
                            ]
                        }
                    },
                },
                run_config={"configurable": {"session_id": "1"}},
                ctx=agent_ctx,
        ):
            print(chunk)
