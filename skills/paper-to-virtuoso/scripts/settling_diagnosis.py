"""Measure sampled settling and separate even and odd common-mode errors."""
import math
from itertools import chain


def settling_window(times, values, target, tolerance, start, stop, max_sample_interval=None):
    """Return seconds relative to the edge. An excursion after entry invalidates that entry."""
    if (len(times)!=len(values) or len(times)<2 or not all(math.isfinite(n) for n in (start,stop,target,tolerance))
            or not start<stop or tolerance<=0 or not all(math.isfinite(n) for n in chain(times,values))
            or any(b<=a for a,b in zip(times,times[1:]))):
        raise ValueError("Expected finite, strictly ordered samples and a positive tolerance")
    # 1 fs accounts for rounding of Spectre time stamps at the ends of a requested window.
    if times[0]>start+1e-15 or times[-1]<stop-1e-15:
        raise ValueError("Waveform does not cover the requested observation window")
    window = [(max(0,t-start),abs(v-target)) for t,v in zip(times,values) if start-1e-15<=t<=stop+1e-15]
    if len(window)<2:
        raise ValueError("Too few samples in the observation window")
    if max_sample_interval is not None:
        if not math.isfinite(max_sample_interval) or max_sample_interval<=0:
            raise ValueError("Maximum sample interval must be finite and positive")
        stamps = [0]+[t for t,_ in window]+[stop-start]
        if any(b-a>max_sample_interval for a,b in zip(stamps,stamps[1:])):
            raise ValueError("Observation window contains a sampling gap")
    first = next((i for i,(_,error) in enumerate(window) if error<=tolerance),None)
    last_out = next((i for i in range(len(window)-1,-1,-1) if window[i][1]>tolerance),-1)
    exits = sum(a<=tolerance<b for (_,a),(_,b) in zip(window,window[1:]))
    return dict(first_entry_s=None if first is None else window[first][0],
                permanent_entry_s=None if last_out+1==len(window) else window[last_out+1][0],
                exits_after_entry=exits,
                max_error_after_first=None if first is None else max(error for _,error in window[first:]))


def common_components(commands,errors):
    """Split common-mode error at symmetric commands into zero, odd and even terms."""
    if (len(commands)!=3 or len(errors)!=3 or not all(math.isfinite(v) for v in (*commands,*errors))
            or not commands[0]<0<commands[2] or abs(commands[1])>1e-9
            or not math.isclose(-commands[0],commands[2],rel_tol=1e-7,abs_tol=1e-12)):
        raise ValueError("Three equally spaced negative, zero and positive commands are required")
    minus,zero,plus=errors
    return dict(zero=zero,odd=(plus-minus)/2,even=(plus+minus)/2-zero)
