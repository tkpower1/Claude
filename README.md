# Superpowers Toolkit

A CLI toolkit demonstrating Claude's superpowers - a collection of text analysis, code generation, data manipulation, and ASCII art utilities.

## Installation

```bash
pip install -e .
```

For development with test dependencies:
```bash
pip install -e ".[dev]"
```

## Usage

The toolkit provides four main command categories:

### Text Powers

Analyze and transform text:

```bash
# Analyze text statistics
superpowers text analyze "Hello world. How are you today?"

# Word frequency analysis
superpowers text frequency "the quick brown fox jumps over the lazy dog the" --top 5

# Reverse words
superpowers text reverse "hello world"
# Output: olleh dlrow

# Convert to leetspeak
superpowers text leet "elite hacker"
# Output: 31173 h4ck3r

# Caesar cipher encryption
superpowers text cipher "secret message" --shift 3

# Check if palindrome
superpowers text palindrome "A man a plan a canal Panama"
```

### Code Powers

Generate code skeletons:

```bash
# Generate a Python class
superpowers code class User --attrs "name,email,age" --methods "save,delete"

# Generate a JavaScript class
superpowers code class User --attrs "name,email" --lang javascript

# Generate a TypeScript class
superpowers code class User --attrs "name,email" --lang typescript

# Generate a function skeleton
superpowers code function calculate --params "a,b,c"

# Count lines of code
superpowers code count myfile.py
```

### Data Powers

Manipulate and transform data:

```bash
# Flatten nested JSON
superpowers data flatten '{"user": {"name": "John", "address": {"city": "NYC"}}}'

# Unflatten JSON
superpowers data unflatten '{"user.name": "John", "user.city": "NYC"}'

# Transform key case styles
superpowers data transform-keys '{"firstName": "John", "lastName": "Doe"}' --case snake_case

# Convert JSON to CSV
superpowers data json-to-csv '[{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]'
```

### Art Powers

Generate ASCII art:

```bash
# Display pre-defined patterns
superpowers art pattern star
superpowers art pattern heart
superpowers art pattern rocket
superpowers art pattern lightning

# List available patterns
superpowers art list

# Create a text banner
superpowers art banner "Hello World" --char "*"

# Create a text box
superpowers art box "Important Message" --style double

# Generate big ASCII text
superpowers art big "HELLO"

# Generate a progress bar
superpowers art progress 75 100 --width 30
```

## Examples

### Text Analysis
```bash
$ superpowers text analyze "The quick brown fox jumps over the lazy dog."
{
  "character_count": 45,
  "word_count": 9,
  "sentence_count": 1,
  "average_word_length": 3.89,
  "unique_words": 9,
  "longest_word": "quick"
}
```

### Code Generation
```bash
$ superpowers code class Person --attrs "name,age" --methods "greet"
class Person:
    """A generated class."""

    def __init__(self, name, age):
        self.name = name
        self.age = age

    def greet(self):
        """TODO: Implement greet."""
        raise NotImplementedError
```

### ASCII Art
```bash
$ superpowers art big "HI"
H   H IIIII
H   H   I
HHHHH   I
H   H   I
H   H IIIII
```

## Running Tests

```bash
pytest
```

With coverage:
```bash
pytest --cov=superpowers
```

## Project Structure

```
.
├── pyproject.toml          # Project configuration
├── README.md               # This file
├── src/
│   └── superpowers/
│       ├── __init__.py     # Package exports
│       ├── cli.py          # Command-line interface
│       ├── text_powers.py  # Text utilities
│       ├── code_powers.py  # Code generation
│       ├── data_powers.py  # Data manipulation
│       └── art_powers.py   # ASCII art
└── tests/
    ├── test_text_powers.py
    ├── test_code_powers.py
    ├── test_data_powers.py
    ├── test_art_powers.py
    └── test_cli.py
```

## License

MIT License
