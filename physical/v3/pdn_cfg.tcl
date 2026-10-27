# Copyright 2025 LibreLane Contributors
#
# Adapted from OpenLane
#
# Copyright 2020-2022 Efabless Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
set_global_connections

set secondary []
foreach vdd $::env(VDD_NETS) gnd $::env(GND_NETS) {
    if { $vdd != $::env(VDD_NET)} {
        lappend secondary $vdd

        set db_net [[ord::get_db_block] findNet $vdd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $vdd]
            $net setSpecial
            $net setSigType "POWER"
        }
    }

    if { $gnd != $::env(GND_NET)} {
        lappend secondary $gnd

        set db_net [[ord::get_db_block] findNet $gnd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $gnd]
            $net setSpecial
            $net setSigType "GROUND"
        }
    }
}

set_voltage_domain -name CORE -power $::env(VDD_NET) -ground $::env(GND_NET) \
    -secondary_power $secondary



if { $::env(PDN_MULTILAYER) == 1 } {

    set arg_list [list]
    if { $::env(PDN_ENABLE_PINS) } {
        lappend arg_list -pins "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
    }

    define_pdn_grid \
        -name stdcell_grid \
        -starts_with POWER \
        -voltage_domain CORE \
        {*}$arg_list

    set arg_list [list]
    append_if_equals arg_list PDN_EXTEND_TO "core_ring" -extend_to_core_ring
    append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_VERTICAL_LAYER) \
        -width $::env(PDN_VWIDTH) \
        -pitch $::env(PDN_VPITCH) \
        -offset $::env(PDN_VOFFSET) \
        -spacing $::env(PDN_VSPACING) \
        -starts_with POWER \
        {*}$arg_list

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_HORIZONTAL_LAYER) \
        -width $::env(PDN_HWIDTH) \
        -pitch $::env(PDN_HPITCH) \
        -offset $::env(PDN_HOFFSET) \
        -spacing $::env(PDN_HSPACING) \
        -starts_with POWER \
        {*}$arg_list

    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
} else {

    set arg_list [list]
    if { $::env(PDN_ENABLE_PINS) } {
        lappend arg_list -pins "$::env(PDN_VERTICAL_LAYER)"
    }

    define_pdn_grid \
        -name stdcell_grid \
        -starts_with POWER \
        -voltage_domain CORE \
        {*}$arg_list

    set arg_list [list]
    append_if_equals arg_list PDN_EXTEND_TO "core_ring" -extend_to_core_ring
    append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_VERTICAL_LAYER) \
        -width $::env(PDN_VWIDTH) \
        -pitch $::env(PDN_VPITCH) \
        -offset $::env(PDN_VOFFSET) \
        -spacing $::env(PDN_VSPACING) \
        -starts_with POWER \
        {*}$arg_list
}

# Adds the standard cell rails if enabled.
if { $::env(PDN_ENABLE_RAILS) == 1 } {
    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_RAIL_LAYER) \
        -width $::env(PDN_RAIL_WIDTH) \
        -followpins

    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_RAIL_LAYER) $::env(PDN_VERTICAL_LAYER)"
}


# Adds the core ring if enabled.
if { $::env(PDN_CORE_RING) == 1 } {
    if { $::env(PDN_MULTILAYER) == 1 } {
        set arg_list [list]
        append_if_flag arg_list PDN_CORE_RING_ALLOW_OUT_OF_DIE -allow_out_of_die
        append_if_flag arg_list PDN_CORE_RING_CONNECT_TO_PADS -connect_to_pads
        append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary
        append_if_exists_argument arg_list PDN_CORE_RING_CONNECT_TO_PAD_LAYERS -connect_to_pad_layers

        set pdn_core_vertical_layer $::env(PDN_VERTICAL_LAYER)
        set pdn_core_horizontal_layer $::env(PDN_HORIZONTAL_LAYER)

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            set pdn_core_vertical_layer $::env(PDN_CORE_VERTICAL_LAYER)
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            set pdn_core_horizontal_layer $::env(PDN_CORE_HORIZONTAL_LAYER)
        }

        add_pdn_ring \
            -grid stdcell_grid \
            -layers "$pdn_core_vertical_layer $pdn_core_horizontal_layer" \
            -widths "$::env(PDN_CORE_RING_VWIDTH) $::env(PDN_CORE_RING_HWIDTH)" \
            -spacings "$::env(PDN_CORE_RING_VSPACING) $::env(PDN_CORE_RING_HSPACING)" \
            -core_offset "$::env(PDN_CORE_RING_VOFFSET) $::env(PDN_CORE_RING_HOFFSET)" \
            {*}$arg_list

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_HORIZONTAL_LAYER) $::env(PDN_VERTICAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] && [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_CORE_HORIZONTAL_LAYER)"
        }

    } else {
        throw APPLICATION "PDN_CORE_RING cannot be used when PDN_MULTILAYER is set to false."
    }
}

# The tile has no horizontal power layer, so nothing above Metal4 can reach the
# SRAM supply columns, and pdngen only joins grids through vias. The core
# stripes are aligned to the columns but pdngen stops them short of each macro.
# After it runs, every stripe end that lines up with a supply column of its own
# net is extended 1 um onto that column. The added metal stays inside the
# column, so it adds no new spacing to the macro's obstructions.
proc v3_bridge_macro_columns {} {
    set block [ord::get_db_block]
    set reach [$block micronsToDbu 1.0]
    foreach inst [$block getInsts] {
        set master [$inst getMaster]
        if { ![$master isBlock] } { continue }
        lassign [$inst getLocation] ix iy
        set top [expr {$iy + [$master getHeight]}]
        set orient [$inst getOrient]
        if { $orient ni {R0 MX} } {
            error "[$inst getName]: unsupported orientation $orient"
        }
        set count 0
        foreach mterm [$master getMTerms] {
            if { [$mterm getSigType] ni {POWER GROUND} } { continue }
            set net [[$inst findITerm [$mterm getName]] getNet]
            if { $net == "NULL" } { continue }
            foreach mpin [$mterm getMPins] {
                foreach box [$mpin getGeometry] {
                    set layer [$box getTechLayer]
                    if { $layer == "NULL" || [$layer getName] != $::env(PDN_VERTICAL_LAYER) } { continue }
                    set x0 [expr {$ix + [$box xMin]}]
                    set x1 [expr {$ix + [$box xMax]}]
                    if { $orient == "R0" } {
                        set y0 [expr {$iy + [$box yMin]}]
                        set y1 [expr {$iy + [$box yMax]}]
                    } else {
                        set y0 [expr {$top - [$box yMax]}]
                        set y1 [expr {$top - [$box yMin]}]
                    }
                    foreach swire [$net getSWires] {
                        foreach wire [$swire getWires] {
                            if { [$wire isVia] || [$wire getTechLayer] != $layer } { continue }
                            if { [$wire xMin] != $x0 || [$wire xMax] != $x1 } { continue }
                            set below [expr {$y0 - [$wire yMax]}]
                            set above [expr {[$wire yMin] - $y1}]
                            if { $y0 == $iy && $below > 0 && $below <= $reach } {
                                odb::dbSBox_create $swire $layer $x0 [$wire yMax] $x1 [expr {$y0 + $reach}] STRIPE
                                incr count
                            } elseif { $y1 == $top && $above > 0 && $above <= $reach } {
                                odb::dbSBox_create $swire $layer $x0 [expr {$y1 - $reach}] $x1 [$wire yMin] STRIPE
                                incr count
                            }
                        }
                    }
                }
            }
        }
        if { $count == 0 } {
            error "[$inst getName]: no power stripe lines up with a supply column"
        }
        puts "\[INFO\] [$inst getName]: extended $count stripe ends onto supply columns."
    }
}

# Stripes stopped by a macro or by a power obstruction are still needed to
# power the cells and macro columns, but the precheck rejects every power pin
# rectangle that does not reach within 10 um of both the top and bottom of the
# die. Keep all of the metal and export only the full-height stripes as pins.
proc v3_keep_spanning_power_pins {} {
    set block [ord::get_db_block]
    set die [$block getDieArea]
    set margin [$block micronsToDbu 10.0]
    foreach netname [concat $::env(VDD_NETS) $::env(GND_NETS)] {
        set bterm [$block findBTerm $netname]
        if { $bterm == "NULL" } { continue }
        set bpins [$bterm getBPins]
        if { [llength $bpins] == 0 } { continue }
        set status [[lindex $bpins 0] getPlacementStatus]
        set spans {}
        set dropped 0
        foreach bpin $bpins {
            foreach box [$bpin getBoxes] {
                if { [$box yMin] - [$die yMin] <= $margin && [$die yMax] - [$box yMax] <= $margin } {
                    lappend spans [list [$box getTechLayer] [$box xMin] [$box yMin] [$box xMax] [$box yMax]]
                } else {
                    incr dropped
                }
            }
        }
        if { [llength $spans] == 0 } {
            error "$netname: no power pin spans the die"
        }
        foreach bpin $bpins {
            odb::dbBPin_destroy $bpin
        }
        set bpin [odb::dbBPin_create $bterm]
        $bpin setPlacementStatus $status
        foreach span $spans {
            lassign $span layer x0 y0 x1 y1
            odb::dbBox_create $bpin $layer $x0 $y0 $x1 $y1
        }
        puts "\[INFO\] $netname: kept [llength $spans] full-height pin shapes, dropped $dropped."
    }
}

# The placement halo cuts the rows just above and below each macro, and the
# power generator will not strap a stripe across a gap with no row, so the
# stripes stop short of the supply columns. Put those row segments back for
# the generator, then remove them again so placement still keeps out of the
# halo. Rows level with a macro body are left alone.
proc v3_restore_halo_rows {} {
    set block [ord::get_db_block]
    set bodies {}
    foreach inst [$block getInsts] {
        if { ![[$inst getMaster] isBlock] } { continue }
        set box [$inst getBBox]
        lappend bodies [list [$box yMin] [$box yMax]]
    }
    set left ""
    array set segments {}
    foreach row [$block getRows] {
        set bbox [$row getBBox]
        if { $left == "" || [$bbox xMin] < $left } { set left [$bbox xMin] }
        lappend segments([$bbox yMin]) $row
    }
    set pending {}
    foreach y [array names segments] {
        set rows $segments($y)
        set first [lindex $rows 0]
        foreach row $rows {
            if { [[$row getBBox] xMin] < [[$first getBBox] xMin] } { set first $row }
        }
        set bbox [$first getBBox]
        if { [$bbox xMin] <= $left } { continue }
        set level 0
        foreach body $bodies {
            lassign $body by0 by1
            if { [$bbox yMin] < $by1 && [$bbox yMax] > $by0 } { set level 1 }
        }
        if { $level } { continue }
        if { [llength $rows] > 1 } {
            error "[$first getName]: the halo splits this row into more than one segment"
        }
        set pitch [expr {([$bbox xMax] - [$bbox xMin]) / [$first getSiteCount]}]
        set missing [expr {[$bbox xMin] - $left}]
        if { $pitch <= 0 || $missing % $pitch != 0 } {
            error "[$first getName]: halo row cannot be extended to the core edge"
        }
        lappend pending [list $first $y [expr {$missing / $pitch}]]
    }
    set count 0
    foreach item $pending {
        lassign $item row y sites
        odb::dbRow_create $block "v3_halo_[$row getName]" [$row getSite] \
            $left $y [$row getOrient] [$row getDirection] $sites [$row getSpacing]
        incr count
    }
    puts "\[INFO\] Restored $count halo rows for the power generator."
}

proc v3_remove_halo_rows {} {
    set block [ord::get_db_block]
    set count 0
    foreach row [$block getRows] {
        if { [string match "v3_halo_*" [$row getName]] } {
            odb::dbRow_destroy $row
            incr count
        }
    }
    puts "\[INFO\] Removed $count temporary halo rows."
}

rename pdngen v3_pdngen
proc pdngen {args} {
    v3_restore_halo_rows
    v3_pdngen {*}$args
    v3_bridge_macro_columns
    v3_keep_spanning_power_pins
    v3_remove_halo_rows
}
