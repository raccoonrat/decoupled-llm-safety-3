"""Ring-1: runtime observability and latent-space probes (Theorem 2.2).

v0.1: `dcbf_evaluator` / `dcbf_monitor` — logprob-based h(x_t) proxy.
v0.2: `proxy_ensemble` / `dcbf_evaluator_v2` — independent geometric probes (Gap 1 fix).
"""

from . import dcbf_monitor
from . import proxy_ensemble
from . import dcbf_evaluator_v2

__all__: list[str] = ["dcbf_monitor", "proxy_ensemble", "dcbf_evaluator_v2"]
