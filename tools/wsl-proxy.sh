#!/usr/bin/env bash
# Source this script so changes reach the current terminal and its child processes.

_pnpink_wsl_proxy() {
    local mode="${1:-status}" name value proxy bypass
    local -a names=(http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
                    all_proxy ALL_PROXY no_proxy NO_PROXY WSL_PAC_URL)

    case "$mode" in
        -h|--help|help)
            printf '%s\n' \
                'Usage (inside WSL):' \
                '  bash tools/wsl-proxy.sh status' \
                '  source tools/wsl-proxy.sh manual http://HOST:PORT [NO_PROXY]' \
                '  source tools/wsl-proxy.sh off' \
                '  source tools/wsl-proxy.sh auto' \
                '' \
                'Changes affect this terminal and programs started from it.' \
                'auto restores the environment captured before the first change in this shell.' \
                'A PAC URL is not a proxy address; this script does not evaluate PAC rules.' \
                'Existing programs, sudo/APT settings and Windows settings are not changed.'
            return 0
            ;;
        status)
            local found=0
            for name in "${names[@]}"; do
                if [[ -v "$name" ]]; then
                    value="${!name}"
                    # Avoid printing credentials embedded in proxy/PAC URLs.
                    if [[ "$value" == *://*@* ]]; then
                        value="${value%%://*}://***@${value##*@}"
                    fi
                    printf '%s=%s\n' "$name" "$value"
                    found=1
                fi
            done
            if (( ! found )); then
                printf '%s\n' 'No proxy environment variables configured.'
            fi
            return 0
            ;;
        manual)
            proxy="${2:-}"
            bypass="${3:-localhost,127.0.0.1,::1}"
            if [[ ! "$proxy" =~ ^https?://[^[:space:]/?#]+/?$ || "$bypass" == *$'\n'* || "$bypass" == *$'\r'* ]]; then
                printf '%s\n' 'Expected a proxy URL: http://HOST:PORT or https://HOST:PORT' >&2
                return 2
            fi
            ;;
        auto|off) ;;
        *)
            printf 'Unknown mode: %s (use --help)\n' "$mode" >&2
            return 2
            ;;
    esac

    if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
        printf '%s\n' 'Use source tools/wsl-proxy.sh ... to change this terminal.' >&2
        return 2
    fi

    if ! declare -p _PNPINK_WSL_PROXY_INITIAL_VALUES &>/dev/null; then
        declare -gA _PNPINK_WSL_PROXY_INITIAL_VALUES=()
        for name in "${names[@]}"; do
            if [[ -v "$name" ]]; then
                _PNPINK_WSL_PROXY_INITIAL_VALUES["$name"]="${!name}"
            fi
        done
    fi

    for name in "${names[@]}"; do
        unset "$name"
    done

    case "$mode" in
        manual)
            export http_proxy="$proxy" https_proxy="$proxy"
            export HTTP_PROXY="$proxy" HTTPS_PROXY="$proxy"
            export no_proxy="$bypass" NO_PROXY="$bypass"
            printf '%s\n' 'Manual proxy enabled for this terminal.'
            ;;
        off)
            printf '%s\n' 'Proxy environment variables cleared for this terminal.'
            ;;
        auto)
            for name in "${!_PNPINK_WSL_PROXY_INITIAL_VALUES[@]}"; do
                export "$name=${_PNPINK_WSL_PROXY_INITIAL_VALUES[$name]}"
            done
            printf '%s\n' 'Original WSL proxy environment restored.'
            if [[ -n "${WSL_PAC_URL:-}" ]]; then
                printf '%s\n' 'PAC restored; applications must support PAC to use its rules.'
            fi
            ;;
    esac
}

_pnpink_wsl_proxy "$@"
