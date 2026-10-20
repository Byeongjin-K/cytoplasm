"""Retired external_effect_gate — tombstone kept only for v2.6.6-era nodes.

A node that last converged on v2.6.6 still has the retired Hermes drop-in, and its
home copy of pin_import_root.sh refuses to start the gateway (exit 78) unless this path
exists in the new release tree. The deploy-reconcile tick that moves such a node is the
old generation's code, so it restarts the gateway and rolls the release back on that
failure (issue #150). This file exists only so that check passes; the retired_jobs
"interop-plugin" cleanup removes the drop-in once the transition lands.

Nothing may import it: the Hermes external-effect evaluator is retired.
"""
raise ImportError("automation.interop.external_effect_gate is retired; see automation/interop/AGENTS.md")
