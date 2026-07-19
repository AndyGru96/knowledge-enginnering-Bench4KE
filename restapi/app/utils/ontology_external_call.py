from typing import Any, Dict

import requests


def call_external_ontology_service(
    payload: Dict[str, Any], external_url: str, timeout: float
) -> Dict[str, Any]:
    try:
        response = requests.post(external_url, json=payload, timeout=timeout)
    except Exception as exc:
        raise Exception(f"Error calling external ontology service: {exc}") from exc

    if response.status_code != 200:
        raise Exception(
            f"External ontology service error ({response.status_code}): {response.text}"
        )

    try:
        return response.json()
    except Exception as exc:
        raise Exception(
            f"Error reading response JSON from external ontology service: {exc}"
        ) from exc
