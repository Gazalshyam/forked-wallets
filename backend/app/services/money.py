"""Integer-cent money helpers. No floats anywhere."""
import re


def equal_split(total_minor: int, member_ids: list[int]) -> dict[int, int]:
    """Equal split; remainder cents go to the first members by ID (one cent each)."""
    if not isinstance(total_minor, int) or total_minor < 0:
        raise ValueError("total must be a non-negative integer number of cents")
    ids = sorted(member_ids)
    if not ids:
        raise ValueError("cannot split between zero members")
    base, rem = divmod(total_minor, len(ids))
    return {mid: base + (1 if i < rem else 0) for i, mid in enumerate(ids)}


def capped_split(total_minor: int, caps: dict[int, int]) -> dict[int, int]:
    """E4: equal split where nobody pays more than their cap (their balance). Whatever a capped member cannot
    cover is split equally among the rest. Remainder cents go to the first members by ID, as in equal_split.
    Raises ValueError if the caps together cannot cover the total."""
    if not isinstance(total_minor, int) or total_minor < 0:
        raise ValueError("total must be a non-negative integer number of cents")
    left_caps = {k: max(0, int(v)) for k, v in caps.items()}
    if sum(left_caps.values()) < total_minor:
        raise ValueError("the participants' balances do not cover the total")
    out = {k: 0 for k in caps}
    active = sorted(k for k, v in left_caps.items() if v > 0)
    left = total_minor
    while left > 0:
        parts = equal_split(left, active)
        over = [k for k in active if parts[k] > left_caps[k]]
        if not over:
            for k, v in parts.items():
                out[k] += v
            break
        for k in over:
            out[k] += left_caps[k]
            left -= left_caps[k]
            active.remove(k)
    return out


def proportional_split(total_minor: int, weights: dict[int, int]) -> dict[int, int]:
    """Split by positive weights; leftover cents go to the largest remainders (ties: lowest ID). Integer only."""
    w = {k: max(0, int(v)) for k, v in weights.items()}
    wsum = sum(w.values())
    if total_minor == 0 or wsum == 0:
        return {k: 0 for k in weights}
    out, rems = {}, []
    for k in sorted(w):
        q, r = divmod(total_minor * w[k], wsum)
        out[k] = q
        rems.append((-r, k))
    for _, k in sorted(rems)[: total_minor - sum(out.values())]:
        out[k] += 1
    return out


def validate_custom_split(total_minor: int, shares: dict[int, int], member_ids: list[int]) -> dict[int, int]:
    """Custom split must cover exactly the active members and sum exactly to the total."""
    if set(shares) != set(member_ids):
        raise ValueError("custom split must list every active member exactly once")
    for v in shares.values():
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise ValueError("each share must be a non-negative integer number of cents")
    if sum(shares.values()) != total_minor:
        raise ValueError(f"custom split sums to {sum(shares.values())} cents but the price is {total_minor} cents")
    return dict(shares)


_AMOUNT = re.compile(r"^\$?\s*(\d{1,9})(?:\.(\d{1,2}))?$")


def parse_usd(text: str) -> int:
    """'5' / '5.0' / '$5.00' -> 500. Raises ValueError otherwise."""
    m = _AMOUNT.match(str(text).strip().replace(",", ""))
    if not m:
        raise ValueError(f"not a USD amount: {text!r}")
    return int(m.group(1)) * 100 + int((m.group(2) or "0").ljust(2, "0"))


def to_paypal_value(minor: int) -> str:
    """500 -> '5.00' (PayPal wants a decimal string)."""
    if minor < 0:
        raise ValueError("negative amount")
    return f"{minor // 100}.{minor % 100:02d}"


def fmt_usd(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    minor = abs(minor)
    return f"{sign}${minor // 100:,}.{minor % 100:02d}"
