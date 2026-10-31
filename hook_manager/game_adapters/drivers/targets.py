# SPDX-License-Identifier: MPL-2.0
"""Identify driver signing by its native path and call signature."""

from ..native_methods import native_target
from .native_methods import DRIVER


def driver_target():
    return native_target(DRIVER, 'drivers.sign', 'sign_driver')


def rookie_check_target():
    return native_target(DRIVER, 'drivers.rookie-check', 'formula_rookie_contract_year_limit')
