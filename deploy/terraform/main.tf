resource "kubernetes_namespace" "this" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/part-of" = "php-ai-bridge"
    }
  }
}

locals {
  ns = kubernetes_namespace.this.metadata[0].name

  # Images are built straight into the node's container runtime, so there is no
  # registry in the loop and nothing to pull.
  pull_policy = "IfNotPresent"

  ai_service_url = "http://ai-service:8000"
}
