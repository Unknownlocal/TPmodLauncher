# SPDX-License-Identifier: MPL-2.0
"""Identify driver signing by its native path and call signature."""

from ..native_methods import native_target
from .native_methods import DRIVER

# Developer inspection provenance only; never a runtime executable/body gate.
DRIVER_INSPECTED_SHA256 = 'e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773'


def driver_target():
    return native_target(DRIVER, 'drivers.sign', 'sign_driver')


def rookie_check_target():
    return native_target(DRIVER, 'drivers.rookie-check', 'formula_rookie_contract_year_limit')
