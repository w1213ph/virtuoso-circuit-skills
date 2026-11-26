v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Programmable gain at the receiver input} 42 -28 0 0 0.44 0.44 {}
T {Signal input} 36 137 0 0 0.24 0.24 {}
T {Feedback amplifier} 259 75 0 0 0.24 0.24 {}
T {Receiver load} 630 75 0 0 0.24 0.24 {}
T {Supply and gain controls} 40 470 0 0 0.29 0.29 {}
T {B1 B0 select 00, 01, 10 or 11. Measure RXIN / VIN. Probe DRIVE to localize losses.} 42 696 0 0 0.24 0.24 {}
T {Input bias 0.4 x VDD, receiver 5 pF, AC 10000 to 1000000 Hz.} 42 732 0 0 0.24 0.24 {}
T {Both bits rise at 1 us and fall at 4 us. Compare RXIN with the final DC value.} 42 768 0 0 0.24 0.24 {}
N 85 295 85 235 {lab=VIN}
N 85 235 230 235 {lab=VIN}
N 85 355 85 395 {lab=0}
N 440 235 515 235 {lab=DRIVE}
N 515 235 600 235 {lab=DRIVE}
N 800 235 935 235 {lab=RXIN}
C {PGA_STAGE.sym} 335 235 0 0 {name=XPGA}
C {PGA_RECEIVER.sym} 700 235 0 0 {name=XRX}
C {ADRV_SOURCE.sym} 85 325 0 0 {name=VSIG value="DC 'VINBIAS' AC 1"}
C {devices/lab_pin.sym} 85 235 0 0 {name=VI lab=VIN}
C {devices/lab_pin.sym} 515 235 1 0 {name=VD lab=DRIVE}
C {devices/opin.sym} 935 235 0 0 {name=P_RXIN lab=RXIN}
C {devices/gnd.sym} 85 395 0 0 {name=GS lab=0}
N 270 330 270 382 {lab=B0}
C {devices/lab_pin.sym} 270 382 0 0 {name=PN0 lab=B0}
N 310 330 310 424 {lab=B1}
C {devices/lab_pin.sym} 310 424 0 0 {name=PN1 lab=B1}
N 345 330 345 365 {lab=0}
C {devices/gnd.sym} 345 365 0 0 {name=PG2 lab=0}
N 345 140 345 110 {lab=VDD}
C {devices/lab_pin.sym} 345 110 0 1 {name=PN3 lab=VDD}
N 700 155 700 115 {lab=VDD}
C {devices/lab_pin.sym} 700 115 0 1 {name=PN4 lab=VDD}
N 700 315 700 370 {lab=0}
C {devices/gnd.sym} 700 370 0 0 {name=PG5 lab=0}
N 120 515 120 555 {lab=VDD}
N 120 615 120 655 {lab=0}
C {ADRV_SOURCE.sym} 120 585 0 0 {name=VSUP value="DC 'VSUPPLY'"}
C {devices/lab_pin.sym} 120 515 0 1 {name=LVDD lab=VDD}
C {devices/gnd.sym} 120 655 0 0 {name=GVDD lab=0}
N 420 515 420 555 {lab=B0}
N 420 615 420 655 {lab=0}
C {ADRV_SOURCE.sym} 420 585 0 0 {name=VB0 value="DC 'VSUPPLY*BIT0' PWL(0 0 1u 0 1.002u 'VSUPPLY' 4u 'VSUPPLY' 4.002u 0 7u 0)"}
C {devices/lab_pin.sym} 420 515 0 1 {name=LB0 lab=B0}
C {devices/gnd.sym} 420 655 0 0 {name=GB0 lab=0}
N 700 515 700 555 {lab=B1}
N 700 615 700 655 {lab=0}
C {ADRV_SOURCE.sym} 700 585 0 0 {name=VB1 value="DC 'VSUPPLY*BIT1' PWL(0 0 1u 0 1.002u 'VSUPPLY' 4u 'VSUPPLY' 4.002u 0 7u 0)"}
C {devices/lab_pin.sym} 700 515 0 1 {name=LB1 lab=B1}
C {devices/gnd.sym} 700 655 0 0 {name=GB1 lab=0}
