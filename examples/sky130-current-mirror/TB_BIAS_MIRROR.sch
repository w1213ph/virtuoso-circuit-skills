v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Bias mirror characterisation} 60 4 0 0 0.45 0.45 {}
T {External reference drive} 205 64 0 0 0.23 0.23 {}
T {Output voltage fixture} 555 187 0 0 0.23 0.23 {}
T {DC sweeps the output voltage. The pulse measures settling after the external reference turns on.} 55 410 0 0 0.22 0.22 {}
T {The 100 fF reference load belongs to the testbench.} 55 435 0 0 0.22 0.22 {}
N 10 60 170 60 {lab=VDD}
N 10 60 10 90 {lab=VDD}
N 170 60 170 90 {lab=VDD}
N 10 150 10 170 {lab=0}
N 170 150 170 250 {lab=IREF}
N 170 250 240 250 {lab=IREF}
N 240 250 320 250 {lab=IREF}
N 240 250 240 290 {lab=IREF}
N 240 350 240 370 {lab=0}
N 520 250 650 250 {lab=IOUT}
N 650 310 650 350 {lab=0}
N 420 340 420 370 {lab=0}
C {devices/vsource.sym} 10 120 0 0 {name=VBIAS value="DC 'VDDTEST'"}
C {devices/isource.sym} 170 120 0 0 {name=IREFDRV value="DC 'ISET' PULSE(0 'ISET' 10n 1n 1n 1u 2u)"}
C {devices/capa.sym} 240 320 0 0 {name=CREF value=100f}
C {BIAS_MIRROR.sym} 420 270 0 0 {name=XBIAS}
C {devices/vsource.sym} 650 280 0 0 {name=VFORCE value="DC 'VOUTTEST'"}
C {devices/gnd.sym} 10 170 0 0 {name=G1 lab=0}
C {devices/gnd.sym} 240 370 0 0 {name=G2 lab=0}
C {devices/gnd.sym} 420 370 0 0 {name=G3 lab=0}
C {devices/gnd.sym} 650 350 0 0 {name=G4 lab=0}
C {devices/lab_pin.sym} 10 60 0 0 {name=L_VDD lab=VDD}
C {devices/lab_pin.sym} 170 250 0 0 {name=L_REF lab=IREF}
C {devices/lab_pin.sym} 650 250 0 1 {name=L_OUT lab=IOUT}
