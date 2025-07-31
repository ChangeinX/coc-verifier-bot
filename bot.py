#!/usr/bin/env python3
"""Entrypoint for the CoC verifier bot."""
from cocbot.main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
