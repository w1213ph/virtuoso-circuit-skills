v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Receiver input and bias} 30 -30 0 0 0.44 0.44 {}
T {Isolation resistor and input load} 50 95 0 0 0.24 0.24 {}
T {Receiver input MOS} 740 100 0 0 0.24 0.24 {}
T {Bias divider} 60 330 0 0 0.24 0.24 {}
T {RXIN is the loaded gate node used for gain and settling.} 45 680 0 0 0.24 0.24 {}
N 50 240 195 240 {lab=DRIVE}
N 255 240 435 240 {lab=RXIN}
N 435 240 600 240 {lab=RXIN}
N 600 240 700 240 {lab=RXIN}
N 700 240 820 240 {lab=RXIN}
N 700 240 700 155 {lab=RXIN}
N 700 155 955 155 {lab=RXIN}
N 955 155 1010 155 {lab=RXIN}
N 435 240 435 305 {lab=RXIN}
N 435 365 435 430 {lab=VSS}
N 600 240 600 305 {lab=RXIN}
N 600 365 600 435 {lab=BIAS}
N 860 172 860 200 {lab=VDD}
N 890 240 945 240 {lab=VSS}
N 860 280 860 320 {lab=MON}
N 860 320 860 355 {lab=MON}
N 860 415 860 480 {lab=VSS}
N 180 350 180 395 {lab=VDD}
N 180 455 180 490 {lab=BIAS}
N 180 490 180 525 {lab=BIAS}
N 180 490 295 490 {lab=BIAS}
N 180 585 180 625 {lab=VSS}
C {devices/ipin.sym} 50 240 0 0 {name=P_DRIVE lab=DRIVE}
C {devices/opin.sym} 1010 155 0 0 {name=P_RXIN lab=RXIN}
C {devices/res.sym} 225 240 1 0 {name=RISO value=2.2k}
C {devices/capa.sym} 435 335 0 0 {name=CRX value=5p}
C {devices/res.sym} 600 335 0 0 {name=RBIAS value=150k}
C {devices/lab_pin.sym} 600 435 0 1 {name=BR lab=BIAS}
C {ADRV_NMOS.sym} 860 240 0 0 {name=XBUF w=4 l=0.5 mult=1 ad=1.2 pd=8.6}
C {devices/iopin.sym} 180 350 3 0 {name=P_VDD lab=VDD}
C {devices/iopin.sym} 180 625 1 0 {name=P_VSS lab=VSS}
C {devices/res.sym} 860 385 0 0 {name=RLOAD value=56k}
C {devices/lab_pin.sym} 860 172 0 1 {name=PD lab=VDD}
C {devices/lab_pin.sym} 945 240 0 1 {name=PB lab=VSS}
C {devices/lab_pin.sym} 860 320 0 1 {name=RM lab=MON}
C {devices/lab_pin.sym} 860 480 0 1 {name=PS lab=VSS}
C {devices/lab_pin.sym} 435 430 0 1 {name=PC lab=VSS}
C {devices/res.sym} 180 425 0 0 {name=RTOP value=150k}
C {devices/res.sym} 180 555 0 0 {name=RBOT value=100k}
C {devices/lab_pin.sym} 295 490 0 1 {name=BD lab=BIAS}
