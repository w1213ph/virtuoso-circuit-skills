"""Netlist conventions of the two pinned four-terminal MOS libraries."""
from typing import NamedTuple


class MosModel(NamedTuple):
    label: str
    nmos: str
    pmos: str
    inner_n: str
    inner_p: str
    length_suffix: str
    area_suffix: str
    symbol: str

    def suffix(self, field):
        if field == "nf":
            return ""
        return self.area_suffix if field in ("ad", "as") else self.length_suffix

    def geometry(self, field, value):
        return "{:.8g}{}".format(value, self.suffix(field))


SKY130 = MosModel("SKY130", "sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8",
                  "msky130_fd_pr__nfet_01v8", "msky130_fd_pr__pfet_01v8", "", "", "NMOS18.sym")
GF180 = MosModel("GF180MCU", "nmos_3p3", "pmos_3p3", "m0", "m0", "u", "p", "NMOS33.sym")
