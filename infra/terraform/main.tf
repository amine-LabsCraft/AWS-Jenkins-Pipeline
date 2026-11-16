# AWS footprint of SunOps. Created and updated by Jenkins (stage "Terraform plan" + "Deploy").
#
#   S3 lake        bronze/ silver/ quarantine/ with lifecycle rules (cold data goes to Glacier)
#   S3 artifacts   versioned releases: Spark job, contract, dbt project, canary reports, DAGs
#   ECR            the sunops-pipeline image built and approved by Jenkins
#   EMR Serverless Spark application for the Bronze -> Silver job (scales to zero when idle)
#   IAM            runtime role for EMR + least-privilege policy for the Jenkins deployer

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
  # Remote state, recommended once more than one person deploys:
  # backend "s3" { bucket = "<state-bucket>" key = "sunops/terraform.tfstate" region = "eu-west-3" }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project     = "sunops"
      environment = var.environment
      managed_by  = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  prefix = "${var.project}-${var.environment}"
  suffix = substr(data.aws_caller_identity.current.account_id, -6, 6)
}

# ------------------------------------------------------------------ S3: data lake
resource "aws_s3_bucket" "lake" {
  bucket        = "${local.prefix}-lake-${local.suffix}"
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "bronze-to-cold-storage"
    status = "Enabled"
    filter { prefix = "bronze/" }
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 120
      storage_class = "GLACIER"
    }
  }

  rule {
    id     = "expire-quarantine"
    status = "Enabled"
    filter { prefix = "quarantine/" }
    expiration { days = var.quarantine_retention_days }
  }
}

# ------------------------------------------------------------------ S3: release artifacts
resource "aws_s3_bucket" "artifacts" {
  bucket        = "${local.prefix}-artifacts-${local.suffix}"
  force_destroy = var.environment != "prod"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ------------------------------------------------------------------ ECR
resource "aws_ecr_repository" "pipeline" {
  name                 = "${local.prefix}-pipeline"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "prod"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "pipeline" {
  repository = aws_ecr_repository.pipeline.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

# ------------------------------------------------------------------ EMR Serverless (Spark)
resource "aws_emrserverless_application" "spark" {
  name          = "${local.prefix}-spark"
  release_label = var.emr_release
  type          = "spark"

  maximum_capacity {
    cpu    = "16 vCPU"
    memory = "64 GB"
  }

  auto_start_configuration { enabled = true }
  auto_stop_configuration {
    enabled              = true
    idle_timeout_minutes = 10
  }
}

data "aws_iam_policy_document" "emr_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["emr-serverless.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "emr_runtime" {
  name               = "${local.prefix}-emr-runtime"
  assume_role_policy = data.aws_iam_policy_document.emr_assume.json
}

data "aws_iam_policy_document" "emr_runtime" {
  statement {
    sid     = "LakeReadWrite"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*",
    ]
  }
  statement {
    sid       = "ReadReleases"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
  }
}

resource "aws_iam_role_policy" "emr_runtime" {
  name   = "lake-access"
  role   = aws_iam_role.emr_runtime.id
  policy = data.aws_iam_policy_document.emr_runtime.json
}

# ------------------------------------------------------------------ Jenkins deployer
# Attach this policy to the IAM user/role whose keys are stored in Jenkins as "aws-sunops".
data "aws_iam_policy_document" "jenkins" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage",
    ]
    resources = [aws_ecr_repository.pipeline.arn]
  }
  statement {
    sid       = "PublishReleases"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }
  statement {
    sid       = "RunSparkJobs"
    actions   = ["emr-serverless:StartJobRun", "emr-serverless:GetJobRun", "emr-serverless:GetApplication"]
    resources = [aws_emrserverless_application.spark.arn, "${aws_emrserverless_application.spark.arn}/jobruns/*"]
  }
  statement {
    sid       = "PassRuntimeRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.emr_runtime.arn]
  }
}

resource "aws_iam_policy" "jenkins" {
  name   = "${local.prefix}-jenkins-deployer"
  policy = data.aws_iam_policy_document.jenkins.json
}
