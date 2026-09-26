"""M0 synthetic graph prototype for diagnostics and the frozen-package smoke gate.

⚠️ This is NOT the conversation graph. The real (and only) conversation
orchestration lives in ``ai_neko.chat.graph`` and is driven by
``ai_neko.runtime.SessionRuntime``. This package exists only for:

- ``ai-neko self-check`` (packaged smoke test) and ``ai-neko graph`` CLI,
- cross-process checkpoint durability/scope evidence in the Windows CI gate
  (scripts/package_smoke.py, tests/test_graph.py).

Do not add product features here; MVP2 work goes to ``ai_neko.chat.graph``.
Importing this module writes nothing.
"""

from .service import GraphService, GraphStateError, Scope, ScopeAccessError

__all__ = ["GraphService", "GraphStateError", "Scope", "ScopeAccessError"]
