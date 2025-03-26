# ----- REQUIRED VARIABLES -----

variable "webflow_secret" {
  type        = string
  description = "ARN of Secret in AWS Secrets Manager containing Webflow Site Token"
  # docs: https://developers.webflow.com/data/reference/site-token
}

variable "webflow_collection_id" {
  type        = string
  description = "ID of Collection in Webflow CMS"
  # docs: https://discourse.webflow.com/t/access-the-auto-generated-id-of-a-cms-item/104891
}

variable "kendal_feed" {
  type        = string
  description = "URL of Kendal XML Feed"
  # docs: https://kendal-ai.notion.site/XML-Feed-Documentation-for-Listings-13fa8cf7e41780d786aef6eec2357bc7
}

# ----- OPTIONAL VARIABLES -----

variable "aws_region" {
  type        = string
  description = "AWS Region for Deployment (e.g. us-east-1)"
  default     = "us-east-1"
  # note: klayers also supports me-south-1 (bahrain)
  #       which could be an option depending on where
  #       kendal is hosted
}

variable "python_version" {
  type        = string
  description = "Python Version for Lambda Runtime (e.g. 3.XX)"
  default     = "3.10"
}

variable "xmltodict_layer" {
  type        = string
  description = "Path to .zip file containing xmltodict Lambda Layer"
  default     = "../xmltodict_layer.zip"
}

variable "trigger_frequency" {
  type        = number
  description = "Frequency at which to trigger Lambda Function (in minutes)"
  default     = 5
}

variable "lambda_code_path" {
  type        = string
  description = "Path to `lambda_function.py`"
  default     = "../lambda_function.py"
}