import json
import logging
import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    SystemMessage,
    UserMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from config import KNOWLEDGE_BASE_DIR, SYSTEM_PROMPT, MAX_TURNS

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[agent] %(message)s")

app = BedrockAgentCoreApp()


def _emit_event(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {
        "message": json.dumps(
            {"event": event, "data": data},
            ensure_ascii=False,
        )
    }


def _parse_tool_input(chunks: list[str]) -> Any:
    raw = "".join(chunks).strip()
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _stringify_tool_result(content: str | list[dict[str, Any]] | None) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


@app.entrypoint
async def invocations(payload, context):
    prompt = payload.get("prompt", "")
    if not prompt:
        yield {"message": json.dumps({"error": "prompt is required"}, ensure_ascii=False)}
        return

    if not os.path.isdir(KNOWLEDGE_BASE_DIR):
        yield {"message": json.dumps({"error": "Knowledge base directory not found. Sync from S3 first."}, ensure_ascii=False)}
        return

    logger.info("received request: %s", json.dumps(payload, ensure_ascii=False))

    options = ClaudeAgentOptions(
        tools=["Read", "Grep", "Glob"],
        allowed_tools=["Read", "Grep", "Glob"],
        include_partial_messages=True,
        system_prompt=SYSTEM_PROMPT,
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
        cwd=KNOWLEDGE_BASE_DIR,
    )

    try:
        streamed_assistant_output = False
        pending_tool_uses: dict[int, dict[str, Any]] = {}

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, StreamEvent):
                streamed_assistant_output = True
                event = message.event
                event_type = event.get("type")

                if event_type == "content_block_start":
                    content_block = event.get("content_block", {})
                    if content_block.get("type") == "tool_use":
                        index = event.get("index", -1)
                        pending_tool_uses[index] = {
                            "id": content_block.get("id"),
                            "name": content_block.get("name"),
                            "chunks": [],
                        }

                        initial_input = content_block.get("input")
                        if initial_input:
                            pending_tool_uses[index]["chunks"].append(
                                json.dumps(initial_input, ensure_ascii=False)
                            )

                        logger.info("tool_start: %s", content_block.get("name"))
                        yield _emit_event(
                            "tool_use_start",
                            {
                                "id": content_block.get("id"),
                                "name": content_block.get("name"),
                            },
                        )

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    delta_type = delta.get("type")

                    if delta_type == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            yield _emit_event("text_delta", {"text": text})
                    elif delta_type == "input_json_delta":
                        index = event.get("index", -1)
                        chunk = delta.get("partial_json", "")
                        if chunk:
                            state = pending_tool_uses.setdefault(
                                index, {"id": None, "name": None, "chunks": []}
                            )
                            state["chunks"].append(chunk)

                elif event_type == "content_block_stop":
                    index = event.get("index", -1)
                    tool_state = pending_tool_uses.pop(index, None)
                    if tool_state:
                        tool_input = _parse_tool_input(tool_state["chunks"])
                        logger.info(
                            "tool_use: %s %s",
                            tool_state.get("name"),
                            json.dumps(tool_input, ensure_ascii=False),
                        )
                        yield _emit_event(
                            "tool_use",
                            {
                                "id": tool_state.get("id"),
                                "name": tool_state.get("name"),
                                "input": tool_input,
                            },
                        )

                continue

            # セッション開始時にモデルとツール一覧をログ出力
            if isinstance(message, SystemMessage) and message.subtype == "init":
                data = message.data
                logger.info("model: %s", data.get("model", "unknown"))
                logger.info("tools: %s", ", ".join(data.get("tools", [])))

            # Claudeの応答（テキスト・ツール呼び出し）
            if isinstance(message, AssistantMessage):
                if streamed_assistant_output:
                    continue

                for block in message.content:
                    if isinstance(block, TextBlock):
                        yield _emit_event("message", {"text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        logger.info("tool_use: %s %s", block.name, json.dumps(block.input))
                        yield _emit_event(
                            "tool_use",
                            {"id": block.id, "name": block.name, "input": block.input},
                        )

            # ツール実行結果
            if isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        content = _stringify_tool_result(block.content)
                        logger.info("tool_result: %s", (content or "")[:300])
                        yield _emit_event(
                            "tool_result",
                            {
                                "tool_use_id": block.tool_use_id,
                                "content": content,
                                "is_error": bool(block.is_error),
                            },
                        )

            # 完了メッセージ
            if isinstance(message, ResultMessage):
                logger.info(
                    "completed: turns=%d, cost=$%s, duration=%dms",
                    message.num_turns,
                    message.total_cost_usd,
                    message.duration_ms,
                )
                yield _emit_event(
                    "result",
                    {
                        "num_turns": message.num_turns,
                        "total_cost_usd": message.total_cost_usd,
                        "duration_ms": message.duration_ms,
                    },
                )

    except Exception:
        logger.exception("error during query")
        raise


if __name__ == "__main__":
    app.run()
