# SPDX-License-Identifier: MPL-2.0
"""Identify academy signing and renewal by native paths and signatures."""

from ...callables import CallableTarget


def academy_targets():
    return (
        CallableTarget("academy.sign", "managers.academy_manager", ("AcademyManager",),
                       'sign_driver', '(self, team_name: str, driver_name: str, seasons: int = 1) -> Tuple[bool, str]'),
        CallableTarget("academy.renew", "managers.academy_manager", ("AcademyManager",),
                       'renew_contract', '(self, team_name: str, driver_name: str, extra_seasons: int) -> Tuple[bool, str]'),
    )
