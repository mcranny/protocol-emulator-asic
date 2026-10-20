// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// One protocol pin: a single pending-commit slot plus input edge timestamping.
//
// The whole V3 time-cursor architecture rests on this slot. A commit whose
// deadline is at or before `now` applies this edge; a commit due in the future
// latches into the one-deep slot, and the owning engine must treat the slot as
// busy (`slot_free` low) until it fires. The signed distance `commit_due - now`
// is interpreted as a two's-complement 24-bit number, so the ordering is
// correct across the free-running timebase wrap as long as the true distance
// stays inside +/-2**23 ticks.
module v3_pin_cell (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,        // low releases the pin and clears the slot
    input  wire [23:0] now,
    input  wire        open_drain,
    input  wire        commit,        // one-cycle strobe from the owning engine
    input  wire [23:0] commit_due,
    input  wire        commit_value,
    input  wire        commit_oe,
    input  wire        clear_missed,
    input  wire        pin_in,        // raw pad input
    output wire        slot_free,     // engine stalls on commit when this is low
    output wire        pin_out,
    output wire        pin_oe,
    output reg         missed,
    output reg  [23:0] edge_time,
    output reg         edge_polarity,
    output wire        level          // synchronized, filtered input level
);
    // Applied output state.
    reg value_r;
    reg oe_r;

    // The single pending commit.
    reg        slot_occupied;
    reg [23:0] slot_due;
    reg        slot_value;
    reg        slot_oe;

    // Input synchronizer. pin_in is already glitch-filtered upstream (the
    // separate input-filter module); here it is only brought into the clock
    // domain before edge timestamping.
    reg sync_meta;
    reg sync_level;

    // Signed distances. `commit_due - now` is a 24-bit modular subtraction;
    // $signed interprets the result as two's complement to recover the signed
    // tick distance, so the compare is never unsigned.
    wire slot_reached = ($signed(slot_due - now) <= 0);
    wire commit_now   = ($signed(commit_due - now) <= 0);
    wire commit_late  = ($signed(commit_due - now) < 0);

    // Output scheduling and the pending slot.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            value_r       <= 1'b0;
            oe_r          <= 1'b0;
            slot_occupied <= 1'b0;
            slot_due      <= 24'd0;
            slot_value    <= 1'b0;
            slot_oe       <= 1'b0;
            missed        <= 1'b0;
        end else if (!enable) begin
            // Release: drop the drive, empty the slot and clear the flag. The
            // last driven value is retained but no longer presented (pin_oe
            // is gated low below while disabled).
            oe_r          <= 1'b0;
            slot_occupied <= 1'b0;
            missed        <= 1'b0;
        end else begin
            // Fire the slot the cycle its deadline is reached or passed.
            if (slot_occupied && slot_reached) begin
                value_r       <= slot_value;
                oe_r          <= slot_oe;
                slot_occupied <= 1'b0;
            end
            // Accept a commit only while the slot is free: an occupied slot is
            // the engine's interlock and is never overwritten. (fire and accept
            // are mutually exclusive: one needs the slot occupied, the other
            // free.)
            if (commit && !slot_occupied) begin
                if (commit_now) begin
                    value_r <= commit_value;
                    oe_r    <= commit_oe;
                end else begin
                    slot_occupied <= 1'b1;
                    slot_due      <= commit_due;
                    slot_value    <= commit_value;
                    slot_oe       <= commit_oe;
                end
            end
            // Missed is sticky. Clear first; a strictly past-due immediate
            // commit then re-sets it this edge. An exactly-due commit never
            // sets it.
            if (clear_missed)
                missed <= 1'b0;
            if (commit && !slot_occupied && commit_late)
                missed <= 1'b1;
        end
    end

    // Input path: synchronize the pad and timestamp every level change. This
    // runs independently of `enable`, matching model.PinCell.observe which has
    // no enable.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sync_meta     <= 1'b0;
            sync_level    <= 1'b0;
            edge_time     <= 24'd0;
            edge_polarity <= 1'b0;
        end else begin
            sync_meta  <= pin_in;
            sync_level <= sync_meta;
            if (sync_meta != sync_level) begin
                edge_time     <= now;
                edge_polarity <= sync_meta;
            end
        end
    end

    assign slot_free = !slot_occupied;
    assign level     = sync_level;

    // Open-drain pins never actively drive high: a high value releases the pad
    // and lets the external pull-up define the level. pin_oe is additionally
    // gated low whenever the cell is disabled or in reset so a release is
    // visible on the pad the same cycle.
    assign pin_out = open_drain ? 1'b0 : value_r;
    assign pin_oe  = (enable && rst_n)
                     ? (open_drain ? (value_r ? 1'b0 : oe_r) : oe_r)
                     : 1'b0;

`ifdef FORMAL
    // Proof bookkeeping only: these registers exist under FORMAL and do not
    // appear in the synthesized cell.
    reg valid;
    reg [23:0] latched_distance;  // signed distance captured when the slot latched
    reg [23:0] cycles_occupied;   // cycles the slot has been continuously occupied
    initial valid = 1'b0;

    // Ghost counters tracking the occupied slot, used only for bounded
    // liveness. They follow exactly the slot transitions of the main block.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            latched_distance <= 24'd0;
            cycles_occupied  <= 24'd0;
        end else if (!enable) begin
            cycles_occupied <= 24'd0;
        end else if (slot_occupied && slot_reached) begin
            cycles_occupied <= 24'd0;                    // fires this edge
        end else if (commit && !slot_occupied && !commit_now) begin
            latched_distance <= commit_due - now;        // latch (distance > 0)
            cycles_occupied  <= 24'd1;
        end else if (slot_occupied) begin
            cycles_occupied <= cycles_occupied + 24'd1;  // held
        end else begin
            cycles_occupied <= 24'd0;                    // free
        end
    end

    always @(posedge clk) begin
        valid <= 1'b1;
        // Start every trace in reset, and model the free-running timebase.
        if (!valid) assume (!rst_n);
        if (valid)  assume (now == $past(now) + 24'd1);

        // Combinational safety invariants hold every cycle.
        assert (slot_free == !slot_occupied);
        assert (!open_drain || !(pin_out && pin_oe));          // Open drain
        assert ((enable && rst_n) || !pin_oe);                 // Release

        if (valid && rst_n && $past(rst_n)) begin
            // Never early: an occupied slot with a strictly future deadline
            // last cycle did not change the output this cycle.
            if ($past(enable) && $past(slot_occupied) &&
                ($signed($past(slot_due) - $past(now)) > 0))
                assert (value_r == $past(value_r) && oe_r == $past(oe_r));

            // Fires on time: once the deadline is reached, the output takes the
            // slot value and the slot frees. Covers the exactly-due boundary.
            if ($past(enable) && $past(slot_occupied) &&
                ($signed($past(slot_due) - $past(now)) <= 0)) begin
                assert (value_r == $past(slot_value) && oe_r == $past(slot_oe));
                assert (!slot_occupied);
            end
            // Exactly-due restatement, matching the property table literally.
            if ($past(enable) && $past(slot_occupied) &&
                ($past(slot_due) == $past(now)))
                assert (value_r == $past(slot_value) && oe_r == $past(slot_oe));

            // Past-due applies immediately and flags.
            if ($past(enable) && $past(commit) && !$past(slot_occupied) &&
                ($signed($past(commit_due) - $past(now)) < 0)) begin
                assert (value_r == $past(commit_value) && oe_r == $past(commit_oe));
                assert (missed);
            end

            // Exactly-due does not set missed: the only change allowed is a
            // concurrent clear.
            if ($past(enable) && $past(commit) && !$past(slot_occupied) &&
                ($past(commit_due) == $past(now)))
                assert (missed == ($past(clear_missed) ? 1'b0 : $past(missed)));

            // No overwrite: a slot that has not reached its deadline is held
            // bit-for-bit.
            if ($past(enable) && $past(slot_occupied) &&
                ($signed($past(slot_due) - $past(now)) > 0)) begin
                assert (slot_occupied);
                assert (slot_due   == $past(slot_due));
                assert (slot_value == $past(slot_value));
                assert (slot_oe    == $past(slot_oe));
            end

            // Missed is sticky: it only clears on clear_missed, disable or reset.
            if ($past(missed) && !missed)
                assert ($past(clear_missed) || !$past(enable) ||
                        !$past(rst_n) || !rst_n);

            // Bounded liveness: an occupied slot latched with distance d is free
            // within d + 1 cycles. The ghost counter is tied to the real
            // remaining distance so the bound is inductive.
            if (slot_occupied) begin
                assert ((slot_due - now) == (latched_distance - cycles_occupied));
                assert ($signed(slot_due - now) >= 0);
                assert (cycles_occupied <= latched_distance + 24'd1);
            end
        end
    end
`endif
endmodule
`default_nettype wire
