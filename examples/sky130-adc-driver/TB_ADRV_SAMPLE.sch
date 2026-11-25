v {xschem version=3.4.4 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Sampling a buffered input} 60 -10 0 0 0.47 0.47 {}
T {Input step} 67 171 0 0 0.24 0.24 {}
T {Unity-gain driver} 298 122 0 0 0.24 0.24 {}
T {Transmission gate and hold capacitor} 704 32 0 0 0.24 0.24 {}
T {Supply and complementary clocks} 58 507 0 0 0.29 0.29 {}
T {__TB_NOTE__} 55 732 0 0 0.24 0.24 {}
T {VPROBE injects the loop test. Its DC and transient voltage is zero.} 55 763 0 0 0.24 0.24 {}
N 80 290 80 250 {lab=VIN}
N 80 250 260 250 {lab=VIN}
N 80 350 80 390 {lab=0}
N 260 290 210 290 {lab=FB}
N 210 290 210 355 {lab=FB}
N 210 415 210 465 {lab=OUT}
N 210 465 505 465 {lab=OUT}
N 505 465 505 270 {lab=OUT}
N 460 270 505 270 {lab=OUT}
N 505 270 570 270 {lab=OUT}
N 570 270 700 270 {lab=OUT}
N 570 270 570 330 {lab=OUT}
N 570 390 570 420 {lab=0}
N 360 180 360 148 {lab=VDD}
N 360 360 360 395 {lab=0}
N 900 270 1040 270 {lab=SAMPLE}
N 755 180 755 100 {lab=CLK}
N 755 360 755 415 {lab=CLKB}
N 830 180 830 105 {lab=VDD}
N 830 360 830 400 {lab=0}
C {ADRV_OTA.sym} 360 270 0 0 {name=XDRV}
C {ADRV_SAMPLER.sym} 800 270 0 0 {name=XLOAD}
C {ADRV_SOURCE.sym} 80 320 0 0 {name=VDATA value="__VDATA__"}
C {ADRV_SOURCE.sym} 210 385 0 0 {name=VPROBE value="__VPROBE__"}
C {devices/capa.sym} 570 360 0 0 {name=COUT value=__CFIX__}
C {devices/lab_pin.sym} 80 250 0 0 {name=VIN_L lab=VIN}
C {devices/lab_pin.sym} 210 290 0 0 {name=FB_L lab=FB}
C {devices/lab_pin.sym} 505 270 1 0 {name=OUT_L lab=OUT}
C {devices/lab_pin.sym} 1040 270 0 1 {name=SAMP_L lab=SAMPLE}
C {devices/lab_pin.sym} 360 148 0 1 {name=SUP1 lab=VDD}
C {devices/lab_pin.sym} 755 100 0 0 {name=CK1 lab=CLK}
C {devices/lab_pin.sym} 755 415 0 0 {name=CK2 lab=CLKB}
C {devices/lab_pin.sym} 830 105 0 1 {name=SUP2 lab=VDD}
C {devices/gnd.sym} 80 390 0 0 {name=G0 lab=0}
C {devices/gnd.sym} 360 395 0 0 {name=G1 lab=0}
C {devices/gnd.sym} 570 420 0 0 {name=G2 lab=0}
C {devices/gnd.sym} 830 400 0 0 {name=G3 lab=0}
N 120 570 120 605 {lab=VDD}
N 120 665 120 695 {lab=0}
C {ADRV_SOURCE.sym} 120 635 0 0 {name=VVDD value="__VVDD__"}
C {devices/lab_pin.sym} 120 570 0 1 {name=LS0 lab=VDD}
C {devices/gnd.sym} 120 695 0 0 {name=GS0 lab=0}
N 405 570 405 605 {lab=CLK}
N 405 665 405 695 {lab=0}
C {ADRV_SOURCE.sym} 405 635 0 0 {name=VCLK value="__VCLK__"}
C {devices/lab_pin.sym} 405 570 0 1 {name=LS1 lab=CLK}
C {devices/gnd.sym} 405 695 0 0 {name=GS1 lab=0}
N 690 570 690 605 {lab=CLKB}
N 690 665 690 695 {lab=0}
C {ADRV_SOURCE.sym} 690 635 0 0 {name=VCLKB value="__VCLKB__"}
C {devices/lab_pin.sym} 690 570 0 1 {name=LS2 lab=CLKB}
C {devices/gnd.sym} 690 695 0 0 {name=GS2 lab=0}
