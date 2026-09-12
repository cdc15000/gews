###############################################################################
# GEWS — AWS ECS Fargate deployment
#
# This configuration deploys:
#   - An ECS cluster with a long-running dashboard service behind an ALB
#   - A scheduled (EventBridge) Fargate task for the monitor pipeline
#   - EFS for shared persistent storage (data + output volumes)
#   - CloudWatch log group for container logs
#
# Prerequisites:
#   - An existing VPC with public and private subnets
#   - Earthdata credentials stored in AWS Secrets Manager
#   - (Optional) An ACM certificate for HTTPS
###############################################################################

terraform {
  required_version = ">= 1.3"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge({
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }, var.tags)
  }
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# -----------------------------------------------------------------------------
# CloudWatch Logs
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 30
}

# -----------------------------------------------------------------------------
# ECS Cluster
# -----------------------------------------------------------------------------
resource "aws_ecs_cluster" "this" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# -----------------------------------------------------------------------------
# IAM — task execution role (pulls images, writes logs, reads secrets)
# -----------------------------------------------------------------------------
resource "aws_iam_role" "ecs_execution" {
  name = "${local.name_prefix}-ecs-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "secrets_access" {
  name = "${local.name_prefix}-secrets"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.earthdata_user_arn, var.earthdata_pass_arn]
    }]
  })
}

# IAM — task role (what the container can do at runtime)
resource "aws_iam_role" "ecs_task" {
  name = "${local.name_prefix}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

# -----------------------------------------------------------------------------
# EFS — shared persistent storage for /app/data and /app/output
# -----------------------------------------------------------------------------
resource "aws_efs_file_system" "this" {
  creation_token = local.name_prefix
  encrypted      = true

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }
}

resource "aws_security_group" "efs" {
  name        = "${local.name_prefix}-efs"
  description = "Allow NFS from ECS tasks"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_efs_mount_target" "this" {
  for_each        = toset(var.private_subnet_ids)
  file_system_id  = aws_efs_file_system.this.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "data" {
  file_system_id = aws_efs_file_system.this.id
  posix_user {
    uid = 0
    gid = 0
  }
  root_directory {
    path = "/gews-data"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "0755"
    }
  }
}

resource "aws_efs_access_point" "output" {
  file_system_id = aws_efs_file_system.this.id
  posix_user {
    uid = 0
    gid = 0
  }
  root_directory {
    path = "/gews-output"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "0755"
    }
  }
}

# -----------------------------------------------------------------------------
# Security Groups
# -----------------------------------------------------------------------------
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Allow HTTP/HTTPS to ALB"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "ecs_tasks" {
  name        = "${local.name_prefix}-ecs-tasks"
  description = "Allow traffic from ALB to ECS tasks"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# -----------------------------------------------------------------------------
# ALB
# -----------------------------------------------------------------------------
resource "aws_lb" "this" {
  name               = local.name_prefix
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}

resource "aws_lb_target_group" "dashboard" {
  name        = "${local.name_prefix}-dash"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard.arn
  }
}

# -----------------------------------------------------------------------------
# Task Definitions
# -----------------------------------------------------------------------------

# Dashboard (long-running service)
resource "aws_ecs_task_definition" "dashboard" {
  family                   = "${local.name_prefix}-dashboard"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.dashboard_cpu
  memory                   = var.dashboard_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  volume {
    name = "gews-output"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.this.id
      transit_encryption = "ENABLED"
      authorization_configuration {
        access_point_id = aws_efs_access_point.output.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = "dashboard"
    image     = var.container_image
    essential = true
    command   = ["gews", "dashboard", "-c", "config/global_watch.yaml", "--port", "8080", "--data-dir", "/app/output"]

    portMappings = [{
      containerPort = 8080
      protocol      = "tcp"
    }]

    mountPoints = [{
      sourceVolume  = "gews-output"
      containerPath = "/app/output"
      readOnly      = true
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "dashboard"
      }
    }
  }])
}

# Monitor (scheduled task)
resource "aws_ecs_task_definition" "monitor" {
  family                   = "${local.name_prefix}-monitor"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.monitor_cpu
  memory                   = var.monitor_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  volume {
    name = "gews-data"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.this.id
      transit_encryption = "ENABLED"
      authorization_configuration {
        access_point_id = aws_efs_access_point.data.id
        iam             = "ENABLED"
      }
    }
  }

  volume {
    name = "gews-output"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.this.id
      transit_encryption = "ENABLED"
      authorization_configuration {
        access_point_id = aws_efs_access_point.output.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = "monitor"
    image     = var.container_image
    essential = true
    command   = ["gews", "monitor", "-c", "config/global_watch.yaml", "--check-now"]

    secrets = [
      { name = "EARTHDATA_USER", valueFrom = var.earthdata_user_arn },
      { name = "EARTHDATA_PASS", valueFrom = var.earthdata_pass_arn },
    ]

    mountPoints = [
      { sourceVolume = "gews-data",   containerPath = "/app/data",   readOnly = false },
      { sourceVolume = "gews-output", containerPath = "/app/output", readOnly = false },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "monitor"
      }
    }
  }])
}

# -----------------------------------------------------------------------------
# ECS Service — Dashboard
# -----------------------------------------------------------------------------
resource "aws_ecs_service" "dashboard" {
  name            = "${local.name_prefix}-dashboard"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.dashboard.arn
  desired_count   = var.dashboard_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets         = var.private_subnet_ids
    security_groups = [aws_security_group.ecs_tasks.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.dashboard.arn
    container_name   = "dashboard"
    container_port   = 8080
  }

  depends_on = [aws_lb_listener.http]
}

# -----------------------------------------------------------------------------
# EventBridge — Scheduled monitor runs
# -----------------------------------------------------------------------------
resource "aws_iam_role" "eventbridge_ecs" {
  name = "${local.name_prefix}-events-ecs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "eventbridge_run_task" {
  name = "${local.name_prefix}-run-task"
  role = aws_iam_role.eventbridge_ecs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = [aws_ecs_task_definition.monitor.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.ecs_task.arn]
      },
    ]
  })
}

resource "aws_cloudwatch_event_rule" "monitor_schedule" {
  name                = "${local.name_prefix}-monitor"
  description         = "Run GEWS monitor pipeline on a schedule"
  schedule_expression = var.monitor_schedule
}

resource "aws_cloudwatch_event_target" "monitor" {
  rule     = aws_cloudwatch_event_rule.monitor_schedule.name
  arn      = aws_ecs_cluster.this.arn
  role_arn = aws_iam_role.eventbridge_ecs.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.monitor.arn
    task_count          = 1
    launch_type         = "FARGATE"

    network_configuration {
      subnets         = var.private_subnet_ids
      security_groups = [aws_security_group.ecs_tasks.id]
    }
  }
}
