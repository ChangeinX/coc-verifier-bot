variable "name" {
  description = "Friendly name used for resource prefixes."
  type        = string
}

variable "family" {
  description = "ECS task definition family name. Defaults to name when empty."
  type        = string
  default     = ""
}

variable "cluster_arn" {
  description = "ARN of the ECS cluster."
  type        = string
}

variable "task_role_arn" {
  description = "IAM role ARN for the task."
  type        = string
}

variable "execution_role_arn" {
  description = "IAM role ARN for task execution."
  type        = string
}

variable "security_group_ids" {
  description = "Security groups attached to the service ENIs."
  type        = list(string)
}

variable "subnet_ids" {
  description = "Subnets used for the service ENIs."
  type        = list(string)
}

variable "container_image" {
  description = "Container image to deploy."
  type        = string
}

variable "environment" {
  description = "Environment variables passed to the container."
  type        = map(string)
  default     = {}
}

variable "cpu" {
  description = "CPU units for the task definition."
  type        = string
  default     = "256"
}

variable "memory" {
  description = "Memory (MiB) for the task definition."
  type        = string
  default     = "512"
}

variable "assign_public_ip" {
  description = "Whether to assign a public IP to the task ENIs."
  type        = bool
  default     = true
}

variable "desired_count" {
  description = "Number of desired tasks."
  type        = number
  default     = 1
}

variable "log_group_name" {
  description = "CloudWatch Logs group name."
  type        = string
}

variable "log_group_retention_days" {
  description = "Log group retention period."
  type        = number
  default     = 7
}

variable "log_region" {
  description = "AWS region for log streaming."
  type        = string
}

variable "container_name" {
  description = "Optional container name override. Defaults to service name."
  type        = string
  default     = ""
}

variable "launch_type" {
  description = "Launch type for the ECS service."
  type        = string
  default     = "FARGATE"
}

variable "platform_cpu_architecture" {
  description = "CPU architecture for the task runtime platform."
  type        = string
  default     = "ARM64"
}

variable "platform_os_family" {
  description = "Operating system family for the task runtime platform."
  type        = string
  default     = "LINUX"
}

variable "enable_execute_command" {
  description = "Whether ECS Exec is enabled."
  type        = bool
  default     = false
}

variable "create_log_group" {
  description = "When true, manage the CloudWatch Logs group in this module."
  type        = bool
  default     = true
}
