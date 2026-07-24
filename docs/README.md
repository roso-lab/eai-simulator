# Documentation

The project documentation is built with Sphinx and MyST Markdown.

## Layout

```text
docs/
├── Makefile
├── make.bat
├── requirements.txt
└── source/
    ├── conf.py
    ├── index.rst
    ├── *.md
    ├── assets/         # Images embedded in documentation pages
    ├── _static/        # Theme assets copied by Sphinx
    └── _extra/         # Files copied unchanged to the HTML root
```

`source/_extra/env_diy_tutorial.html` is published as
`env_diy_tutorial.html`. Its local assets are stored beside it under
`source/_extra/env-diy-assets/`.

## Local build

```bash
python -m pip install -r docs/requirements.txt
cd docs
make clean
make html
```

The generated site is written to `docs/build/html/`. Build output must not be
committed.

Preview it locally with:

```bash
python -m http.server 8000 --directory docs/build/html
```

## Deployment

The repository workflow builds the documentation from source and publishes
`docs/build/html`. Contributors should commit only source documents, static
assets, and build configuration.
