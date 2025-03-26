# Klayers :: Get ARN for Requests
data "klayers_package_latest_version" "requests" {
  name           = "requests"
  region         = var.aws_region
  python_version = var.python_version
}

# Klayers :: Get ARN for Pydantic
data "klayers_package_latest_version" "pydantic" {
  name           = "pydantic"
  region         = var.aws_region
  python_version = var.python_version
}

# Lambda :: Upload xmltodict Layer (if change detected)
resource "aws_lambda_layer_version" "xmltodict" {
  layer_name  = "xmltodict"
  description = "xmltodict is a Python package for converting between XML and JSON"

  compatible_runtimes      = ["python${var.python_version}"]
  compatible_architectures = ["x86_64", "arm64"]

  filename         = var.xmltodict_layer
  source_code_hash = filebase64sha256(var.xmltodict_layer)

  skip_destroy = true
}

# EventBridge :: Create Lambda Trigger
resource "aws_cloudwatch_event_rule" "cron" {
  name        = "PropSync-LambdaTrigger"
  description = "Triggers a Lambda Function every ${var.trigger_frequency} mins"

  schedule_expression = "rate(${var.trigger_frequency} minutes)"
}

# IAM :: Create Lambda Execution Policy
resource "aws_iam_policy" "lambda" {
  name        = "PropSync-PolicyForLambdaFunction"
  path        = "/"
  description = "Allows the PropSync Lambda to read the Webflow secret, and log to CloudWatch"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadWebflowSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.webflow_secret]
      },
      {
        Sid    = "CloudWatchLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = ["*"]
      },
    ]
  })
}

# IAM :: Create Lambda Execution Role
resource "aws_iam_role" "lambda" {
  name        = "PropSync-RoleForLambdaFunction"
  path        = "/"
  description = "Allows the PropSync Lambda to read the Webflow secret, and log to CloudWatch"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# IAM :: Create Role-Policy Attachment
resource "aws_iam_role_policy_attachment" "lambda" {
  policy_arn = aws_iam_policy.lambda.arn
  role       = aws_iam_role.lambda.name
}

# Zip :: Codebase for Lambda Function
data "archive_file" "lambda" {
  type        = "zip"
  source_file = var.lambda_code_path
  output_path = "lambda.zip"
}

# Lambda :: Create Function
# -> Env Vars: XML Endpoint, Name of Webflow Secret, Name of DynamoDB Table
resource "aws_lambda_function" "lambda" {
  function_name = "PropSync-LambdaFunction"
  description   = "Polls Kendal XML feed and updates Webflow CMS"
  role          = aws_iam_role.lambda.arn

  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  handler = "lambda_function.lambda_handler"
  runtime = "python${var.python_version}"

  memory_size = 1024
  timeout     = 30

  layers = [
    data.klayers_package_latest_version.pydantic.arn,
    data.klayers_package_latest_version.requests.arn,
    aws_lambda_layer_version.xmltodict.arn
  ]

  environment {
    variables = {
      XML_ENDPOINT   = var.kendal_feed
      WEBFLOW_SECRET = var.webflow_secret
      WF_COLLECTION  = var.webflow_collection_id
    }
  }
}

# EventBridge :: Create Target
resource "aws_cloudwatch_event_target" "target" {
  target_id = "PropSync-LambdaTarget"
  arn       = aws_lambda_function.lambda.arn
  rule      = aws_cloudwatch_event_rule.cron.name
}

# Lambda :: Grant EventBridge Permission
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cron.arn
}