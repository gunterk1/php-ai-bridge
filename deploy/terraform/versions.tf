terraform {
  required_version = ">= 1.6"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.35"
    }
  }
}

# The cluster is not managed here. This configuration manages the workloads that
# run on whichever cluster the current kube context points at -- minikube in the
# repository's own runs. Splitting "who provisions the cluster" from "what runs
# on it" keeps this apply-able on a laptop; the same resources move to a managed
# cluster by changing the context, not the code.
provider "kubernetes" {
  config_path    = var.kubeconfig
  config_context = var.kube_context
}
