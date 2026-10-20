`default_nettype none

// The single time reference. Everything that schedules, waits, timestamps or
// captures reads this counter; nothing else keeps its own notion of time.
//
// At 48 MHz the 24-bit counter wraps every 349.5 ms. Deadline comparisons use
// a signed distance so the wrap is invisible to firmware as long as a deadline
// stays inside +/-174.8 ms of the present. The epoch extends the timestamp to
// 32 bits for capture and host readout, covering about 89 seconds.
module v3_timebase (
    input  wire        clk,
    input  wire        rst_n,
    output reg  [23:0] now,
    output reg  [7:0]  epoch
);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            now   <= 24'd0;
            epoch <= 8'd0;
        end else begin
            now <= now + 24'd1;
            if (&now) epoch <= epoch + 8'd1;
        end
    end

`ifdef FORMAL
    reg started = 1'b0;
    always @(posedge clk) started <= 1'b1;

    always @(posedge clk) if (started && rst_n && $past(rst_n)) begin
        // The counter only ever advances by one.
        assert(now == $past(now) + 24'd1);
        // The epoch advances exactly when the counter wraps.
        if ($past(&now)) assert(epoch == $past(epoch) + 8'd1);
        else             assert(epoch == $past(epoch));
    end
`endif

endmodule

`default_nettype wire
