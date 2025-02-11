import * as cdk from "aws-cdk-lib";
import { RemovalPolicy } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Construct } from "constructs";

export class DevSshEc2Stack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // DynamoDBテーブルの作成
    const chainlitTable = new dynamodb.Table(this, "ChainlitTable", {
      tableName: "ChainlitData",
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // UserThread GSIの追加
    chainlitTable.addGlobalSecondaryIndex({
      indexName: "UserThread",
      partitionKey: {
        name: "UserThreadPK",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: { name: "UserThreadSK", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.INCLUDE,
      nonKeyAttributes: ["id", "name"],
    });

    // 認証用テーブル
    const authTable = new dynamodb.Table(this, "AuthTable", {
      tableName: "UserAuth",
      partitionKey: { name: "username", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // VPCの作成
    const vpc = new ec2.Vpc(this, "VsCodeRemoteVpc", {
      maxAzs: 2,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC,
        },
      ],
    });

    // キーペアの作成
    const keyPair = new ec2.KeyPair(this, "KeyPair", {
      type: ec2.KeyPairType.ED25519,
      format: ec2.KeyPairFormat.PEM,
    });

    // EC2用のIAMロールの作成
    const ec2Role = new iam.Role(this, "EC2Role", {
      assumedBy: new iam.ServicePrincipal("ec2.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonSSMManagedInstanceCore"),
      ],
    });

    // DynamoDB用の最小限の権限を付与
    const dynamoDbPolicy = new iam.Policy(this, "DynamoDBPolicy", {
      statements: [
        new iam.PolicyStatement({
          actions: [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
            "dynamodb:DeleteItem",
            "dynamodb:Query",
            "dynamodb:Scan"
          ],
          resources: [
            chainlitTable.tableArn,
            authTable.tableArn,
            `${chainlitTable.tableArn}/index/*`, // GSIへのアクセスを許可
          ],
        }),
      ],
    });

    // Bedrock用の権限
    const bedrockPolicy = new iam.Policy(this, "BedrockPolicy", {
      statements: [
        new iam.PolicyStatement({
          actions: ["bedrock:*"],
          resources: ["*"],
        }),
      ],
    });

    // S3バケットの作成
    const bucket = new s3.Bucket(this, "ChainlitStorageBucket", {
      bucketName: `chainlit-storage-${this.account}-${this.region}`,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      versioned: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // S3用の最小限の権限を付与
    const s3Policy = new iam.Policy(this, "S3Policy", {
      statements: [
        new iam.PolicyStatement({
          actions: [
            "s3:PutObject",
            "s3:GetObject",
            "s3:DeleteObject",
            "s3:ListBucket",
          ],
          resources: [
            bucket.bucketArn,
            `${bucket.bucketArn}/*`,
          ],
        }),
      ],
    });

    // ポリシーをロールにアタッチ
    ec2Role.attachInlinePolicy(dynamoDbPolicy);
    ec2Role.attachInlinePolicy(bedrockPolicy);
    ec2Role.attachInlinePolicy(s3Policy);

    // セキュリティグループの作成
    const sg = new ec2.SecurityGroup(this, "VsCodeRemoteSG", {
      vpc,
      description: "Security group for VS Code Remote Development",
      allowAllOutbound: true,
    });

    // EC2インスタンスの作成
    const instance = new ec2.Instance(this, "VsCodeRemoteInstance", {
      vpc,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PUBLIC,
      },
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
      machineImage: new ec2.AmazonLinuxImage({
        generation: ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023,
      }),
      securityGroup: sg,
      role: ec2Role,
      keyPair,
    });

    // 出力
    new cdk.CfnOutput(this, "InstanceId", {
      value: instance.instanceId,
      description: "Instance ID for VS Code Remote connection",
    })
  }
}