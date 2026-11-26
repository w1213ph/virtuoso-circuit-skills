"""Choose a physical code per die from one calibration condition and preserve the die identities."""
import math


def select_codes(errors,bits):
    """errors[code][draw_id] is a signed measured error at the same calibration point."""
    if type(bits) is not int or bits<1 or not errors or any(type(c) is not int for c in errors):
        raise ValueError("A complete physical code sweep with integer codes is required")
    count = len(errors)
    if bits!=count.bit_length()-1 or count & (count-1) or set(errors)!=set(range(count)):
        raise ValueError("Calibration requires one measurement for each physical code")
    ids = set(errors[0])
    if not ids or any(set(rows)!=ids for rows in errors.values()):
        raise ValueError("Every code must measure the same identified draws")
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x)
           for rows in errors.values() for x in rows.values()):
        raise ValueError("Expected finite signed measurements for every die and code")
    return {draw:min(errors,key=lambda code:(abs(errors[code][draw]),code)) for draw in sorted(ids)}


def held_measurements(frozen,observed):
    """Read only the previously chosen code for each draw at a different operating condition."""
    if not frozen or any(c not in observed for c in frozen.values()):
        raise ValueError("Missing evidence for a frozen code")
    if any(set(observed[c])!=set(frozen) for c in set(frozen.values())):
        raise ValueError("Validation evidence belongs to different physical draws")
    return {draw:observed[code][draw] for draw,code in frozen.items()}
