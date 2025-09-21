"""Entry point for running the unified bot as a module via python -m bots.unified"""

import asyncio

from bots.unified import main

if __name__ == "__main__":
    asyncio.run(main())
