# -*- coding: utf-8 -*-
"""Local customer directory used to fill Brother label templates."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Customer:
    """A saved label recipient; customer details stay local to the app."""

    customer_id: str
    name: str
    address: str = ""
    postal_code: str = ""
    city: str = ""
    phone: str = ""
    email: str = ""

    @classmethod
    def create(
        cls,
        name: str,
        address: str = "",
        postal_code: str = "",
        city: str = "",
        phone: str = "",
        email: str = "",
        *,
        customer_id: str | None = None,
    ) -> "Customer":
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Kundnamn måste anges")
        return cls(
            customer_id=str(customer_id or uuid4().hex),
            name=clean_name[:160],
            address=str(address).strip()[:240],
            postal_code=str(postal_code).strip()[:32],
            city=str(city).strip()[:120],
            phone=str(phone).strip()[:80],
            email=str(email).strip()[:160],
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Customer":
        return cls.create(
            str(value.get("name", "")),
            str(value.get("address", "")),
            str(value.get("postal_code", "")),
            str(value.get("city", "")),
            str(value.get("phone", "")),
            str(value.get("email", "")),
            customer_id=str(value.get("customer_id", "") or uuid4().hex),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "customer_id": self.customer_id,
            "name": self.name,
            "address": self.address,
            "postal_code": self.postal_code,
            "city": self.city,
            "phone": self.phone,
            "email": self.email,
        }

    def label_values(self) -> dict[str, str]:
        """Common P-touch object names; exact object-name matches are filled."""
        return {
            "KUND": self.name,
            "KUNDNAMN": self.name,
            "NAMN": self.name,
            "CUSTOMER": self.name,
            "CUSTOMER_NAME": self.name,
            "ADRESS": self.address,
            "GATUADRESS": self.address,
            "ADDRESS": self.address,
            "POSTNR": self.postal_code,
            "POSTNUMMER": self.postal_code,
            "POSTAL_CODE": self.postal_code,
            "POSTORT": self.city,
            "ORT": self.city,
            "STAD": self.city,
            "CITY": self.city,
            "TELEFON": self.phone,
            "PHONE": self.phone,
            "EPOST": self.email,
            "EMAIL": self.email,
        }


def load_customers(path: Path | str) -> list[Customer]:
    """Read a customer list, accepting both current and early list-only JSON."""
    source = Path(path)
    if not source.is_file():
        return []
    data = json.loads(source.read_text(encoding="utf-8"))
    items = data.get("customers", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Kundfilen måste innehålla en lista med kunder")
    customers: list[Customer] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            customer = Customer.from_dict(item)
        except (TypeError, ValueError):
            continue
        if customer.customer_id not in seen:
            customers.append(customer)
            seen.add(customer.customer_id)
    return sorted(customers, key=lambda customer: customer.name.casefold())


def save_customers(path: Path | str, customers: list[Customer]) -> None:
    """Persist customer data atomically alongside the app's configuration."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "customers": [customer.to_dict() for customer in customers],
    }
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
