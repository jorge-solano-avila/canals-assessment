"""The address a service is asked to ship to.

A frozen dataclass rather than the Pydantic request schema, so services stay
independent of the wire contract and address normalization lives in one place
that is not the API. api/ maps ShippingAddressIn onto this.
"""

import re
from dataclasses import dataclass

_WHITESPACE = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip()).casefold()


@dataclass(frozen=True, slots=True)
class PostalAddress:
    line1: str
    city: str
    postal_code: str
    country_code: str
    line2: str | None = None

    def normalized_key(self) -> str:
        """Stable cache key input.

        line2 is deliberately excluded: it is an apartment or floor, which does
        not move the building. Including it would fragment the geocoding cache
        across every resident of one address for no gain in accuracy.
        """
        return "|".join(
            (
                _norm(self.line1),
                _norm(self.city),
                _norm(self.postal_code),
                self.country_code.strip().upper(),
            )
        )
