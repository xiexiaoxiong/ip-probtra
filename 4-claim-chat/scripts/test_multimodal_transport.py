#!/usr/bin/env python3
"""验证模块4会把文字和图片一起发送给 GLM-4.6V，且不降级成纯文本。"""

import os
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from utils import local_llm  # noqa: E402


def main() -> None:
    env_keys = [
        "LOCAL_LLM_BASE_URL",
        "LOCAL_LLM_API_KEY",
        "LOCAL_LLM_DEFAULT_MODEL",
        "LOCAL_LLM_FAST_MODEL",
        "LOCAL_LLM_VISION_MODEL",
        "LOCAL_LLM_FALLBACK_BASE_URL",
        "LOCAL_LLM_FALLBACK_API_KEY",
        "LOCAL_LLM_DIRECT_ROUTE_MODE",
        "LOCAL_LLM_FORCE_DIRECT_ROUTE",
    ]
    original_env = {key: os.environ.get(key) for key in env_keys}
    original_invoke = local_llm.invoke_openai_compatible_via_urllib
    original_direct_check = local_llm.should_use_bigmodel_direct_route
    captured_payloads: list[dict] = []

    try:
        os.environ.update(
            {
                "LOCAL_LLM_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
                "LOCAL_LLM_API_KEY": "test-key",
                "LOCAL_LLM_DEFAULT_MODEL": "glm-4.6v",
                "LOCAL_LLM_FAST_MODEL": "glm-4.6v",
                "LOCAL_LLM_VISION_MODEL": "glm-4.6v",
                "LOCAL_LLM_FALLBACK_BASE_URL": "",
                "LOCAL_LLM_FALLBACK_API_KEY": "",
                "LOCAL_LLM_DIRECT_ROUTE_MODE": "off",
                "LOCAL_LLM_FORCE_DIRECT_ROUTE": "0",
            }
        )

        def fake_invoke(**kwargs):
            payload = kwargs["payload"]
            captured_payloads.append(payload)
            return {
                "id": "multimodal-test",
                "model": payload["model"],
                "choices": [{"message": {"content": "图文输入已接收"}, "finish_reason": "stop"}],
                "usage": {},
            }

        local_llm.invoke_openai_compatible_via_urllib = fake_invoke
        local_llm.should_use_bigmodel_direct_route = lambda _: False
        local_llm._LLM_COOLDOWN_UNTIL = 0.0
        local_llm._LLM_COOLDOWN_REASON = ""

        response = local_llm.invoke_local_llm(
            messages=[
                SystemMessage(content="同时读取文字和图片。"),
                HumanMessage(
                    content=[
                        {"type": "text", "text": "商品名称：开放式头戴耳机"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
                            },
                        },
                    ]
                ),
            ],
            model="glm-4.6v",
            max_completion_tokens=128,
        )

        assert response.content == "图文输入已接收"
        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert payload["model"] == "glm-4.6v"
        user_content = payload["messages"][1]["content"]
        assert isinstance(user_content, list)
        assert any(item.get("type") == "text" for item in user_content)
        assert any(item.get("type") == "image_url" for item in user_content)
        print("multimodal transport ok: model=glm-4.6v, text=true, image=true")
    finally:
        local_llm.invoke_openai_compatible_via_urllib = original_invoke
        local_llm.should_use_bigmodel_direct_route = original_direct_check
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    main()
