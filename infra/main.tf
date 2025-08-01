terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  default = "us-east-1"
}

variable "ddb_table_name" {
  default = "coc-verifications"
}

variable "bot_image" {}
variable "discord_token" {}
variable "news_bot_image" {}
variable "news_discord_token" {}
variable "news_channel_id" {}
variable "openai_api_key" {}
variable "coc_email" {}
variable "coc_password" {}
variable "clan_tag" {}
variable "verified_role_id" {}
variable "admin_log_channel_id" { default = "" }
variable "subnets" { type = list(string) }
variable "vpc_id" {}

resource "aws_cloudwatch_log_group" "bot" {
  name              = "/ecs/coc-verifier-bot"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "news" {
  name              = "/ecs/coc-news-bot"
  retention_in_days = 7
}

resource "aws_dynamodb_table" "verifications" {
  name         = var.ddb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "discord_id"

  attribute {
    name = "discord_id"
    type = "S"
  }
}

data "aws_iam_policy_document" "ecs_task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "coc-bot-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_assume.json
}

data "aws_iam_policy_document" "ddb_access" {
  statement {
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Scan", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.verifications.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.ddb_access.json
}

data "aws_iam_policy_document" "task_extra" {
  statement {
    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage"
    ]
    resources = ["*"]
  }

  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]
    resources = [
      "${aws_cloudwatch_log_group.bot.arn}:*",
      "${aws_cloudwatch_log_group.news.arn}:*"
    ]
  }
}

resource "aws_iam_role_policy" "task_extra" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task_extra.json
}

resource "aws_ecr_repository" "bot" {
  name = "coc-verifier-bot"
}

resource "aws_ecr_repository" "news" {
  name = "coc-news-bot"
}

resource "aws_ecs_cluster" "bot" {
  name = "coc-verifier-cluster"
}

resource "aws_security_group" "bot" {
  name   = "coc-bot-sg"
  vpc_id = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_task_definition" "bot" {
  family                   = "coc-bot"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  execution_role_arn = aws_iam_role.task.arn
  task_role_arn      = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "bot"
      image     = var.bot_image
      essential = true
      environment = [
        { name = "DISCORD_TOKEN", value = var.discord_token },
        { name = "COC_EMAIL", value = var.coc_email },
        { name = "COC_PASSWORD", value = var.coc_password },
        { name = "CLAN_TAG", value = var.clan_tag },
        { name = "VERIFIED_ROLE_ID", value = var.verified_role_id },
        { name = "ADMIN_LOG_CHANNEL_ID", value = var.admin_log_channel_id },
        { name = "DDB_TABLE_NAME", value = aws_dynamodb_table.verifications.name },
        { name = "AWS_REGION", value = var.aws_region }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.bot.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "bot"
        }
      }
    }
  ])
}

resource "aws_ecs_task_definition" "news_bot" {
  family                   = "coc-news-bot"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  execution_role_arn = aws_iam_role.task.arn
  task_role_arn      = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "news"
      image     = var.news_bot_image
      essential = true
      environment = [
        { name = "DISCORD_TOKEN", value = var.news_discord_token },
        { name = "NEWS_CHANNEL_ID", value = var.news_channel_id },
        { name = "OPENAI_API_KEY", value = var.openai_api_key },
        { name = "AWS_REGION", value = var.aws_region }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.news.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "news"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "bot" {
  name            = "coc-bot"
  cluster         = aws_ecs_cluster.bot.id
  task_definition = aws_ecs_task_definition.bot.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnets
    security_groups  = [aws_security_group.bot.id]
    assign_public_ip = true
  }
}

resource "aws_ecs_service" "news_bot" {
  name            = "coc-news-bot"
  cluster         = aws_ecs_cluster.bot.id
  task_definition = aws_ecs_task_definition.news_bot.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnets
    security_groups  = [aws_security_group.bot.id]
    assign_public_ip = true
  }
}
