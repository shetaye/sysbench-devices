import logging

import pytest

from sysbench_devices.logging_config import configure_logging


def test_configure_logging_sets_root_level():
    configure_logging("DEBUG")

    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_rejects_invalid_level():
    with pytest.raises(ValueError, match="invalid log level"):
        configure_logging("NOPE")
