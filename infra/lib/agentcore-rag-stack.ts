import * as cdk from "aws-cdk-lib";
import * as cr from "aws-cdk-lib/custom-resources";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as agentcore from "@aws-cdk/aws-bedrock-agentcore-alpha";
import { Platform } from "aws-cdk-lib/aws-ecr-assets";
import { ContainerImageBuild } from "@cdklabs/deploy-time-build";
import * as path from "path";

export class AgentcoreRagStack extends cdk.Stack {
  constructor(scope: cdk.App, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Fixed session ID shared between Lambda sync and user queries (must be >= 33 chars)
    const sessionId = "agentcore-rag-shared-session-00000001";
    const s3Prefix = "knowledge_base/";

    // ===== S3 Bucket (Knowledge Base source) =====
    const kbBucket = new s3.Bucket(this, "KnowledgeBaseBucket", {
      bucketName: `agentcore-rag-kb-${this.account}`,
      eventBridgeEnabled: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // ===== AgentCore Runtime =====
    const agentImage = new ContainerImageBuild(this, "AgentImage", {
      directory: path.join(__dirname, "..", "..", "agent"),
      platform: Platform.LINUX_ARM64,
      exclude: [".venv"],
    });

    const runtime = new agentcore.Runtime(this, "Runtime", {
      runtimeName: "agentcore_rag",
      agentRuntimeArtifact:
        agentcore.AgentRuntimeArtifact.fromEcrRepository(
          agentImage.repository,
          agentImage.imageTag,
        ),
      networkConfiguration:
        agentcore.RuntimeNetworkConfiguration.usingPublicNetwork(),
      environmentVariables: {
        SESSION_STORAGE_MOUNT: "/mnt/session",
        S3_BUCKET: kbBucket.bucketName,
      },
    });

    // Session Storage (not in CFn schema - configure via UpdateAgentRuntime API)
    // ref: https://dev.classmethod.jp/articles/bedrock-agentcore-runtime-session-storage/
    const containerUri = cdk.Fn.join(":", [
      agentImage.repository.repositoryUri,
      agentImage.imageTag,
    ]);

    new cr.AwsCustomResource(this, "EnableSessionStorage", {
      installLatestAwsSdk: true,
      onCreate: {
        service: "bedrock-agentcore-control",
        action: "UpdateAgentRuntime",
        parameters: {
          agentRuntimeId: runtime.agentRuntimeId,
          agentRuntimeArtifact: {
            containerConfiguration: { containerUri },
          },
          roleArn: runtime.role.roleArn,
          networkConfiguration: { networkMode: "PUBLIC" },
          filesystemConfigurations: [
            { sessionStorage: { mountPath: "/mnt/session" } },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.of(
          "session-storage-config",
        ),
      },
      onUpdate: {
        service: "bedrock-agentcore-control",
        action: "UpdateAgentRuntime",
        parameters: {
          agentRuntimeId: runtime.agentRuntimeId,
          agentRuntimeArtifact: {
            containerConfiguration: { containerUri },
          },
          roleArn: runtime.role.roleArn,
          networkConfiguration: { networkMode: "PUBLIC" },
          filesystemConfigurations: [
            { sessionStorage: { mountPath: "/mnt/session" } },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.of(
          "session-storage-config",
        ),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ["bedrock-agentcore:UpdateAgentRuntime"],
          resources: [runtime.agentRuntimeArn],
        }),
        new iam.PolicyStatement({
          actions: ["iam:PassRole"],
          resources: [runtime.role.roleArn],
        }),
      ]),
    });

    // IAM: Bedrock model invocation
    runtime.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ],
        resources: [
          "arn:aws:bedrock:*::foundation-model/*",
          "arn:aws:bedrock:*:*:inference-profile/*",
        ],
      }),
    );

    // IAM: S3 read (for aws s3 sync executed inside the Runtime microVM)
    kbBucket.grantRead(runtime);

    // ===== Lambda: S3 Sync Handler =====
    const syncLogGroup = new logs.LogGroup(this, "SyncHandlerLogGroup", {
      logGroupName: "/aws/lambda/agentcore-rag-sync",
      retention: logs.RetentionDays.TWO_WEEKS,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const syncFn = new lambda.Function(this, "SyncHandler", {
      functionName: "agentcore-rag-sync",
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: "sync_handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "lambda")),
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      logGroup: syncLogGroup,
      environment: {
        AGENT_RUNTIME_ARN: runtime.agentRuntimeArn,
        SESSION_ID: sessionId,
        S3_BUCKET: kbBucket.bucketName,
        S3_PREFIX: s3Prefix,
        KB_MOUNT_PATH: "/mnt/session/knowledge_base",
      },
    });

    // IAM: Lambda -> AgentCore
    syncFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bedrock-agentcore:InvokeAgentRuntime",
          "bedrock-agentcore:InvokeAgentRuntimeCommand",
        ],
        resources: [
          runtime.agentRuntimeArn,
          `${runtime.agentRuntimeArn}/*`,
        ],
      }),
    );

    // ===== EventBridge: S3 -> Lambda =====
    const eventLog = new logs.LogGroup(this, "EventLog", {
      logGroupName: "/agentcore-rag/s3-events",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const rule = new events.Rule(this, "S3SyncRule", {
      ruleName: "agentcore-rag-s3-sync",
      eventPattern: {
        source: ["aws.s3"],
        detailType: ["Object Created", "Object Deleted"],
        detail: {
          bucket: { name: [kbBucket.bucketName] },
          object: { key: [{ prefix: s3Prefix }] },
        },
      },
    });

    rule.addTarget(new targets.CloudWatchLogGroup(eventLog));
    rule.addTarget(
      new targets.LambdaFunction(syncFn, {
        retryAttempts: 2,
      }),
    );

    // ===== Outputs =====
    new cdk.CfnOutput(this, "BucketName", {
      value: kbBucket.bucketName,
    });
    new cdk.CfnOutput(this, "RuntimeArn", {
      value: runtime.agentRuntimeArn,
    });
    new cdk.CfnOutput(this, "RuntimeId", {
      value: runtime.agentRuntimeId,
    });
    new cdk.CfnOutput(this, "SessionId", {
      value: sessionId,
    });
  }
}
