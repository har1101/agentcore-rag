"""Example: minimal agent using Claude Agent SDK with built-in tools."""

import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage


async def run_search_agent(user_query: str, kb_path: str) -> str:
    """Run a search agent against a knowledge base directory.

    Args:
        user_query: The user's question.
        kb_path: Path to the knowledge base directory.

    Returns:
        The agent's response text.
    """
    options = ClaudeAgentOptions(
        model="us.anthropic.claude-sonnet-4-6-v1:0",
        tools=["Read", "Grep", "Glob"],
        allowed_tools=["Read", "Grep", "Glob"],
        system_prompt="Search the knowledge base and answer the question.",
        permission_mode="bypassPermissions",
        max_turns=10,
        cwd=kb_path,
    )

    result = ""
    async for message in query(prompt=user_query, options=options):
        if isinstance(message, ResultMessage):
            result = message.result or ""

    return result


if __name__ == "__main__":
    answer = asyncio.run(run_search_agent(
        user_query="What is Agentic Search?",
        kb_path="./knowledge_base",
    ))
    print(answer)
