#!/usr/bin/env bash
# SPDX-License-Identifier: MPL-2.0
# Shared read-only installation checks. No manifest content is evaluated as shell code.

load_linux_installation() {
    mod_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P) || return 1
    game_root=$(dirname -- "$mod_root")
    if [[ ${mod_root##*/} != modLauncher || ! -f "$game_root/TeamPrincipal.exe" || ! -f "$mod_root/bundled_packages.toml" ]]; then
        printf '%s\n' 'Extract the complete modLauncher folder directly beside TeamPrincipal.exe.' >&2
        return 1
    fi
    # Release manifests use a plain, quoted runtime directory directly under libs.
    # Accept that exact bounded shape and reject shell syntax, escapes and traversal.
    local in_runtime=0 manifest_line runtime_name
    runtime_dir=
    while IFS= read -r manifest_line || [[ -n $manifest_line ]]; do
        manifest_line=${manifest_line%$'\r'}
        if [[ $manifest_line =~ ^[[:space:]]*\[runtime\][[:space:]]*$ ]]; then
            in_runtime=1
        elif [[ $manifest_line =~ ^[[:space:]]*\[ ]]; then
            in_runtime=0
        elif (( in_runtime )) && [[ $manifest_line =~ ^directory[[:space:]]*=[[:space:]]*\"(libs/[^\"\\]+)\"[[:space:]]*$ ]]; then
            if [[ -n $runtime_dir ]]; then
                printf '%s\n' 'Duplicate runtime directory in the release manifest.' >&2
                return 1
            fi
            runtime_dir=${BASH_REMATCH[1]}
        fi
    done < "$mod_root/bundled_packages.toml"
    runtime_name=${runtime_dir#libs/}
    if [[ -z $runtime_dir || $runtime_name == */* || $runtime_name == . || $runtime_name == .. || ! -f "$mod_root/$runtime_dir/python.exe" ]]; then
        printf '%s\n' 'The bundled Windows Python is missing or its manifest path is invalid. Extract the complete release again.' >&2
        return 1
    fi
    python_exe="$mod_root/$runtime_dir/python.exe"
}

quote_steam_path() {
    local quoted=${1//\'/\'\\\'\'}
    printf "'%s'" "$quoted"
}
