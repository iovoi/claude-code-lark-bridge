import anyio
from .server import main

if __name__ == "__main__":
    anyio.run(main)
