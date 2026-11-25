v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {ADC sample driver} 44 -20 0 0 0.46 0.46 {}
T {Supply-derived bias} 42 21 0 0 0.24 0.24 {}
T {PMOS mirror load} 487 21 0 0 0.24 0.24 {}
T {Matched input pair} 497 278 0 0 0.24 0.24 {}
T {Tail mirror} 537 465 0 0 0.24 0.24 {}
T {__INPUT_NOTE__} 60 654 0 0 0.24 0.24 {}
T {__LOAD_NOTE__} 60 687 0 0 0.24 0.24 {}
N 120 60 480 60 {lab=VDD}
N 480 60 630 60 {lab=VDD}
N 630 60 780 60 {lab=VDD}
N 120 60 120 120 {lab=VDD}
N 480 60 480 120 {lab=VDD}
N 780 60 780 120 {lab=VDD}
N 630 60 630 30 {lab=VDD}
N 120 200 120 250 {lab=VB}
N 120 250 120 310 {lab=VB}
N 80 160 50 160 {lab=VB}
N 50 160 50 250 {lab=VB}
N 50 250 120 250 {lab=VB}
N 120 250 200 250 {lab=VB}
N 50 250 50 350 {lab=VB}
N 50 350 80 350 {lab=VB}
N 150 160 185 160 {lab=VDD}
N 150 350 185 350 {lab=VSS}
N 120 390 120 610 {lab=VSS}
N 120 610 630 610 {lab=VSS}
N 630 610 630 630 {lab=VSS}
N 480 200 480 240 {lab=VX}
N 480 240 480 310 {lab=VX}
N 480 240 605 240 {lab=VX}
N 605 240 605 160 {lab=VX}
N 520 160 605 160 {lab=VX}
N 605 160 740 160 {lab=VX}
N 450 160 415 160 {lab=VDD}
N 810 160 850 160 {lab=VDD}
N 330 350 440 350 {lab=INP}
N 820 350 920 350 {lab=INM}
N 780 200 780 250 {lab=OUT}
N 780 250 780 310 {lab=OUT}
N 780 250 920 250 {lab=OUT}
N 510 350 545 350 {lab=VSS}
N 750 350 720 350 {lab=VSS}
N 480 390 480 430 {lab=TAIL}
N 480 430 630 430 {lab=TAIL}
N 630 430 780 430 {lab=TAIL}
N 780 430 780 390 {lab=TAIL}
N 630 430 630 490 {lab=TAIL}
N 780 430 835 430 {lab=TAIL}
N 540 530 590 530 {lab=VB}
N 660 530 710 530 {lab=VSS}
N 630 570 630 610 {lab=VSS}
C {ADRV_PMOS.sym} 120 160 0 0 {name=XPREF __XPREF__}
C {ADRV_NMOS.sym} 120 350 0 0 {name=XNREF __XNREF__}
C {ADRV_PMOS.sym} 480 160 0 1 {name=XLA __XLA__}
C {ADRV_PMOS.sym} 780 160 0 0 {name=XLB __XLB__}
C {ADRV_NMOS.sym} 480 350 0 0 {name=XIP __XIP__}
C {ADRV_NMOS.sym} 780 350 0 1 {name=XIM __XIM__}
C {ADRV_NMOS.sym} 630 530 0 0 {name=XTAIL __XTAIL__}
C {devices/iopin.sym} 630 30 3 0 {name=P_VDD lab=VDD}
C {devices/iopin.sym} 630 630 1 0 {name=P_VSS lab=VSS}
C {devices/ipin.sym} 330 350 0 0 {name=P_INP lab=INP}
C {devices/ipin.sym} 920 350 0 1 {name=P_INM lab=INM}
C {devices/opin.sym} 920 250 0 0 {name=P_OUT lab=OUT}
C {devices/lab_pin.sym} 200 250 0 1 {name=B1 lab=VB}
C {devices/lab_pin.sym} 540 530 0 0 {name=B2 lab=VB}
C {devices/lab_pin.sym} 605 240 0 1 {name=M1 lab=VX}
C {devices/lab_pin.sym} 835 430 0 1 {name=T1 lab=TAIL}
C {devices/lab_pin.sym} 185 160 0 1 {name=D1 lab=VDD}
C {devices/lab_pin.sym} 185 350 0 1 {name=S1 lab=VSS}
C {devices/lab_pin.sym} 415 160 0 0 {name=D2 lab=VDD}
C {devices/lab_pin.sym} 850 160 0 1 {name=D3 lab=VDD}
C {devices/lab_pin.sym} 545 350 0 1 {name=S2 lab=VSS}
C {devices/lab_pin.sym} 720 350 0 0 {name=S3 lab=VSS}
C {devices/lab_pin.sym} 710 530 0 1 {name=S4 lab=VSS}
