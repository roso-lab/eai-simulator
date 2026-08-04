# 扩展
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
    'sphinx.ext.intersphinx',
    'myst_parser',
    'sphinx_autodoc_typehints',
]

# 主题配置
html_theme = 'furo'
templates_path = ["_templates"]

html_context = {
    "eai_repository_url": "https://github.com/roso-lab/eai-simulator",
    # Cusdis 评论组件配置(社区页论坛)。启用步骤:
    # 1) 在 https://cusdis.com 注册并创建一个站点/app,复制 app_id 填入;
    # 2) app_id 为空时社区页不渲染评论区(避免控制台报错);
    # 3) 若需自托管,把 host 改成自托管地址并在 Cusdis 侧配置对应 host。
    "eai_cusdis": {
        "host": "https://cusdis.com",
        "app_id": "671109d6-0f53-405d-9cd7-cf23316878ce",  # cusdis.com 站点 app id
    },
}

html_sidebars = {
    "**": [
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ]
}

# 静态资源与原样发布页面（相对于 source 目录）
html_logo = "_static/img/logo.png"
html_favicon = "_static/img/Icon.png"
html_static_path = ["_static"]
html_extra_path = ["_extra", "../architecture"]
html_css_files = ["custom.css"]
html_js_files = [("custom.js", {"type": "module"})]

# Furo 主题选项
html_theme_options = {
    "announcement": "eai-product-header",
    "sidebar_hide_name": True,
    "navigation_with_keys": True,
    "top_of_page_buttons": [],
}

# 路径配置
import sys
import os
sys.path.insert(0, os.path.abspath('../source'))

# 项目信息
project = 'EAI Simulator'
copyright = '2026, Rosolab'
author = 'Haixu Zhang'
release = '0.1.0'
html_title = 'EAI Simulator Docs'

# 语言
language = 'zh_CN'  # 或 'en'

# 源文件扩展
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'myst',
}

# 自动文档配置
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'show-inheritance': True,
}

# Napoleon 配置（用于 Google/NumPy 风格文档字符串）
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
