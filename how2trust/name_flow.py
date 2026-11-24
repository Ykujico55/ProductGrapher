"""Adapt Beniget use-definition chains to the source index's original AST."""

import ast

from ._vendor.beniget import DefUseChains, UseDefChains


class _Definitions(DefUseChains):
    def warn(self, msg, node):
        # Unresolved calls are reported by the caller with their source location.
        # Beniget's default warning stream would corrupt JSON CLI output.
        pass


def unique_name_origins(tree):
    definitions = _Definitions()
    definitions.visit(tree)
    return {use: origins[0].node for use, origins in UseDefChains(definitions).chains.items()
            if isinstance(use, ast.Name) and len(origins) == 1}
