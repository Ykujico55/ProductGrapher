# Bundled Python libraries

How2Trust includes these libraries for local, offline source analysis. Beniget and gast use the BSD 3-Clause License. Their original license files accompany the packages.

| Package | Official distribution | Wheel SHA-256 | License |
| --- | --- | --- | --- |
| Beniget 0.5.0 | [PyPI release files](https://pypi.org/project/beniget/0.5.0/#files) | `909efcd083af1ac4a5db311f57c415df8cb05c4307eda345edeafac3cd930043` | [LICENSE-beniget.txt](LICENSE-beniget.txt) |
| gast 0.7.0 | [PyPI release files](https://pypi.org/project/gast/0.7.0/#files) | `99cbf1365633a74099f69c59bd650476b96baa5ef196fec88032b00b31ba36f7` | [LICENSE-gast.txt](LICENSE-gast.txt) |

The Python 3 runtime sources come from `beniget-0.5.0-py3-none-any.whl` and `gast-0.7.0-py3-none-any.whl`. Files copied from the wheels were verified against their `RECORD` entries. The workbench loads the bundled sources from `runtime.zip` through the private package's `__path__`.

## Reproducing the archive offline

1. Verify the two wheels against the hashes above. Copy their `beniget/` and `gast/` Python sources, omitting `beniget/__main__.py` (CLI) and `gast/ast2.py` (Python 2). Copy each wheel's `.dist-info/licenses/LICENSE` to its package directory as `LICENSE.txt`.
2. In `beniget/__init__.py`, replace the two absolute `from beniget.` imports with `from .`. In `beniget/beniget.py`, `gast/ast3.py` and `gast/astn.py`, change the module-level `import gast` to `from .. import gast`. In `gast/ast3.py`, change `from gast.astn import` to `from .astn import`. In Beniget's `pkg()`, replace the literal `'gast.gast'` with `gast.gast.__name__` to recognize the bundled gast module. Preserve the upstream source bytes elsewhere.
3. Store the files under `beniget/` and `gast/` in lexicographic path order. Write only file entries with a DOS timestamp of 1980-01-01 00:00:00, Unix mode `0100644` and DEFLATE level 9. The bundled ZIP's SHA-256 is `e0966a06cc140f0a1b4ea7e851c3001abb70f4cfa34fb7a1eec38774272923fe`.

To inspect the bundled source or licenses, run `python -m zipfile -l how2trust/_vendor/runtime.zip`.

## PyCG call analysis

`pycg.zip` contains the analysis and JSON output modules of PyCG 2.0.0 from commit `79a0ee706c44f8e63721f69e1ee185aa27573418`. It includes the precomputed type stub data shipped at that commit, generated from typeshed `98ab14707e0d52deabd05161328f4cfce3869d34`. Both distributions use Apache License 2.0. [PyCG source](https://github.com/vitsalis/PyCG/tree/79a0ee706c44f8e63721f69e1ee185aa27573418) · [PyCG license](LICENSE-pycg.txt) · [typeshed license and notices](LICENSE-typeshed.txt)

The worker imports the engine and reads its JSON data directly from the ZIP. The bundled adaptation uses package resources for the data paths. On Python 3.10 and 3.11 it supplies private, nonmatching AST node classes for syntax absent from the host interpreter. On newer interpreters the same references resolve to the standard AST classes. The host's parser still determines which input syntax it can read.

To regenerate `pycg.zip`, download the [pinned source ZIP](https://codeload.github.com/vitsalis/PyCG/zip/79a0ee706c44f8e63721f69e1ee185aa27573418), then run `python how2trust/_vendor/build_pycg.py path/to/source.zip`. The script checks the source SHA-256, applies the local compatibility changes, copies the licenses and writes entries in deterministic order. The bundled ZIP's SHA-256 is `cf52057ac8a2a1f755b2c854b3313da4e0d6b5b09aeeae53f1722e0b122640c2`. [Rebuild script and exact changes](build_pycg.py)
