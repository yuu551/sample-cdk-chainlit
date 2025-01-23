import * as cdk from "aws-cdk-lib";
import { RemovalPolicy, Token } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"; // インポートを追加
import * as s3 from "aws-cdk-lib/aws-s3";

export class DevSshEc2Stack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // DynamoDBテーブルの作成
    const table = new dynamodb.Table(this, "ChainlitTable", {
      tableName: "ChainlitData", // テーブル名を指定
      partitionKey: { name: "PK", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "SK", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY, // 開発環境用（本番環境では注意）
    });

    // UserThreadPKとUserThreadSKの属性定義を追加
    table.addGlobalSecondaryIndex({
      indexName: "UserThread",
      partitionKey: {
        name: "UserThreadPK",
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: { name: "UserThreadSK", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.INCLUDE,
      nonKeyAttributes: ["id", "name"], // 追加の属性を指定
    });

    // 認証用テーブルの追加
    const authTable = new dynamodb.Table(this, "AuthTable", {
      tableName: "UserAuth",
      partitionKey: { name: "username", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // テーブルのARNを出力
    new cdk.CfnOutput(this, "TableName", {
      value: table.tableName,
      description: "DynamoDB Table Name",
    });

    new cdk.CfnOutput(this, "TableArn", {
      value: table.tableArn,
      description: "DynamoDB Table ARN",
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

    const keyPair = new ec2.KeyPair(this, "KeyPair", {
      type: ec2.KeyPairType.ED25519,
      format: ec2.KeyPairFormat.PEM,
    });

    const privateKey = keyPair.privateKey;

    // EC2用のIAMロールの作成
    const role = new iam.Role(this, "EC2Role", {
      assumedBy: new iam.ServicePrincipal("ec2.amazonaws.com"),
    });

    // SSM管理用のポリシーをアタッチ
    role.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonSSMManagedInstanceCore")
    );

    // Bedrock関連のポリシーを追加
    const bedrockPolicy = new iam.Policy(this, "BedrockPolicy", {
      statements: [
        new iam.PolicyStatement({
          actions: [
            "bedrock:*", // Bedrock関連のすべてのアクションを許可
          ],
          resources: ["*"], // リソースを制限しない（必要に応じて絞り込む）
        }),
      ],
    });

    // EC2インスタンスにDynamoDBアクセス権限を追加
    role.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonDynamoDBFullAccess")
    );

    // IAMロールにBedrockポリシーをアタッチ
    role.attachInlinePolicy(bedrockPolicy);

    // セキュリティグループの作成
    const sg = new ec2.SecurityGroup(this, "VsCodeRemoteSG", {
      vpc,
      description: "Security group for VS Code Remote Development",
      allowAllOutbound: true, // アウトバウンドは全て許可
    });

    // EC2インスタンスの作成
    const instance = new ec2.Instance(this, "VsCodeRemoteInstance", {
      vpc,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PUBLIC,
      },
      instanceType: ec2.InstanceType.of(
        ec2.InstanceClass.T3,
        ec2.InstanceSize.MEDIUM
      ),
      machineImage: new ec2.AmazonLinuxImage({
        generation: ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023,
      }),
      securityGroup: sg,
      role: role,
      keyPair,
    });

    // S3バケットの作成
    const bucket = new s3.Bucket(this, "ChainlitStorageBucket", {
      bucketName: `chainlit-storage-${this.account}-${this.region}`, // アカウントとリージョンを含めてユニークな名前を生成
      removalPolicy: RemovalPolicy.DESTROY, // 開発環境用（本番環境では注意）
      autoDeleteObjects: true, // スタック削除時にオブジェクトも削除（開発環境用）
      versioned: true, // バージョニングを有効化
      encryption: s3.BucketEncryption.S3_MANAGED, // S3マネージド暗号化を使用
    });

    // S3アクセス用のカスタムポリシーを作成
    const s3Policy = new iam.Policy(this, "S3AccessPolicy", {
      statements: [
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: [
            "s3:PutObject",
            "s3:GetObject",
            "s3:DeleteObject",
            "s3:ListBucket",
          ],
          resources: [
            bucket.bucketArn, // バケット自体へのアクセス
            `${bucket.bucketArn}/*`, // バケット内のすべてのオブジェクトへのアクセス
          ],
        }),
      ],
    });

    // EC2のIAMロールにS3ポリシーをアタッチ
    role.attachInlinePolicy(s3Policy);

    // 出力
    new cdk.CfnOutput(this, "InstanceId", {
      value: instance.instanceId,
      description: "Instance ID for VS Code Remote connection",
    });
    new cdk.CfnOutput(this, "BucketName", {
      value: bucket.bucketName,
      description: "S3 Bucket Name for Chainlit Storage",
    });
  }
}
