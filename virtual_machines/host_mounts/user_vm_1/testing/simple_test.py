#!/usr/bin/env python3
"""Simple end-to-end connectivity and NAT check for user_vm_1."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


USER_VM_1_IP = "10.8.1.2"
USER_VM_2_IP = "10.8.1.3"
EXTERNAL_VM_IP = "10.8.0.2"
NAT_PUBLIC_IP = "10.8.0.1"

USER_VM_2_URL = f"http://{USER_VM_2_IP}:8080/test"
EXTERNAL_VM_URL = f"http://{EXTERNAL_VM_IP}:8080/test"
REQUEST_TIMEOUT_SEC = 5


@dataclass
class StepResult:
    name: str
    ok: bool
    message: str


def fetch_reflector_json(url: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method="GET")
    with opener.open(request, timeout=REQUEST_TIMEOUT_SEC) as response:
        status_code = response.getcode()
        body = response.read().decode("utf-8", errors="replace")

    if status_code != 200:
        raise RuntimeError(f"HTTP status {status_code}, expected 200")

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON in response: {exc}") from exc


def check_user_vm_2_connectivity() -> StepResult:
    step_name = "Шаг 1: user_vm_1 -> user_vm_2"
    print(f"\n[{step_name}]")
    print(f"URL: {USER_VM_2_URL}")

    try:
        payload = fetch_reflector_json(USER_VM_2_URL)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
        return StepResult(step_name, False, f"запрос неуспешен: {exc}")

    src_ip = payload.get("src_ip")
    dst_ip = payload.get("dst_ip")
    src_port = payload.get("src_port")
    dst_port = payload.get("dst_port")

    print(f"Ответ рефлектора: src={src_ip}:{src_port} -> dst={dst_ip}:{dst_port}")

    errors: list[str] = []
    if src_ip != USER_VM_1_IP:
        errors.append(f"ожидался src_ip={USER_VM_1_IP}, получено {src_ip}")
    if dst_ip != USER_VM_2_IP:
        errors.append(f"ожидался dst_ip={USER_VM_2_IP}, получено {dst_ip}")

    if errors:
        return StepResult(step_name, False, "; ".join(errors))

    return StepResult(step_name, True, "соединение есть, src/dst IP корректные")


def check_external_nat() -> StepResult:
    step_name = "Шаг 2: user_vm_1 -> external_vm (NAT)"
    print(f"\n[{step_name}]")
    print(f"URL: {EXTERNAL_VM_URL}")

    try:
        payload = fetch_reflector_json(EXTERNAL_VM_URL)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
        return StepResult(step_name, False, f"запрос неуспешен: {exc}")

    src_ip = payload.get("src_ip")
    dst_ip = payload.get("dst_ip")
    src_port = payload.get("src_port")
    dst_port = payload.get("dst_port")

    print(f"Ответ рефлектора: src={src_ip}:{src_port} -> dst={dst_ip}:{dst_port}")

    if dst_ip != EXTERNAL_VM_IP:
        return StepResult(
            step_name,
            False,
            f"некорректный dst_ip: ожидался {EXTERNAL_VM_IP}, получено {dst_ip}",
        )

    if src_ip == NAT_PUBLIC_IP:
        return StepResult(step_name, True, f"NAT работает (виден src_ip={NAT_PUBLIC_IP})")

    if src_ip == USER_VM_1_IP:
        return StepResult(step_name, False, f"NAT НЕ работает (виден src_ip={USER_VM_1_IP})")

    return StepResult(
        step_name,
        False,
        f"неожиданный src_ip={src_ip}; ожидался {NAT_PUBLIC_IP} (работает) или {USER_VM_1_IP} (не работает)",
    )


def print_step_result(result: StepResult) -> None:
    status = "OK" if result.ok else "FAIL"
    print(f"Результат: {status} - {result.message}")


def main() -> int:
    print("=== Проверка тестовой схемы и NAT ===")
    print(f"Ожидаемый внутренний IP user_vm_1: {USER_VM_1_IP}")
    print(f"Ожидаемый NAT публичный IP: {NAT_PUBLIC_IP}")

    first = check_user_vm_2_connectivity()
    print_step_result(first)

    second = check_external_nat()
    print_step_result(second)

    print("\n=== Итог ===")
    if first.ok and second.ok:
        print("Система работает, NAT работает корректно.")
        return 0

    if not second.ok and "NAT НЕ работает" in second.message:
        print("Система частично работает, но NAT не работает.")
    else:
        print("Обнаружены проблемы в тестовой схеме или NAT.")

    return 1


if __name__ == "__main__":
    sys.exit(main())
