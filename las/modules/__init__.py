"""Feature modules (ADR-0002).

Each capability module under this package ships a ``manifest.py`` with a
``MODULE`` :class:`~las.shared.module_contract.ModuleManifest`. Discovery is
automatic (``las.app.registry.discover``); there is no central index to
maintain. Children register one level below their parent
(for example ``providers/mock/``).
"""
