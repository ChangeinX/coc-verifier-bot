// CI/CD role and GitHub OIDC provider for GitHub Actions deploys

variable "github_owner" {
  description = "GitHub organization or user that owns the repo"
  type        = string
  default     = "ChangeinX"
}

variable "github_repo" {
  description = "GitHub repository name (without owner)"
  type        = string
  default     = "coc-verifier-bot"
}

variable "github_branch" {
  description = "Git branch allowed to assume the role"
  type        = string
  default     = "main"
}

variable "deploy_role_name" {
  description = "IAM role name for CI/CD"
  type        = string
  default     = "coc-bot-deploy-role"
}

// GitHub OIDC provider for actions
resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = [
    "sts.amazonaws.com"
  ]

  // Thumbprint for DigiCert Global Root CA (GitHub OIDC)
  // See: https://docs.github.com/actions/deployment/security-hardening-your-deployments/configuring-openid-connect-in-amazon-web-services
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1"
  ]
}

// Trust policy for GitHub Actions OIDC
data "aws_iam_policy_document" "github_oidc_assume_role" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    // Allow only this repo and branch
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/*"
      ]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                  = var.deploy_role_name
  assume_role_policy    = data.aws_iam_policy_document.github_oidc_assume_role.json
  description           = "Role assumed by GitHub Actions to deploy ECS/ECR/Dynamo/Logs infra for CoC bots"
  force_detach_policies = true
}

// CI/CD permissions policy (scoped to services used by infra/main.tf)
data "aws_iam_policy_document" "deploy_permissions" {
  // ECR push/build + repo management
  statement {
    actions   = ["ecr:*"]
    resources = ["*"]
  }

  // ECS cluster/service/task definition management
  statement {
    actions   = ["ecs:*"]
    resources = ["*"]
  }

  // DynamoDB table management (verifications + giveaways)
  statement {
    actions   = ["dynamodb:*"]
    resources = ["*"]
  }

  // CloudWatch Logs groups and streams
  statement {
    actions   = ["logs:*"]
    resources = ["*"]
  }

  // EC2 security groups used by ECS services
  statement {
    actions = [
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeVpcs",
      "ec2:DescribeSubnets",
      "ec2:CreateTags",
      "ec2:DeleteTags"
    ]
    resources = ["*"]
  }

  // IAM to create task role and inline policies + PassRole to ECS
  statement {
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetRole",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "iam:PassRole"
    ]
    resources = [
      "*"
    ]
  }
}

resource "aws_iam_policy" "deploy" {
  name        = "coc-bot-deploy-policy"
  description = "Permissions for CI/CD to manage ECS/ECR/Dynamo/Logs and IAM for CoC bots"
  policy      = data.aws_iam_policy_document.deploy_permissions.json
}

resource "aws_iam_role_policy_attachment" "deploy_attach" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy.arn
}

output "deploy_role_arn" {
  description = "IAM Role ARN to set as AWS_ROLE secret in GitHub"
  value       = aws_iam_role.deploy.arn
}

output "github_oidc_provider_arn" {
  description = "OIDC provider ARN for GitHub Actions"
  value       = aws_iam_openid_connect_provider.github.arn
}
