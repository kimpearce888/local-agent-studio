"""Feature modules (ADR-0002).

Each capability module under this package ships a ``manifest.py`` with a
``MODULE`` :class:`~las.shared.module_contract.ModuleManifest`. Discovery is
automatic (``las.app.registry.discover``); there is no central index to
maintain. Children register below their parent at any depth
(the parent chain must simply be acyclic)
(for example ``providers/mock/``).
"""
