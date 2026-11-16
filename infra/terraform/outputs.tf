output "lake_bucket" {
  value = aws_s3_bucket.lake.bucket
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "ecr_repository_url" {
  value = aws_ecr_repository.pipeline.repository_url
}

output "emr_application_id" {
  value = aws_emrserverless_application.spark.id
}

output "emr_runtime_role_arn" {
  value = aws_iam_role.emr_runtime.arn
}

output "jenkins_policy_arn" {
  value = aws_iam_policy.jenkins.arn
}

output "image" {
  value = "${aws_ecr_repository.pipeline.repository_url}:${var.image_tag}"
}
