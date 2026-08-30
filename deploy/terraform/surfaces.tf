# The three product surfaces. Two of them are stateless proxies and deploy the
# boring way. The third is not, and Kubernetes will not warn you about it.

locals {
  stateless_surfaces = {
    php-app = {
      port = 8080
      env  = { AI_SERVICE_URL = local.ai_service_url }
    }
    node-app = {
      port = 8081
      env  = { AI_SERVICE_URL = local.ai_service_url, PORT = "8081" }
    }
  }
}

resource "kubernetes_deployment" "surface" {
  for_each = local.stateless_surfaces

  metadata {
    name      = each.key
    namespace = local.ns
    labels    = { app = each.key }
  }

  spec {
    replicas = 2
    selector { match_labels = { app = each.key } }

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = 1
        max_unavailable = 0
      }
    }

    template {
      metadata { labels = { app = each.key } }

      spec {
        container {
          name              = each.key
          image             = "${each.key}:${var.image_tag}"
          image_pull_policy = local.pull_policy

          port { container_port = each.value.port }

          dynamic "env" {
            for_each = each.value.env
            content {
              name  = env.key
              value = env.value
            }
          }

          resources {
            requests = { cpu = "50m", memory = "128Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "surface" {
  for_each = local.stateless_surfaces

  metadata {
    name      = each.key
    namespace = local.ns
  }

  spec {
    selector = { app = each.key }
    port {
      port        = each.value.port
      target_port = each.value.port
    }
  }
}

# --- The one with state -----------------------------------------------------
#
# symfony-app keeps an audit trail in SQLite on a volume. That single fact rules
# out both defaults you would otherwise reach for:
#
#   replicas > 1     -- two processes writing one SQLite file is not a scaling
#                       strategy, it is a corruption strategy.
#   RollingUpdate    -- the default starts the new pod before stopping the old
#                       one. With a ReadWriteOnce volume the new pod cannot
#                       mount what the old pod still holds, so the rollout does
#                       not fail loudly; it hangs in ContainerCreating until
#                       someone notices.
#
# Recreate is the honest strategy here: it accepts a gap in availability rather
# than pretending the service is horizontally scalable. The gap is the cost of
# the audit trail, and it belongs in the open.

resource "kubernetes_persistent_volume_claim" "symfony_audit" {
  metadata {
    name      = "symfony-audit"
    namespace = local.ns
  }

  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = { storage = "1Gi" }
    }
  }

  wait_until_bound = false
}

resource "kubernetes_deployment" "symfony_app" {
  metadata {
    name      = "symfony-app"
    namespace = local.ns
    labels    = { app = "symfony-app" }
  }

  spec {
    replicas = 1
    selector { match_labels = { app = "symfony-app" } }

    strategy {
      type = "Recreate"
    }

    template {
      metadata { labels = { app = "symfony-app" } }

      spec {
        volume {
          name = "audit"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.symfony_audit.metadata[0].name
          }
        }

        container {
          name              = "symfony-app"
          image             = "symfony-app:${var.image_tag}"
          image_pull_policy = local.pull_policy

          port { container_port = 8082 }

          volume_mount {
            name       = "audit"
            mount_path = "/app/var"
          }

          env {
            name  = "AI_SERVICE_URL"
            value = local.ai_service_url
          }
          env {
            name  = "API_KEY"
            value = "dev-key"
          }
          env {
            name  = "APP_SECRET"
            value = "change-me-in-deployment"
          }
          env {
            name  = "DATABASE_URL"
            value = "sqlite:////app/var/audit.db"
          }

          resources {
            requests = { cpu = "50m", memory = "128Mi" }
            limits   = { cpu = "500m", memory = "512Mi" }
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "symfony_app" {
  metadata {
    name      = "symfony-app"
    namespace = local.ns
  }

  spec {
    selector = { app = "symfony-app" }
    port {
      port        = 8082
      target_port = 8082
    }
  }
}
