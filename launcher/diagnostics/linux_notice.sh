#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Compatibility guard: native Linux desktop presentation is deferred.
# The Windows runtime's launcher console owns failure text and dismissal.
printf '%s\n' 'Native Linux presentation is unavailable; use the launcher console and printed log path.' >&2
exit 0
