:orphan:

Documentation Versions
======================

Current Version
---------------

**v0.1.0-beta.1** is the current EAI Simulator documentation release.

:doc:`Open the v0.1.0-beta.1 documentation <index_en>`

Version Authority
-----------------

This repository contains multiple components that can be released independently. A component's
version is authoritative in that component's own package metadata; Python package versions,
the documentation version, and asset-provider revisions are not required to be identical.
The current public release name is ``v0.1.0-beta.1``. Python package metadata uses the
PEP 440-equivalent form ``0.1.0b1``. The ``EAI_hmrs`` package version comes from ``__version__``
in ``source/EAI_hmrs/EAI_hmrs/__init__.py``, and ``source/EAI_hmrs/setup.py`` reuses that value.

Asset resolution defaults to the release revision ``v0.1.0-beta.1`` of ``HuangQIjun/eai-simulator-assets``.
Release and reproducible workflows should pin ``EAI_ASSETS_HF_REVISION`` to an explicit immutable
tag or commit. That provider revision identifies the external asset set; it does not need to share
a name with a source tag or any component package version.

This page will provide links to previous versions after future releases. Unreleased versions and inactive links are not listed.
