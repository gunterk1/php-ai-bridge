# The AI service is the only workload here that is genuinely long-running: a
# completion takes seconds to tens of seconds, and FastAPI serves the service's
# synchronous endpoints from one bounded threadpool. Everything interesting in
# this deployment follows from those two facts.

locals {
  # The naive profile is not a strawman. It is the probe configuration in the
  # Kubernetes documentation's own example and in most Helm charts: liveness on
  # the service's existing health endpoint, one second to answer.
  probes = {
    naive = {
      liveness_path     = "/health"
      liveness_timeout  = 1
      readiness_path    = "/health"
      readiness_timeout = 1
    }
    honest = {
      # /alive is declared async, so Starlette runs it on the event loop rather
      # than in the threadpool. It cannot queue behind inference, which is the
      # entire point: a liveness probe must not share a resource with the work
      # it is supposed to be observing.
      liveness_path    = "/alive"
      liveness_timeout = 1
      # /ready asks the model backend whether it is actually reachable instead
      # of answering ok unconditionally. Readiness may legitimately fail while
      # the pod is saturated; that sheds traffic without killing in-flight work.
      readiness_path    = "/ready"
      readiness_timeout = 2
    }
  }

  probe = local.probes[var.probe_profile]
}

resource "kubernetes_deployment" "ai_service" {
  metadata {
    name      = "ai-service"
    namespace = local.ns
    labels    = { app = "ai-service" }
    annotations = {
      "php-ai-bridge/probe-profile" = var.probe_profile
    }
  }

  spec {
    replicas = var.ai_service_replicas
    selector { match_labels = { app = "ai-service" } }

    strategy {
      type = "RollingUpdate"
      rolling_update {
        max_surge       = 1
        max_unavailable = 0
      }
    }

    template {
      metadata {
        labels = { app = "ai-service" }
        annotations = {
          "php-ai-bridge/probe-profile" = var.probe_profile
        }
      }

      spec {
        # A completion in flight when the pod is told to stop should finish.
        # The default of 30s is shorter than a slow generation, so the default
        # truncates one request per pod on every single rollout.
        termination_grace_period_seconds = var.chat_latency_seconds + 15

        container {
          name              = "ai-service"
          image             = "ai-service:${var.image_tag}"
          image_pull_policy = local.pull_policy

          port { container_port = 8000 }

          env {
            name  = "AI_PROVIDER"
            value = "openai"
          }
          env {
            name  = "OPENAI_API_KEY"
            value = "stub-key-no-secret"
          }
          env {
            name  = "OPENAI_BASE_URL"
            value = "http://slow-provider:9000/v1"
          }
          env {
            name  = "CHAT_MODEL"
            value = "stub-chat"
          }
          env {
            name  = "EMBED_MODEL"
            value = "stub-embed"
          }

          liveness_probe {
            http_get {
              path = local.probe.liveness_path
              port = 8000
            }
            initial_delay_seconds = 5
            period_seconds        = 5
            timeout_seconds       = local.probe.liveness_timeout
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = local.probe.readiness_path
              port = 8000
            }
            initial_delay_seconds = 3
            period_seconds        = 5
            timeout_seconds       = local.probe.readiness_timeout
            failure_threshold     = 3
          }

          resources {
            requests = { cpu = "200m", memory = "512Mi" }
            limits   = { cpu = "2", memory = "2Gi" }
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "ai_service" {
  metadata {
    name      = "ai-service"
    namespace = local.ns
  }

  spec {
    selector = { app = "ai-service" }
    port {
      port        = 8000
      target_port = 8000
    }
  }
}
