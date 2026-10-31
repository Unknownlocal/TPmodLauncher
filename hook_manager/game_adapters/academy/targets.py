# SPDX-License-Identifier: MPL-2.0
"""Identify academy signing and renewal by native paths and signatures."""

from ..native_methods import native_target
from .native_methods import ACADEMY


def academy_targets():
    return (
        native_target(ACADEMY, 'academy.sign', 'sign_driver'),
        native_target(ACADEMY, 'academy.renew', 'renew_contract'),
    )
