"""Private copies of the runtime libraries distributed with How2Trust."""

from pathlib import Path

# Only submodules of this package can be loaded from the upstream source archive.
__path__.append(str(Path(__file__).with_name("runtime.zip")))
