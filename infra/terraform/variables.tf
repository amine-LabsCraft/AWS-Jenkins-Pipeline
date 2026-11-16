variable "project" {
  type    = string
  default = "sunops"
}

variable "environment" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "region" {
  type        = string
  default     = "eu-west-3"
  description = "Paris: the closest AWS region to Morocco."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Tag of the pipeline image pushed by Jenkins (the build number)."
}

variable "emr_release" {
  type    = string
  default = "emr-7.2.0"
}

variable "quarantine_retention_days" {
  type    = number
  default = 30
}
