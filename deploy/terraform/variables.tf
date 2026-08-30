variable "kubeconfig" {
  description = "Path to the kubeconfig to use."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "Context within the kubeconfig."
  type        = string
  default     = "minikube"
}

variable "namespace" {
  description = "Namespace everything is created in."
  type        = string
  default     = "php-ai-bridge"
}

variable "image_tag" {
  description = "Tag shared by all locally built images."
  type        = string
  default     = "k8s"
}

variable "probe_profile" {
  description = <<-EOT
    Which liveness/readiness configuration to apply to the AI service.

      "naive"  -- the probe most services ship with: liveness on /health with a
                  one second timeout. /health is a synchronous FastAPI endpoint,
                  so it queues behind inference in the same threadpool.
      "honest" -- liveness on /alive, which is async and cannot queue behind
                  work, plus readiness on /ready, which actually checks the
                  model backend instead of always answering ok.

    The experiment runs the identical load against both.
  EOT
  type        = string
  default     = "naive"

  validation {
    condition     = contains(["naive", "honest"], var.probe_profile)
    error_message = "probe_profile must be \"naive\" or \"honest\"."
  }
}

variable "chat_latency_seconds" {
  description = "How long the stand-in model server takes per completion."
  type        = number
  default     = 20
}

variable "ai_service_replicas" {
  description = "Replicas of the AI service."
  type        = number
  default     = 1
}
