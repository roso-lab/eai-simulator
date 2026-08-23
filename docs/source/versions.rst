:orphan:

文档版本
========

当前版本
--------

**v0.1.0** 是当前发布的 EAI Simulator 文档。

:doc:`打开 v0.1.0 文档 <index>`

版本权威
--------

仓库包含多个可独立发布的组件。组件版本以该组件自己的包元数据为准，
不要求所有 Python 包、文档版本和资源提供方修订号保持相同。
例如，``EAI_hmrs`` 的 Python 包版本由 ``source/EAI_hmrs/EAI_hmrs/__init__.py``
中的 ``__version__`` 提供，并由 ``source/EAI_hmrs/setup.py`` 复用同一值。

资源解析默认使用 ``HuangQIjun/eai-simulator-assets`` 的移动 ``main`` 修订。
发布或可复现运行需要固定 ``EAI_ASSETS_HF_REVISION`` 到明确的不可变标签或提交；
该资源提供方修订表示外部资源集合的来源，不表示必须与源码标签或任一组件包版本同名。

后续发布历史版本时，本页将提供对应的版本入口。这里不会列出尚未发布的版本或失效链接。
