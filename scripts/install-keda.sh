#!/usr/bin/env bash
# Install KEDA into its own namespace with Helm.
set -euo pipefail

KEDA_VERSION="${KEDA_VERSION:-2.15.1}"

helm repo add kedacore https://kedacore.github.io/charts >/dev/null
helm repo update kedacore >/dev/null
helm upgrade --install keda kedacore/keda \
  --namespace keda --create-namespace \
  --version "${KEDA_VERSION}" \
  --wait

kubectl -n keda rollout status deployment/keda-operator
kubectl get crd scaledobjects.keda.sh >/dev/null && echo "KEDA ${KEDA_VERSION} installed"
