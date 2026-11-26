"""Physical four-bit source degeneration for the fully differential input pair."""
from ota_views import TOP, BOTTOM


CODE_PINS = (("T0",.5),("T1",1.5),("T2",2.375),("T3",3.5))
EXTRA_MOS = tuple("XDUT.XI."+prefix+str(bit) for prefix in ("MSWP","MSWN","MIVP","MIVN") for bit in range(4))


def effective_cdf_source(lib,cell):
    # Default-valued parameters can be absent from the OA instance property list.
    return '''procedure(fdReadTrimCDF() let((cv port vals cf ww ll)
      cv=dbOpenCellViewByType("%s" "%s" "schematic" "" "r")
      unless(cv error("Cannot reopen saved input stage for effective CDF"))
      foreach(ii cv~>instances
        when(member(ii~>cellName '("n18" "p18" "n18_ckt" "p18_ckt"))
          cf=cdfGetInstCDF(ii) ww=cdfFindParamByName(cf "w") ll=cdfFindParamByName(cf "l")
          unless(ww && ll error("Cannot read effective MOS dimensions"))
          vals=cons(list(ii~>name ii~>cellName ww~>value ll~>value) vals)))
      port=outfile("trim-effective-cdf.json") fprintf(port "%%s" cmpJson(vals))
      close(port) dbClose(cv) t))''' % (lib,cell)


def bit_gain(code,bit):
    if type(code) is not int or not 0<=code<16 or type(bit) is not int or not 0<=bit<4:
        raise ValueError("Expected a four-bit word and a bit from zero to three")
    return str((code>>bit)&1)


def inverter(d,bit,x,y,trim):
    gate,bar = "T"+str(bit),"TB"+str(bit)
    for kind,center,supply in (("p",y+1,"VDD"),("n",y-1,"VSS")):
        d.mos("MIV"+kind.upper()+str(bit),kind,x,center,-1,(bar,gate,supply,supply),
              None,geometry=(trim["inverter_"+kind+"_um"],trim["length_um"]))
    d.stub((x,y+1.1875),TOP,"VDD",.625)
    d.stub((x,y-1.1875),BOTTOM,"VSS",.625)
    d.wire((x,y+.8125),(x,y),(x,y-.8125))
    d.stub((x,y),(1,0),bar,1.125)
    d.wire((x-.25,y+1),(x-1,y+1),(x-1,y),(x-1,y-1),(x-.25,y-1))
    d.port(gate,x-2,y,"R0")
    d.wire((x-2,y),(x-1,y),net=gate,label=(x-1.75,y))


def source_bank(d):
    trim = d.sizes["source_trim"]
    if len(trim["ohms"])!=len(CODE_PINS) or any(r<=0 for r in trim["ohms"]):
        raise ValueError("Expected four positive physical resistor targets")
    for side,x,sign in (("P",4,-1),("N",12,1)):
        previous_y = 3.5625
        for bit,resistance in enumerate(trim["ohms"]):
            y = 2.125-2*bit
            upper = "S"+side+str(bit)
            lower = "S"+side+str(bit+1) if bit<3 else "TAIL"
            gate = ("T" if side=="P" else "TB")+str(bit)
            sw = x+sign*2.25
            d.resistor("RS"+side+str(bit),(x,y),upper,lower,resistance,label_x=x+.15 if side=="P" else x-1.875)
            d.mos("MSW"+side+str(bit),"n",sw,y-.1875,sign,(upper,gate,lower,"VSS"),
                  None,geometry=(trim["switch_um"],trim["length_um"]))
            d.wire((x,previous_y),(x,y+.5),(x,y))
            d.wire((x,y+.5),(sw,y+.5),(sw,y),net=upper,label=(x+sign*1.125,y+.5))
            d.wire((x,y-.375),(x,y-.875))
            d.wire((x,y-.875),(sw,y-.875),(sw,y-.375))
            d.stub((sw+sign*.25,y-.1875),(sign,0),gate,.875)
            previous_y = y-.875
    for bit in range(len(CODE_PINS)):
        inverter(d,bit,22+5.5*(bit%2),4-5*(bit//2),trim)
    d.text(19.5,7.125,"Complementary switch controls",.2)
    d.text(19.5,6.625,"High T bypasses the P resistor; low T bypasses the N resistor.",.12)
    return previous_y
