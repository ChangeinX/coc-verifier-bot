locals {
  family         = var.family != "" ? var.family : var.name
  container_name = var.container_name != "" ? var.container_name : var.name
  environment = [
    for key in sort(keys(var.environment)) : {
      name  = key
      value = var.environment[key]
    }
  ]
}

resource "aws_cloudwatch_log_group" "this" {
  count             = var.create_log_group ? 1 : 0
  name              = var.log_group_name
  retention_in_days = var.log_group_retention_days
}

resource "aws_ecs_task_definition" "this" {
  family                   = local.family
  requires_compatibilities = [var.launch_type]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory

  runtime_platform {
    cpu_architecture        = var.platform_cpu_architecture
    operating_system_family = var.platform_os_family
  }

  execution_role_arn = var.execution_role_arn
  task_role_arn      = var.task_role_arn

  container_definitions = jsonencode([
    {
      name        = local.container_name
      image       = var.container_image
      essential   = true
      environment = local.environment
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = var.log_group_name
          awslogs-region        = var.log_region
          awslogs-stream-prefix = local.container_name
        }
      }
    }
  ])
}

resource "aws_ecs_service" "this" {
  name                   = var.name
  cluster                = var.cluster_arn
  task_definition        = aws_ecs_task_definition.this.arn
  desired_count          = var.desired_count
  launch_type            = var.launch_type
  enable_execute_command = var.enable_execute_command

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = var.assign_public_ip
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}
