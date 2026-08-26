#!/usr/bin/env python3
"""Compatibility entry point for the Baochuang AI local demo."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "backend.main:app",
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", os.getenv("PORT", "8765"))),
        log_level=os.getenv("APP_LOG_LEVEL", "info"),
        reload=False,
    )


if __name__ == "__main__":
    main()
