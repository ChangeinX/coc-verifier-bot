locals {
  white_devel_domain    = "white-devel.com"
  tournaments_subdomain = "tournaments.white-devel.com"
}

data "aws_route53_zone" "white_devel" {
  name         = local.white_devel_domain
  private_zone = false
}

resource "aws_acm_certificate" "white_devel" {
  domain_name               = local.white_devel_domain
  subject_alternative_names = [local.tournaments_subdomain]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "white_devel_certificate_validation" {
  for_each = {
    for dvo in aws_acm_certificate.white_devel.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = data.aws_route53_zone.white_devel.zone_id
}

resource "aws_acm_certificate_validation" "white_devel" {
  certificate_arn         = aws_acm_certificate.white_devel.arn
  validation_record_fqdns = [for record in aws_route53_record.white_devel_certificate_validation : record.fqdn]
}

resource "aws_route53_record" "white_devel_apex_alias" {
  name    = data.aws_route53_zone.white_devel.name
  type    = "A"
  zone_id = data.aws_route53_zone.white_devel.zone_id

  alias {
    name                   = aws_cloudfront_distribution.white_devel_cup.domain_name
    zone_id                = aws_cloudfront_distribution.white_devel_cup.hosted_zone_id
    evaluate_target_health = false
  }

  depends_on = [aws_cloudfront_distribution.white_devel_cup]
}

resource "aws_route53_record" "white_devel_apex_alias_ipv6" {
  name    = data.aws_route53_zone.white_devel.name
  type    = "AAAA"
  zone_id = data.aws_route53_zone.white_devel.zone_id

  alias {
    name                   = aws_cloudfront_distribution.white_devel_cup.domain_name
    zone_id                = aws_cloudfront_distribution.white_devel_cup.hosted_zone_id
    evaluate_target_health = false
  }

  depends_on = [aws_cloudfront_distribution.white_devel_cup]
}

resource "aws_route53_record" "white_devel_tournaments_alias" {
  name    = "tournaments"
  type    = "A"
  zone_id = data.aws_route53_zone.white_devel.zone_id

  alias {
    name                   = aws_cloudfront_distribution.white_devel_cup.domain_name
    zone_id                = aws_cloudfront_distribution.white_devel_cup.hosted_zone_id
    evaluate_target_health = false
  }

  depends_on = [aws_cloudfront_distribution.white_devel_cup]
}

resource "aws_route53_record" "white_devel_tournaments_alias_ipv6" {
  name    = "tournaments"
  type    = "AAAA"
  zone_id = data.aws_route53_zone.white_devel.zone_id

  alias {
    name                   = aws_cloudfront_distribution.white_devel_cup.domain_name
    zone_id                = aws_cloudfront_distribution.white_devel_cup.hosted_zone_id
    evaluate_target_health = false
  }

  depends_on = [aws_cloudfront_distribution.white_devel_cup]
}

output "white_devel_route53_name_servers" {
  description = "Route53 name servers for white-devel.com (already in use if the domain was registered via AWS)"
  value       = data.aws_route53_zone.white_devel.name_servers
}
