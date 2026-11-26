v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Complementary transmission gate} 30 -30 0 0 0.43 0.43 {}
T {Control inverter} 45 40 0 0 0.24 0.24 {}
T {Analog path} 505 40 0 0 0.24 0.24 {}
N 40 250 115 250 {lab=EN}
N 115 250 115 170 {lab=EN}
N 115 170 170 170 {lab=EN}
N 115 250 115 325 {lab=EN}
N 115 325 170 325 {lab=EN}
N 210 75 210 130 {lab=VDD}
N 210 365 210 420 {lab=VSS}
N 210 210 210 250 {lab=ENB}
N 210 250 210 285 {lab=ENB}
N 210 250 350 250 {lab=ENB}
N 240 170 275 170 {lab=VDD}
N 240 325 275 325 {lab=VSS}
N 455 245 520 245 {lab=A}
N 520 245 520 160 {lab=A}
N 520 160 610 160 {lab=A}
N 520 245 520 330 {lab=A}
N 520 330 610 330 {lab=A}
N 690 160 795 160 {lab=B}
N 795 160 795 245 {lab=B}
N 795 245 895 245 {lab=B}
N 690 330 795 330 {lab=B}
N 795 330 795 245 {lab=B}
N 650 75 650 120 {lab=EN}
N 650 370 650 425 {lab=ENB}
N 650 190 650 212 {lab=VSS}
N 650 212 600 212 {lab=VSS}
N 650 300 650 278 {lab=VDD}
N 650 278 705 278 {lab=VDD}
C {ADRV_PMOS.sym} 210 170 0 0 {name=XIP w=1.2 l=0.15 mult=1 ad=0.36 pd=3}
C {ADRV_NMOS.sym} 210 325 0 0 {name=XIN w=0.6 l=0.15 mult=1 ad=0.18 pd=1.8}
C {ADRV_NMOS.sym} 650 160 1 0 {name=XTN w=2 l=0.15 mult=1 ad=0.6 pd=4.6}
C {ADRV_PMOS.sym} 650 330 3 0 {name=XTP w=4 l=0.15 mult=1 ad=1.2 pd=8.6}
C {devices/ipin.sym} 40 250 0 0 {name=P_EN lab=EN}
C {devices/iopin.sym} 210 75 3 0 {name=P_VDD lab=VDD}
C {devices/iopin.sym} 210 420 1 0 {name=P_VSS lab=VSS}
C {devices/iopin.sym} 455 245 0 1 {name=P_A lab=A}
C {devices/iopin.sym} 895 245 0 0 {name=P_B lab=B}
C {devices/lab_pin.sym} 350 250 0 1 {name=COMPL lab=ENB}
C {devices/lab_pin.sym} 275 170 0 1 {name=BP lab=VDD}
C {devices/lab_pin.sym} 275 325 0 1 {name=BN lab=VSS}
C {devices/lab_pin.sym} 650 75 0 0 {name=SE lab=EN}
C {devices/lab_pin.sym} 650 425 0 1 {name=SEB lab=ENB}
C {devices/lab_pin.sym} 600 212 0 0 {name=TN lab=VSS}
C {devices/lab_pin.sym} 705 278 0 1 {name=TP lab=VDD}
T {EN high connects A and B. ENB is generated locally.} 40 480 0 0 0.24 0.24 {}
