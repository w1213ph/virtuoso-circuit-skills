v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {ADC sampling load} 55 -25 0 0 0.45 0.45 {}
T {Sampling switch} 60 22 0 0 0.24 0.24 {}
T {Charge storage} 450 22 0 0 0.24 0.24 {}
T {__CAP_NOTE__} 56 520 0 0 0.24 0.24 {}
T {CLK high tracks the input. CLKB drives the PMOS gate.} 56 552 0 0 0.24 0.24 {}
N 70 230 165 230 {lab=IN}
N 165 230 165 140 {lab=IN}
N 165 140 260 140 {lab=IN}
N 165 230 165 320 {lab=IN}
N 165 320 260 320 {lab=IN}
N 340 140 445 140 {lab=SAMPLE}
N 445 140 445 230 {lab=SAMPLE}
N 445 230 570 230 {lab=SAMPLE}
N 570 230 675 230 {lab=SAMPLE}
N 340 320 445 320 {lab=SAMPLE}
N 445 320 445 230 {lab=SAMPLE}
N 570 230 570 285 {lab=SAMPLE}
N 570 345 570 405 {lab=VSS}
N 300 65 300 100 {lab=CLK}
N 300 360 300 410 {lab=CLKB}
N 300 170 300 190 {lab=VSS}
N 300 190 250 190 {lab=VSS}
N 300 290 300 265 {lab=VDD}
N 300 265 355 265 {lab=VDD}
C {ADRV_NMOS.sym} 300 140 1 0 {name=XSN __XSN__}
C {ADRV_PMOS.sym} 300 320 3 0 {name=XSP __XSP__}
C {devices/ipin.sym} 70 230 0 0 {name=P_IN lab=IN}
C {devices/opin.sym} 675 230 0 0 {name=P_SAMPLE lab=SAMPLE}
C {devices/ipin.sym} 300 65 1 0 {name=P_CLK lab=CLK}
C {devices/ipin.sym} 300 410 3 0 {name=P_CLKB lab=CLKB}
C {devices/iopin.sym} 355 265 0 0 {name=P_VDD lab=VDD}
C {devices/iopin.sym} 570 405 1 0 {name=P_VSS lab=VSS}
C {devices/lab_pin.sym} 250 190 0 0 {name=S1 lab=VSS}
C {devices/capa.sym} 570 315 0 0 {name=CSH value=__CS__}
