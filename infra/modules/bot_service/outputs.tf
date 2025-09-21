output "service_name" {
  value       = aws_ecs_service.this.name
  description = "Name of the ECS service."
}

output "task_definition_arn" {
  value       = aws_ecs_task_definition.this.arn
  description = "ARN of the generated task definition."
}

output "log_group_name" {
  value       = var.log_group_name
  description = "CloudWatch Logs group name."
}

output "log_group_arn" {
  value       = var.create_log_group ? aws_cloudwatch_log_group.this[0].arn : null
  description = "CloudWatch Logs group ARN when managed by the module."
}
