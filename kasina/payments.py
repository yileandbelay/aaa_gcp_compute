"""Payment provider integration (Chapa - Ethiopian payment gateway).

Chapa accepts telebirr, CBE Birr, M-Pesa, cards and bank transfers.
Docs: https://developer.chapa.co
"""
import requests
from flask import current_app


class PaymentError(Exception):
    pass


def chapa_enabled():
    return bool(current_app.config.get("CHAPA_SECRET_KEY"))


def _headers():
    return {"Authorization": f"Bearer {current_app.config['CHAPA_SECRET_KEY']}"}


def chapa_initialize(payment, user, return_url, callback_url):
    """Create a hosted checkout session; returns the checkout URL."""
    first, _, last = user.full_name.partition(" ")
    payload = {
        "amount": str(payment.amount),
        "currency": payment.currency,
        "email": user.email,
        "first_name": first,
        "last_name": last or first,
        "phone_number": user.phone or None,
        "tx_ref": payment.tx_ref,
        "callback_url": callback_url,
        "return_url": return_url,
        "customization[title]": "Kasina Cakery",
        "customization[description]": f"Course {payment.course_id}"[:50],
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    try:
        r = requests.post(
            f"{current_app.config['CHAPA_API_BASE']}/transaction/initialize",
            json=payload, headers=_headers(), timeout=20,
        )
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError(f"Could not reach payment provider: {exc}") from exc
    if r.status_code != 200 or data.get("status") != "success":
        raise PaymentError(data.get("message") or "Payment provider rejected the request.")
    return data["data"]["checkout_url"]


def chapa_verify(payment):
    """Ask Chapa whether tx_ref was paid with the expected amount/currency.

    Returns the provider reference on success, otherwise None.
    """
    try:
        r = requests.get(
            f"{current_app.config['CHAPA_API_BASE']}/transaction/verify/{payment.tx_ref}",
            headers=_headers(), timeout=20,
        )
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    if r.status_code != 200 or data.get("status") != "success":
        return None
    info = data.get("data") or {}
    if info.get("status") != "success":
        return None
    try:
        amount_ok = float(info.get("amount", 0)) >= float(payment.amount)
    except (TypeError, ValueError):
        amount_ok = False
    if not amount_ok or str(info.get("currency", "")).upper() != payment.currency.upper():
        return None
    return info.get("reference") or info.get("tx_ref") or payment.tx_ref
