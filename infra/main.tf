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

variable "bot_image" {
  description = "Optional override for the verifier bot image tag"
  type        = string
  default     = null
}
variable "discord_token" {}
variable "coc_email" {}
variable "coc_password" {}
variable "clan_tag" {}
variable "feeder_clan_tag" { default = "" }
variable "verified_role_id" {}
variable "admin_log_channel_id" { default = "" }
variable "giveaway_bot_image" {
  description = "Optional override for the giveaway bot image tag"
  type        = string
  default     = null
}
variable "giveaway_discord_token" {}
variable "giveaway_channel_id" {}
variable "giveaway_table_name" { default = "coc-giveaways" }
variable "giveaway_test" {}
variable "subnets" { type = list(string) }
variable "tournament_bot_image" {
  description = "Optional override for the tournament bot image tag"
  type        = string
  default     = null
}
variable "unified_bot_image" {
  description = "Optional override for the unified bot image tag"
  type        = string
  default     = null
}
variable "tournament_discord_token" {}
variable "tournament_table_name" { default = "coc-tournaments" }
variable "tournament_registration_channel_id" { default = "" }
variable "vpc_id" {}

data "aws_ecs_task_definition" "bot_latest" {
  count           = var.bot_image == null ? 1 : 0
  task_definition = "coc-bot"
}

data "aws_ecs_task_definition" "giveaway_latest" {
  count           = var.giveaway_bot_image == null ? 1 : 0
  task_definition = "coc-giveaway-bot"
}

data "aws_ecs_task_definition" "tournament_latest" {
  count           = var.tournament_bot_image == null ? 1 : 0
  task_definition = "coc-tournament-bot"
}

data "aws_ecs_task_definition" "unified_latest" {
  count           = var.unified_bot_image == null ? 1 : 0
  task_definition = "coc-unified-bot"
}

locals {
  bot_image_effective = var.bot_image != null ? var.bot_image : try(
    jsondecode(data.aws_ecs_task_definition.bot_latest[0].container_definitions)[0].image,
    null
  )

  giveaway_image_effective = var.giveaway_bot_image != null ? var.giveaway_bot_image : try(
    jsondecode(data.aws_ecs_task_definition.giveaway_latest[0].container_definitions)[0].image,
    null
  )

  tournament_image_effective = var.tournament_bot_image != null ? var.tournament_bot_image : try(
    jsondecode(data.aws_ecs_task_definition.tournament_latest[0].container_definitions)[0].image,
    null
  )

  unified_image_effective = var.unified_bot_image != null ? var.unified_bot_image : try(
    jsondecode(data.aws_ecs_task_definition.unified_latest[0].container_definitions)[0].image,
    null
  )

  legacy_verifier_environment = {
    DISCORD_TOKEN        = var.discord_token
    COC_EMAIL            = var.coc_email
    COC_PASSWORD         = var.coc_password
    CLAN_TAG             = var.clan_tag
    FEEDER_CLAN_TAG      = var.feeder_clan_tag
    VERIFIED_ROLE_ID     = tostring(var.verified_role_id)
    ADMIN_LOG_CHANNEL_ID = var.admin_log_channel_id
    DDB_TABLE_NAME       = aws_dynamodb_table.verifications.name
    AWS_REGION           = var.aws_region
  }

  legacy_giveaway_environment = {
    DISCORD_TOKEN       = var.giveaway_discord_token
    GIVEAWAY_CHANNEL_ID = var.giveaway_channel_id
    GIVEAWAY_TABLE_NAME = var.giveaway_table_name
    DDB_TABLE_NAME      = aws_dynamodb_table.verifications.name
    COC_EMAIL           = var.coc_email
    COC_PASSWORD        = var.coc_password
    CLAN_TAG            = var.clan_tag
    FEEDER_CLAN_TAG     = var.feeder_clan_tag
    AWS_REGION          = var.aws_region
    GIVEAWAY_TEST       = var.giveaway_test
    USE_FAIRNESS_SYSTEM = "true"
  }

  legacy_tournament_environment = {
    DISCORD_TOKEN                      = var.tournament_discord_token
    COC_EMAIL                          = var.coc_email
    COC_PASSWORD                       = var.coc_password
    TOURNAMENT_TABLE_NAME              = var.tournament_table_name
    TOURNAMENT_REGISTRATION_CHANNEL_ID = var.tournament_registration_channel_id
    AWS_REGION                         = var.aws_region
  }

  unified_environment = {
    DISCORD_TOKEN                      = var.discord_token
    COC_EMAIL                          = var.coc_email
    COC_PASSWORD                       = var.coc_password
    CLAN_TAG                           = var.clan_tag
    FEEDER_CLAN_TAG                    = var.feeder_clan_tag
    VERIFIED_ROLE_ID                   = tostring(var.verified_role_id)
    ADMIN_LOG_CHANNEL_ID               = var.admin_log_channel_id
    DDB_TABLE_NAME                     = aws_dynamodb_table.verifications.name
    AWS_REGION                         = var.aws_region
    GIVEAWAY_CHANNEL_ID                = var.giveaway_channel_id
    GIVEAWAY_TABLE_NAME                = var.giveaway_table_name
    GIVEAWAY_TEST                      = var.giveaway_test
    USE_FAIRNESS_SYSTEM                = "true"
    TOURNAMENT_TABLE_NAME              = var.tournament_table_name
    TOURNAMENT_REGISTRATION_CHANNEL_ID = var.tournament_registration_channel_id
    SHADOW_MODE                        = "true"
    SHADOW_CHANNEL_ID                  = coalesce(var.admin_log_channel_id, "")
  }
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
      for arn in [
        module.legacy_verifier_service.log_group_arn,
        module.legacy_giveaway_service.log_group_arn,
        module.legacy_tournament_service.log_group_arn,
        module.unified_bot_service.log_group_arn,
      ] : "${arn}:*"
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

resource "aws_ecr_repository" "unified" {
  name = "coc-unified-bot"
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

module "legacy_verifier_service" {
  source = "./modules/bot_service"

  name               = "coc-bot"
  family             = "coc-bot"
  cluster_arn        = aws_ecs_cluster.bot.arn
  task_role_arn      = aws_iam_role.task.arn
  execution_role_arn = aws_iam_role.task.arn
  security_group_ids = [aws_security_group.bot.id]
  subnet_ids         = var.subnets
  container_image    = local.bot_image_effective
  environment        = local.legacy_verifier_environment
  log_group_name     = "/ecs/coc-verifier-bot"
  log_region         = var.aws_region
}

module "legacy_giveaway_service" {
  source = "./modules/bot_service"

  name               = "coc-giveaway-bot"
  family             = "coc-giveaway-bot"
  cluster_arn        = aws_ecs_cluster.bot.arn
  task_role_arn      = aws_iam_role.task.arn
  execution_role_arn = aws_iam_role.task.arn
  security_group_ids = [aws_security_group.bot.id]
  subnet_ids         = var.subnets
  container_image    = local.giveaway_image_effective
  environment        = local.legacy_giveaway_environment
  log_group_name     = "/ecs/coc-giveaway-bot"
  log_region         = var.aws_region
}

module "legacy_tournament_service" {
  source = "./modules/bot_service"

  name               = "coc-tournament-bot"
  family             = "coc-tournament-bot"
  cluster_arn        = aws_ecs_cluster.bot.arn
  task_role_arn      = aws_iam_role.task.arn
  execution_role_arn = aws_iam_role.task.arn
  security_group_ids = [aws_security_group.bot.id]
  subnet_ids         = var.subnets
  container_image    = local.tournament_image_effective
  environment        = local.legacy_tournament_environment
  log_group_name     = "/ecs/coc-tournament-bot"
  log_region         = var.aws_region
}

module "unified_bot_service" {
  source = "./modules/bot_service"

  name               = "coc-unified-bot"
  family             = "coc-unified-bot"
  cluster_arn        = aws_ecs_cluster.bot.arn
  task_role_arn      = aws_iam_role.task.arn
  execution_role_arn = aws_iam_role.task.arn
  security_group_ids = [aws_security_group.bot.id]
  subnet_ids         = var.subnets
  container_image    = local.unified_image_effective
  environment        = local.unified_environment
  log_group_name     = "/ecs/coc-unified-bot"
  log_region         = var.aws_region
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
