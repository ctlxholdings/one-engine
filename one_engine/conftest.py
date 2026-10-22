"""pytest configuration for the one_engine package.

``cases/reference/`` holds the oracles exactly as their owners wrote them. Some
of them are test files themselves (esco-product's answer key,
``cases/reference/esco/tests/test_lod0_numbers.py``): they are data here, read
by :mod:`one_engine.oracles.esco`, and run against esco-product's own source
files, which are not vendored. pytest must not collect them as this repo's tests.
"""
collect_ignore = ["cases/reference"]
