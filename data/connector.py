#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy import shim.

The original 797-line module was split into `data.sources/` in batch #31
(see Notes for development.md). All names below are re-exported so existing
imports continue to work unchanged:

    from data.connector import ExcelDataSource, ...     # still works
    from data.sources   import ExcelDataSource, ...     # preferred
"""
from data.sources import (
    DataSource,
    MAX_DISPLAY_ROWS,
    ExcelDataSource,
    CSVDataSource,
    SQLDataSource,
    GoogleSheetsDataSource,
    HTTPAPIDataSource,
)

__all__ = [
    "DataSource",
    "MAX_DISPLAY_ROWS",
    "ExcelDataSource",
    "CSVDataSource",
    "SQLDataSource",
    "GoogleSheetsDataSource",
    "HTTPAPIDataSource",
]
