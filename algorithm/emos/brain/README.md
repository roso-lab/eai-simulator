# Python Project

This Python project depends on several packages and uses the Deepseek Model.

## Features

- Command-line interface for easy execution.
- Integration with Deepseek Model for AI-powered functionality.
- Structured configuration and type validation with `pydantic`.
- Colorful terminal outputs using `colorama`.
- Automated docstring parsing with `docstring_parser`.

## Installation

Install the required dependencies:

```bash
pip install pydantic
pip install docstring_parser
pip install colorama
pip install openai
```



## Configuration

To use the Deepseek Model, create a .env file in the project root and add your Deepseek API key:

DEESEEK_API_KEY=your_api_key_here



## Testing

To verify the installation, run the following test scripts:

python test_base.py
python test_model.py
python test_discussion.py
