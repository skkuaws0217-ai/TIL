"""
PaperHub — AWS CDK 인프라 배포
전체 아키텍처를 CDK로 정의합니다.

사용법:
    cd infra/
    pip install -r requirements.txt
    cdk bootstrap
    cdk deploy --all
"""

import os
from constructs import Construct
from aws_cdk import (
    App, Stack, Duration, RemovalPolicy, CfnOutput,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_lambda_python_alpha as lambda_python,
    aws_apigateway as apigw,
    aws_events as events,
    aws_events_targets as targets,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as sfn_tasks,
    aws_iam as iam,
    aws_ses as ses,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
)


# ══════════════════════════════════════════
# Storage Stack: DynamoDB + S3
# ══════════════════════════════════════════

class StorageStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ─── DynamoDB: 논문 메타데이터 ───
        self.papers_table = dynamodb.Table(
            self, "PapersTable",
            table_name="paperhub-papers",
            partition_key=dynamodb.Attribute(
                name="pmid", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # DOI 기반 조회용 GSI
        self.papers_table.add_global_secondary_index(
            index_name="doi-index",
            partition_key=dynamodb.Attribute(
                name="doi", type=dynamodb.AttributeType.STRING
            ),
        )

        # ─── DynamoDB: 키워드 알림 ───
        self.alerts_table = dynamodb.Table(
            self, "AlertsTable",
            table_name="paperhub-alerts",
            partition_key=dynamodb.Attribute(
                name="alert_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # 키워드 기반 조회용 GSI
        self.alerts_table.add_global_secondary_index(
            index_name="keyword-index",
            partition_key=dynamodb.Attribute(
                name="keyword", type=dynamodb.AttributeType.STRING
            ),
        )

        # ─── S3: PDF 캐시 저장소 ───
        self.pdf_bucket = s3.Bucket(
            self, "PdfBucket",
            bucket_name=f"paperhub-pdfs-{self.account}",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-old-pdfs",
                    expiration=Duration.days(90),
                    prefix="papers/",
                ),
            ],
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                ),
            ],
        )

        # ─── S3: 프론트엔드 호스팅 ───
        self.frontend_bucket = s3.Bucket(
            self, "FrontendBucket",
            bucket_name=f"paperhub-frontend-{self.account}",
            website_index_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                ignore_public_acls=False,
                block_public_policy=False,
                restrict_public_buckets=False,
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ─── Outputs ───
        CfnOutput(self, "PapersTableName", value=self.papers_table.table_name)
        CfnOutput(self, "AlertsTableName", value=self.alerts_table.table_name)
        CfnOutput(self, "PdfBucketName", value=self.pdf_bucket.bucket_name)


# ══════════════════════════════════════════
# Pipeline Stack: Lambda + Step Functions + EventBridge
# ══════════════════════════════════════════

class PipelineStack(Stack):
    def __init__(self, scope: Construct, id: str, storage: StorageStack, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ─── Lambda 공통 설정 ───
        common_env = {
            "PAPERS_TABLE": storage.papers_table.table_name,
            "ALERTS_TABLE": storage.alerts_table.table_name,
            "PDF_BUCKET": storage.pdf_bucket.bucket_name,
            "USE_BEDROCK": "true",
            "BEDROCK_MODEL_ID": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
        }

        # ─── Lambda: 논문 수집 ───
        collector_fn = _lambda.Function(
            self, "CollectorFn",
            function_name="paperhub-collector",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("../lambda/paper_collector"),
            timeout=Duration.minutes(5),
            memory_size=512,
            environment=common_env,
        )

        # ─── Lambda: 논문 요약 (Bedrock 호출) ───
        summarizer_fn = _lambda.Function(
            self, "SummarizerFn",
            function_name="paperhub-summarizer-fn",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("../lambda/paper_summarizer"),
            timeout=Duration.minutes(5),
            memory_size=1024,
            environment=common_env,
        )

        # ─── Lambda: 알림 메일 발송 ───
        alert_fn = _lambda.Function(
            self, "AlertFn",
            function_name="paperhub-alert-sender",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("../lambda/alert_sender"),
            timeout=Duration.minutes(2),
            memory_size=256,
            environment={
                **common_env,
                "SENDER_EMAIL": "alert@paperhub.io",
                "FRONTEND_URL": "https://paperhub.io",
            },
        )

        # ─── IAM 권한 부여 ───
        storage.papers_table.grant_read_write_data(collector_fn)
        storage.papers_table.grant_read_write_data(summarizer_fn)
        storage.alerts_table.grant_read_data(collector_fn)
        storage.alerts_table.grant_read_write_data(collector_fn)
        storage.alerts_table.grant_read_data(alert_fn)
        storage.pdf_bucket.grant_read_write(collector_fn)
        storage.pdf_bucket.grant_read(summarizer_fn)
        storage.pdf_bucket.grant_read(alert_fn)

        # Bedrock 호출 권한
        summarizer_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["*"],
        ))

        # SES 발송 권한
        alert_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=["*"],
        ))

        # Textract 권한 (PDF 텍스트 추출 대안)
        summarizer_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["textract:DetectDocumentText"],
            resources=["*"],
        ))

        # ─── Step Functions: 요약 → 메일 발송 워크플로우 ───
        summarize_task = sfn_tasks.LambdaInvoke(
            self, "SummarizeTask",
            lambda_function=summarizer_fn,
            output_path="$.Payload",
            result_path="$",
        )

        send_alert_task = sfn_tasks.LambdaInvoke(
            self, "SendAlertTask",
            lambda_function=alert_fn,
            output_path="$.Payload",
        )

        # 워크플로우 정의: 요약 → (키워드 있으면) 메일 발송
        has_keyword = sfn.Condition.is_present("$.alert_keyword")
        keyword_not_empty = sfn.Condition.not_(
            sfn.Condition.string_equals("$.alert_keyword", "")
        )

        definition = summarize_task.next(
            sfn.Choice(self, "HasAlertKeyword")
            .when(
                sfn.Condition.and_(has_keyword, keyword_not_empty),
                send_alert_task,
            )
            .otherwise(sfn.Succeed(self, "NoAlertNeeded"))
        )

        state_machine = sfn.StateMachine(
            self, "SummarizeWorkflow",
            state_machine_name="paperhub-summarize-pipeline",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(15),
        )

        # Lambda에 Step Functions 시작 권한
        state_machine.grant_start_execution(collector_fn)

        # Step Functions ARN을 수집 Lambda에 전달
        collector_fn.add_environment(
            "STATE_MACHINE_ARN", state_machine.state_machine_arn,
        )

        # ─── EventBridge: 주기적 수집 스케줄 ───

        # 매일 오전 9시 (KST) = UTC 00:00
        daily_rule = events.Rule(
            self, "DailyCollectionRule",
            rule_name="paperhub-daily-collection",
            schedule=events.Schedule.cron(minute="0", hour="0"),
            description="PaperHub 일간 논문 수집",
        )
        daily_rule.add_target(targets.LambdaFunction(
            collector_fn,
            event=events.RuleTargetInput.from_object({
                "source": "aws.events",
                "schedule_type": "daily",
            }),
        ))

        # 매주 월요일 오전 9시 (KST)
        weekly_rule = events.Rule(
            self, "WeeklyCollectionRule",
            rule_name="paperhub-weekly-collection",
            schedule=events.Schedule.cron(
                minute="0", hour="0", week_day="MON"
            ),
            description="PaperHub 주간 논문 수집",
        )
        weekly_rule.add_target(targets.LambdaFunction(
            collector_fn,
            event=events.RuleTargetInput.from_object({
                "source": "aws.events",
                "schedule_type": "weekly",
            }),
        ))

        # ─── 저장 ───
        self.collector_fn = collector_fn
        self.summarizer_fn = summarizer_fn
        self.alert_fn = alert_fn
        self.state_machine = state_machine

        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)


# ══════════════════════════════════════════
# API Stack: API Gateway + CloudFront
# ══════════════════════════════════════════

class ApiStack(Stack):
    def __init__(self, scope: Construct, id: str,
                 storage: StorageStack, pipeline: PipelineStack, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ─── Lambda: API 서빙 ───
        api_fn = _lambda.Function(
            self, "ApiFn",
            function_name="paperhub-api",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("../lambda/paper_collector"),
            timeout=Duration.minutes(2),
            memory_size=1024,
            environment={
                "PAPERS_TABLE": storage.papers_table.table_name,
                "ALERTS_TABLE": storage.alerts_table.table_name,
                "PDF_BUCKET": storage.pdf_bucket.bucket_name,
                "USE_BEDROCK": "true",
                "BEDROCK_MODEL_ID": "us.anthropic.claude-3-5-haiku-20241022-v1:0",
            },
        )

        storage.papers_table.grant_read_write_data(api_fn)
        storage.alerts_table.grant_read_write_data(api_fn)
        storage.pdf_bucket.grant_read(api_fn)

        # Bedrock 호출 권한 (온디맨드 요약)
        api_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["*"],
        ))

        # ─── API Gateway ───
        api = apigw.RestApi(
            self, "PaperHubApi",
            rest_api_name="PaperHub API",
            description="PaperHub 논문 검색 및 알림 API",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
            ),
        )

        # /papers - 논문 검색
        papers = api.root.add_resource("papers")
        papers_integration = apigw.LambdaIntegration(
            api_fn, timeout=Duration.seconds(29),
        )
        papers.add_method("POST", papers_integration)  # 검색
        papers.add_method("GET", papers_integration)    # 목록

        # /papers/{pmid} - 논문 상세
        paper_detail = papers.add_resource("{pmid}")
        paper_detail.add_method("GET", papers_integration)

        # /papers/{pmid}/pdf - PDF 다운로드 URL
        paper_pdf = paper_detail.add_resource("pdf")
        paper_pdf.add_method("GET", papers_integration)

        # /papers/{pmid}/summary - 요약 요청
        paper_summary = paper_detail.add_resource("summary")
        paper_summary.add_method("POST", papers_integration)

        # /papers/{pmid}/related - 관련 논문 추천
        paper_related = paper_detail.add_resource("related")
        paper_related.add_method("GET", papers_integration)

        # /alerts - 알림 관리
        alerts = api.root.add_resource("alerts")
        alerts_integration = apigw.LambdaIntegration(api_fn)
        alerts.add_method("GET", alerts_integration)     # 목록
        alerts.add_method("POST", alerts_integration)    # 등록

        alert_detail = alerts.add_resource("{alert_id}")
        alert_detail.add_method("PUT", alerts_integration)     # 수정
        alert_detail.add_method("DELETE", alerts_integration)  # 삭제

        # ─── CloudFront ───
        distribution = cloudfront.Distribution(
            self, "PaperHubCDN",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(storage.frontend_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.RestApiOrigin(api),
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                ),
            },
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_page_path="/index.html",
                    response_http_status=200,
                ),
            ],
        )

        CfnOutput(self, "ApiUrl", value=api.url)
        CfnOutput(self, "CloudFrontUrl", value=f"https://{distribution.distribution_domain_name}")


# ══════════════════════════════════════════
# App
# ══════════════════════════════════════════

app = App()

storage = StorageStack(app, "PaperHubStorage")
pipeline = PipelineStack(app, "PaperHubPipeline", storage=storage)
api = ApiStack(app, "PaperHubApi", storage=storage, pipeline=pipeline)

app.synth()
