output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.this.name
}

output "dashboard_service_name" {
  description = "Name of the dashboard ECS service"
  value       = aws_ecs_service.dashboard.name
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = aws_lb.this.dns_name
}

output "dashboard_url" {
  description = "URL for the GEWS dashboard"
  value       = var.certificate_arn != "" ? "https://${aws_lb.this.dns_name}" : "http://${aws_lb.this.dns_name}"
}

output "log_group_name" {
  description = "CloudWatch log group for GEWS containers"
  value       = aws_cloudwatch_log_group.this.name
}

output "efs_file_system_id" {
  description = "EFS file system ID used for persistent data"
  value       = aws_efs_file_system.this.id
}
