#!/usr/bin/env node
import * as cdk from "aws-cdk-lib/core";
import { AgentcoreRagStack } from "../lib/agentcore-rag-stack";

const app = new cdk.App();
new AgentcoreRagStack(app, "AgentcoreRagStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? "ap-northeast-1",
  },
});
