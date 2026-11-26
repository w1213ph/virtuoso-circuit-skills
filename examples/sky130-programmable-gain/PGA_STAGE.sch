v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Inverting programmable gain} 45 -30 0 0 0.44 0.44 {}
T {Always-connected input} 65 132 0 0 0.24 0.24 {}
T {Feedback} 620 50 0 0 0.24 0.24 {}
T {CMOS gain stage} 835 170 0 0 0.24 0.24 {}
T {B1 B0 select extra input conductance. Ideal magnitudes: 1, 2, 3 and 4 V/V.} 48 700 0 0 0.24 0.24 {}
N 55 215 125 215 {lab=IN}
N 125 215 465 215 {lab=IN}
N 125 215 125 360 {lab=IN}
N 125 360 125 550 {lab=IN}
N 125 360 220 360 {lab=IN}
N 125 550 220 550 {lab=IN}
N 525 215 655 215 {lab=SUM}
N 655 215 655 360 {lab=SUM}
N 655 360 655 550 {lab=SUM}
N 655 215 655 178 {lab=SUM}
N 655 178 655 105 {lab=SUM}
N 655 105 785 105 {lab=SUM}
N 655 178 611 178 {lab=SUM}
N 845 105 1175 105 {lab=OUT}
N 1175 105 1175 375 {lab=OUT}
N 390 360 415 360 {lab=S0}
N 415 360 465 360 {lab=S0}
N 525 360 655 360 {lab=SUM}
N 390 550 415 550 {lab=S1}
N 415 550 465 550 {lab=S1}
N 525 550 655 550 {lab=SUM}
N 655 360 800 360 {lab=SUM}
N 800 360 800 290 {lab=SUM}
N 800 290 925 290 {lab=SUM}
N 800 360 800 460 {lab=SUM}
N 800 460 925 460 {lab=SUM}
N 965 330 965 375 {lab=OUT}
N 965 375 965 420 {lab=OUT}
N 965 375 1175 375 {lab=OUT}
N 1175 375 1245 375 {lab=OUT}
N 965 195 965 250 {lab=VDD}
N 965 500 965 565 {lab=VSS}
N 995 290 1045 290 {lab=VDD}
N 995 460 1045 460 {lab=VSS}
C {devices/res.sym} 495 215 1 0 {name=RBASE value=120k}
C {devices/res.sym} 495 360 1 0 {name=R0 value=120k}
C {devices/res.sym} 495 550 1 0 {name=R1 value=60k}
C {devices/res.sym} 815 105 1 0 {name=RF value=120k}
C {ADRV_PMOS.sym} 965 290 0 0 {name=XAMP_P w=4.9 l=1 mult=8 ad=1.47 pd=10.4}
C {ADRV_NMOS.sym} 965 460 0 0 {name=XAMP_N w=2 l=1 mult=4 ad=0.6 pd=4.6}
C {devices/ipin.sym} 55 215 0 0 {name=P_IN lab=IN}
C {devices/opin.sym} 1245 375 0 0 {name=P_OUT lab=OUT}
C {devices/iopin.sym} 965 195 3 0 {name=P_VDD lab=VDD}
C {devices/iopin.sym} 965 565 1 0 {name=P_VSS lab=VSS}
C {devices/lab_pin.sym} 1045 290 0 1 {name=BP lab=VDD}
C {devices/lab_pin.sym} 1045 460 0 1 {name=BN lab=VSS}
C {devices/lab_pin.sym} 611 178 0 0 {name=SJ lab=SUM}
T {Core W/L (um): N 2/1 x 4; P 4.9/1 x 8.} 48 735 0 0 0.24 0.24 {}
C {PGA_SWITCH.sym} 305 360 0 0 {name=XSW0}
N 285 300 285 272 {lab=B0}
C {devices/ipin.sym} 285 272 1 0 {name=P_B0 lab=B0}
N 340 300 340 278 {lab=VDD}
C {devices/lab_pin.sym} 340 278 0 1 {name=VDD0 lab=VDD}
N 340 420 340 440 {lab=VSS}
C {devices/lab_pin.sym} 340 440 0 1 {name=VSS0 lab=VSS}
N 415 360 415 335 {lab=S0}
C {devices/lab_pin.sym} 415 335 0 1 {name=BR0 lab=S0}
C {PGA_SWITCH.sym} 305 550 0 0 {name=XSW1}
N 285 490 285 462 {lab=B1}
C {devices/ipin.sym} 285 462 1 0 {name=P_B1 lab=B1}
N 340 490 340 468 {lab=VDD}
C {devices/lab_pin.sym} 340 468 0 1 {name=VDD1 lab=VDD}
N 340 610 340 630 {lab=VSS}
C {devices/lab_pin.sym} 340 630 0 1 {name=VSS1 lab=VSS}
N 415 550 415 525 {lab=S1}
C {devices/lab_pin.sym} 415 525 0 1 {name=BR1 lab=S1}
