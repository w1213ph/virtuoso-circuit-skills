v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {1:1 NMOS bias current mirror} 96 18 0 0 0.45 0.45 {}
T {Diode-connected reference} 95 60 0 0 0.23 0.23 {}
T {Matched output branch} 455 60 0 0 0.23 0.23 {}
T {Both bodies return to VSS.} 95 362 0 0 0.23 0.23 {}
T {MIR_W and MIR_L set both device dimensions in micrometres.} 95 385 0 0 0.23 0.23 {}
N 100 100 220 100 {lab=IREF}
N 220 100 220 140 {lab=IREF}
N 220 100 330 100 {lab=IREF}
N 330 100 330 180 {lab=IREF}
N 260 180 330 180 {lab=IREF}
N 330 180 460 180 {lab=IREF}
N 500 100 620 100 {lab=IOUT}
N 500 100 500 140 {lab=IOUT}
N 180 180 190 180 {lab=VSS}
N 180 180 180 250 {lab=VSS}
N 180 250 220 250 {lab=VSS}
N 220 220 220 250 {lab=VSS}
N 220 250 360 250 {lab=VSS}
N 360 250 500 250 {lab=VSS}
N 500 220 500 250 {lab=VSS}
N 500 250 540 250 {lab=VSS}
N 530 180 540 180 {lab=VSS}
N 540 180 540 250 {lab=VSS}
N 360 250 360 290 {lab=VSS}
C {devices/ipin.sym} 100 100 0 0 {name=PIN_REF lab=IREF}
C {devices/opin.sym} 620 100 0 0 {name=PIN_OUT lab=IOUT}
C {devices/iopin.sym} 360 290 1 0 {name=PIN_VSS lab=VSS}
C {NMOS18.sym} 220 180 0 1 {name=XREF w='MIR_W' l='MIR_L' ad='MIR_W*0.30' pd='2*(MIR_W+0.30)'}
C {NMOS18.sym} 500 180 0 0 {name=XMIR w='MIR_W' l='MIR_L' ad='MIR_W*0.30' pd='2*(MIR_W+0.30)'}
