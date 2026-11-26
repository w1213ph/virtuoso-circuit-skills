"""Count changes in joint acceptance on the same physical mismatch draws."""


def compare_passes(before, after):
    """Each input supplies (verified draw identity, Boolean joint verdict), in any order."""
    def collect(rows):
        found = {}
        for identity, passed in rows:
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError("Missing draw identity")
            if identity in found or type(passed) is not bool:
                raise ValueError("Expected a unique draw identity and an explicit Boolean verdict")
            found[identity] = passed
        return found

    original, candidate = collect(before), collect(after)
    if not original or original.keys() != candidate.keys():
        raise ValueError("The two candidates must cover the same nonempty set of actual draws")
    names = {(True, True): "kept_pass", (False, True): "gained_pass",
             (True, False): "lost_pass", (False, False): "both_failed"}
    outcomes = dict.fromkeys(names.values(), 0)
    for identity, prior in original.items():
        outcomes[names[prior, candidate[identity]]] += 1
    return outcomes
