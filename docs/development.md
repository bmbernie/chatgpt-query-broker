# Development

Create the development environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

Run the test suite:

```sh
.venv/bin/pytest -q
```

Check whitespace errors:

```sh
git diff --check
```

The package requires Python 3.13 or newer.
