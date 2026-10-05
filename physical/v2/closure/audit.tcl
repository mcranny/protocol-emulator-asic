# Invoked by the pinned corner.tcl after reading the ODB and estimating RC.
# Fail instead of silently substituting placement estimates for global routes.
if {![grt::have_routes]} {
    error "Closure timing requires saved global routes"
}
if {[llength [lln::get_corner_names]] != 1} {
    error "Closure timing requires exactly one corner per process"
}
set clocks [all_clocks]
if {[llength $clocks] != 1} {
    error "Expected exactly one V2 clock"
}
foreach clock $clocks {
    if {![get_property $clock is_propagated]} {
        error "Closure timing requires a propagated clock"
    }
    if {[get_property $clock period] != 40.0} {
        error "Closure timing requires a 40 ns clock"
    }
}
write_metric_int "closure__global_route_parasitics" 1
write_metric_int "closure__propagated_clock" 1
