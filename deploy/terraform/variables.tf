variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used as a prefix for all resources"
  type        = string
  default     = "gews"
}

variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
  default     = "prod"
}

# --- Container image ---

variable "container_image" {
  description = "Docker image URI for the GEWS application"
  type        = string
  default     = "ghcr.io/gews-project/gews:latest"
}

# --- Networking ---

variable "vpc_id" {
  description = "VPC ID to deploy into (must have at least two subnets)"
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for ECS tasks"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the ALB"
  type        = list(string)
}

# --- Dashboard task sizing ---

variable "dashboard_cpu" {
  description = "Fargate CPU units for the dashboard task (256 = 0.25 vCPU)"
  type        = number
  default     = 256
}

variable "dashboard_memory" {
  description = "Fargate memory (MiB) for the dashboard task"
  type        = number
  default     = 512
}

variable "dashboard_desired_count" {
  description = "Number of dashboard task replicas"
  type        = number
  default     = 2
}

# --- Monitor task sizing ---

variable "monitor_cpu" {
  description = "Fargate CPU units for the monitor task (1024 = 1 vCPU)"
  type        = number
  default     = 1024
}

variable "monitor_memory" {
  description = "Fargate memory (MiB) for the monitor task"
  type        = number
  default     = 4096
}

variable "monitor_schedule" {
  description = "CloudWatch Events schedule expression for the monitor CronJob"
  type        = string
  default     = "rate(6 hours)"
}

# --- Secrets ---

variable "earthdata_user_arn" {
  description = "ARN of the Secrets Manager secret holding the Earthdata username"
  type        = string
}

variable "earthdata_pass_arn" {
  description = "ARN of the Secrets Manager secret holding the Earthdata password"
  type        = string
}

# --- DNS / TLS (optional) ---

variable "certificate_arn" {
  description = "ACM certificate ARN for HTTPS on the ALB (leave empty to disable)"
  type        = string
  default     = ""
}

variable "domain_name" {
  description = "Domain name for the dashboard (leave empty to skip Route 53)"
  type        = string
  default     = ""
}

# --- Tags ---

variable "tags" {
  description = "Additional tags to apply to all resources"
  type        = map(string)
  default     = {}
}
