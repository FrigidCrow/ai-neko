"""Frozen console entry point; imports do not create application data."""

from multiprocessing import freeze_support

from ai_neko.__main__ import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
