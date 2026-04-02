"""Lambda handler: S3 event → sync knowledge base to AgentCore Session Storage.

Trigger: EventBridge rule matching S3 PutObject / DeleteObject events.

Environment variables:
  AGENT_RUNTIME_ARN  - AgentCore Runtime ARN
  SESSION_ID         - Fixed session ID shared with user queries
  S3_BUCKET          - Source S3 bucket name
  S3_PREFIX          - Source S3 key prefix (e.g. "knowledge_base/")
  KB_MOUNT_PATH      - Session Storage path (default: /mnt/session/knowledge_base)
  AWS_REGION         - AWS region
"""

import json
import os
import sys
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
SESSION_ID = os.environ["SESSION_ID"]
S3_BUCKET = os.environ["S3_BUCKET"]
S3_PREFIX = os.environ.get("S3_PREFIX", "knowledge_base/")
KB_MOUNT_PATH = os.environ.get("KB_MOUNT_PATH", "/mnt/session/knowledge_base")
REGION = os.environ.get("AWS_REGION", "us-west-2")

client = boto3.client("bedrock-agentcore", region_name=REGION)


def handler(event, context):
    """Handle S3 event via EventBridge and sync files to Session Storage."""
    logger.info("Received event: %s", json.dumps(event))

    # Step 1: Ensure session is active by sending a lightweight agent invocation
    _ensure_session()

    # Step 2: Run s3 sync via InvokeAgentRuntimeCommand
    sync_command = (
        f'aws s3 sync "s3://{S3_BUCKET}/{S3_PREFIX}" "{KB_MOUNT_PATH}/" --delete'
    )
    exit_code = _run_command(sync_command, timeout=300)

    if exit_code != 0:
        logger.error("s3 sync failed with exit code %d", exit_code)
        raise RuntimeError(f"s3 sync failed with exit code {exit_code}")

    logger.info("Knowledge base synced successfully")
    return {"statusCode": 200, "body": "Sync completed"}


def _ensure_session():
    """Invoke agent runtime to ensure the session is active."""
    logger.info("Ensuring session %s is active...", SESSION_ID)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        runtimeSessionId=SESSION_ID,
        payload=json.dumps({"prompt": "ping"}).encode(),
    )
    # Consume the response stream to complete the invocation
    if "stream" in response:
        for _ in response["stream"]:
            pass
    elif "body" in response:
        response["body"].read()
    logger.info("Session %s is active", SESSION_ID)


def _run_command(command: str, timeout: int = 60) -> int:
    """Execute a shell command in the AgentCore session and return exit code."""
    logger.info("Running command: %s", command)

    response = client.invoke_agent_runtime_command(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        runtimeSessionId=SESSION_ID,
        qualifier="DEFAULT",
        contentType="application/json",
        accept="application/vnd.amazon.eventstream",
        body={"command": f'/bin/bash -c "{command}"', "timeout": timeout},
    )

    exit_code = -1
    for event in response.get("stream", []):
        if "chunk" in event:
            chunk = event["chunk"]
            if "contentDelta" in chunk:
                delta = chunk["contentDelta"]
                if delta.get("stdout"):
                    logger.info("stdout: %s", delta["stdout"].rstrip())
                if delta.get("stderr"):
                    logger.warning("stderr: %s", delta["stderr"].rstrip())
            if "contentStop" in chunk:
                exit_code = chunk["contentStop"].get("exitCode", -1)
                status = chunk["contentStop"].get("status", "UNKNOWN")
                logger.info("Command finished: exit_code=%d status=%s", exit_code, status)

    return exit_code
