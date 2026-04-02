import json
import os
from dataclasses import asdict

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import query, ClaudeAgentOptions

from config import KNOWLEDGE_BASE_DIR, SYSTEM_PROMPT, MAX_TURNS

app = BedrockAgentCoreApp()


@app.entrypoint
async def invocations(payload, context):
    prompt = payload.get("prompt", "")
    if not prompt:
        yield {"message": json.dumps({"error": "prompt is required"}, ensure_ascii=False)}
        return

    if not os.path.isdir(KNOWLEDGE_BASE_DIR):
        yield {"message": json.dumps({"error": "Knowledge base directory not found. Sync from S3 first."}, ensure_ascii=False)}
        return

    options = ClaudeAgentOptions(
        tools=["Read", "Grep", "Glob"],
        allowed_tools=["Read", "Grep", "Glob"],
        system_prompt=SYSTEM_PROMPT,
        permission_mode="bypassPermissions",
        max_turns=MAX_TURNS,
        cwd=KNOWLEDGE_BASE_DIR,
    )

    async for message in query(prompt=prompt, options=options):
        data = {"type": message.__class__.__name__, **asdict(message)}
        yield {"message": json.dumps(data, ensure_ascii=False)}


if __name__ == "__main__":
    app.run()
