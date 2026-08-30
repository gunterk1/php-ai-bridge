# A stand-in for an OpenAI-compatible model server with a fixed, configurable
# latency. The experiment measures Kubernetes' behaviour under slow inference;
# a real provider's jitter would only add noise to that.

resource "kubernetes_deployment" "slow_provider" {
  metadata {
    name      = "slow-provider"
    namespace = local.ns
    labels    = { app = "slow-provider" }
  }

  spec {
    replicas = 1
    selector { match_labels = { app = "slow-provider" } }

    template {
      metadata { labels = { app = "slow-provider" } }

      spec {
        container {
          name              = "slow-provider"
          image             = "slow-provider:${var.image_tag}"
          image_pull_policy = local.pull_policy

          port { container_port = 9000 }

          env {
            name  = "LATENCY_S"
            value = tostring(var.chat_latency_seconds)
          }

          resources {
            requests = { cpu = "50m", memory = "64Mi" }
            limits   = { cpu = "500m", memory = "256Mi" }
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "slow_provider" {
  metadata {
    name      = "slow-provider"
    namespace = local.ns
  }

  spec {
    selector = { app = "slow-provider" }
    port {
      port        = 9000
      target_port = 9000
    }
  }
}
