"""Money helpers. All amounts are integer cents."""

from decimal import ROUND_HALF_UP, Decimal


def _round_half_up(value):
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def line_net_cents(qty, unit_cents, discount_pct=0):
    """Net amount of one invoice line, rounded half up to whole cents."""
    gross = Decimal(str(qty)) * Decimal(str(unit_cents))
    factor = (Decimal(100) - Decimal(str(discount_pct))) / Decimal(100)
    return _round_half_up(gross * factor)


def vat_cents(net_cents, rate):
    """VAT on a net amount (rate in percent, e.g. 13), rounded half up."""
    return _round_half_up(Decimal(str(net_cents)) * Decimal(str(rate)) / Decimal(100))


def format_eur(cents):
    """Greek format: 123456 -> '1.234,56 €', -197 -> '−1,97 €' (U+2212)."""
    sign = "−" if cents < 0 else ""
    euros, rest = divmod(abs(int(cents)), 100)
    whole = f"{euros:,}".replace(",", ".")
    return f"{sign}{whole},{rest:02d} €"
