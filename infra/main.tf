terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
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
variable "coc_email" {}
variable "coc_password" {}
variable "clan_tag" {}
variable "feeder_clan_tag" { default = "" }
variable "verified_role_id" {}
variable "admin_log_channel_id" { default = "" }
variable "giveaway_bot_image" {}
variable "giveaway_discord_token" {}
variable "giveaway_channel_id" {}
variable "giveaway_table_name" { default = "coc-giveaways" }
variable "giveaway_test" {}
variable "subnets" { type = list(string) }
variable "tournament_bot_image" {}
variable "tournament_discord_token" {}
variable "tournament_table_name" { default = "coc-tournaments" }
variable "vpc_id" {}

resource "aws_cloudwatch_log_group" "bot" {
  name              = "/ecs/coc-verifier-bot"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "giveaway" {
  name              = "/ecs/coc-giveaway-bot"
  retention_in_days = 7
}


resource "aws_cloudwatch_log_group" "tournament" {
  name              = "/ecs/coc-tournament-bot"
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
resource "aws_dynamodb_table" "giveaways" {
  name         = var.giveaway_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "giveaway_id"
  range_key    = "user_id"
  attribute {
    name = "giveaway_id"
    type = "S"
  }
  attribute {
    name = "user_id"
    type = "S"
  }
}


resource "aws_dynamodb_table" "tournaments" {
  name         = var.tournament_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
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
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:Scan",
      "dynamodb:UpdateItem",
      "dynamodb:Query"
    ]
    resources = [aws_dynamodb_table.verifications.arn, aws_dynamodb_table.giveaways.arn, aws_dynamodb_table.tournaments.arn]
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
      "${aws_cloudwatch_log_group.giveaway.arn}:*",
      "${aws_cloudwatch_log_group.tournament.arn}:*"
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


resource "aws_ecr_repository" "giveaway" {
  name = "coc-giveaway-bot"
}

resource "aws_ecr_repository" "tournament" {
  name = "coc-tournament-bot"
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
        { name = "FEEDER_CLAN_TAG", value = var.feeder_clan_tag },
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

resource "aws_ecs_task_definition" "giveaway_bot" {
  family                   = "coc-giveaway-bot"
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
      name      = "giveaway"
      image     = var.giveaway_bot_image
      essential = true
      environment = [
        { name = "DISCORD_TOKEN", value = var.giveaway_discord_token },
        { name = "GIVEAWAY_CHANNEL_ID", value = var.giveaway_channel_id },
        { name = "GIVEAWAY_TABLE_NAME", value = var.giveaway_table_name },
        { name = "DDB_TABLE_NAME", value = aws_dynamodb_table.verifications.name },
        { name = "COC_EMAIL", value = var.coc_email },
        { name = "COC_PASSWORD", value = var.coc_password },
        { name = "CLAN_TAG", value = var.clan_tag },
        { name = "FEEDER_CLAN_TAG", value = var.feeder_clan_tag },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "GIVEAWAY_TEST", value = var.giveaway_test },
        { name = "USE_FAIRNESS_SYSTEM", value = "true" }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.giveaway.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "giveaway"
        }
      }
    }
  ])
}


resource "aws_ecs_task_definition" "tournament_bot" {
  family                   = "coc-tournament-bot"
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
      name      = "tournament"
      image     = var.tournament_bot_image
      essential = true
      environment = [
        { name = "DISCORD_TOKEN", value = var.tournament_discord_token },
        { name = "COC_EMAIL", value = var.coc_email },
        { name = "COC_PASSWORD", value = var.coc_password },
        { name = "TOURNAMENT_TABLE_NAME", value = var.tournament_table_name },
        { name = "AWS_REGION", value = var.aws_region }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.tournament.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "tournament"
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

  lifecycle {
    ignore_changes = [task_definition]
  }
}

resource "aws_ecs_service" "giveaway_bot" {
  name            = "coc-giveaway-bot"
  cluster         = aws_ecs_cluster.bot.id
  task_definition = aws_ecs_task_definition.giveaway_bot.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnets
    security_groups  = [aws_security_group.bot.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}

resource "aws_ecs_service" "tournament_bot" {
  name            = "coc-tournament-bot"
  cluster         = aws_ecs_cluster.bot.id
  task_definition = aws_ecs_task_definition.tournament_bot.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnets
    security_groups  = [aws_security_group.bot.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [task_definition]
  }
}

resource "random_id" "white_devel_cup_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "white_devel_cup" {
  bucket        = "white-devel-cup-${random_id.white_devel_cup_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_object" "white_devel_cup_html" {
  bucket       = aws_s3_bucket.white_devel_cup.id
  key          = "white-devel-cup.html"
  source       = "${path.module}/white-devel-cup.html"
  etag         = filemd5("${path.module}/white-devel-cup.html")
  content_type = "text/html"
}

resource "aws_s3_object" "white_devel_cup_robots" {
  bucket        = aws_s3_bucket.white_devel_cup.id
  key           = "robots.txt"
  source        = "${path.module}/robots.txt"
  etag          = filemd5("${path.module}/robots.txt")
  content_type  = "text/plain"
  cache_control = "public, max-age=86400"
}

resource "aws_s3_object" "white_devel_cup_sitemap" {
  bucket        = aws_s3_bucket.white_devel_cup.id
  key           = "sitemap.xml"
  source        = "${path.module}/sitemap.xml"
  etag          = filemd5("${path.module}/sitemap.xml")
  content_type  = "application/xml"
  cache_control = "public, max-age=86400"
}

resource "aws_s3_bucket_public_access_block" "white_devel_cup" {
  bucket = aws_s3_bucket.white_devel_cup.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "white_devel_cup" {
  bucket = aws_s3_bucket.white_devel_cup.id

  index_document {
    suffix = "white-devel-cup.html"
  }

  error_document {
    key = "white-devel-cup.html"
  }
}

data "aws_iam_policy_document" "white_devel_cup_public" {
  statement {
    sid = "AllowPublicRead"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.white_devel_cup.arn}/*"]
  }
}

resource "aws_s3_bucket_policy" "white_devel_cup" {
  bucket = aws_s3_bucket.white_devel_cup.id
  policy = data.aws_iam_policy_document.white_devel_cup_public.json

  depends_on = [aws_s3_bucket_public_access_block.white_devel_cup]
}

resource "aws_cloudfront_function" "white_devel_redirect" {
  name    = "white-devel-apex-redirect"
  runtime = "cloudfront-js-1.0"
  comment = "Redirect white-devel.com to tournaments subdomain"
  publish = true
  code    = file("${path.module}/white-devel-redirect.js")
}

resource "aws_cloudfront_distribution" "white_devel_cup" {
  enabled             = true
  comment             = "HTTPS distribution for white-devel-cup"
  default_root_object = "white-devel-cup.html"
  aliases = [
    local.tournaments_subdomain,
    local.white_devel_domain
  ]

  origin {
    domain_name = aws_s3_bucket_website_configuration.white_devel_cup.website_endpoint
    origin_id   = "white-devel-cup-s3"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "white-devel-cup-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.white_devel_redirect.arn
    }

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.white_devel.certificate_arn
    minimum_protocol_version = "TLSv1.2_2021"
    ssl_support_method       = "sni-only"
  }
}

output "white_devel_cup_http_endpoint" {
  description = "HTTP endpoint that serves white-devel-cup.html"
  value       = "http://${aws_s3_bucket_website_configuration.white_devel_cup.website_endpoint}"
}

output "white_devel_cup_https_endpoint" {
  description = "HTTPS endpoint backed by CloudFront"
  value       = "https://${local.tournaments_subdomain}"
}

output "white_devel_cup_cloudfront_domain" {
  description = "CloudFront distribution domain name"
  value       = aws_cloudfront_distribution.white_devel_cup.domain_name
}
