"""Host package: composition root, module registry, capability resolution.

The host imports everything (composition only) and owns application-wide
concerns: startup, module discovery, enable/disable policy and the CLI
adapter. Feature modules never import ``las.app`` (DEPENDENCY_RULES.md
rule 2).
"""
