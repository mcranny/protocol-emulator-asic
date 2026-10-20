// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// V3 host SPI transport: mode 0, MSB first, frames of exactly 48 clocks.
//
// A frame is {cmd[7:0], addr[7:0], payload[31:0]}, first bit at bit 47.
// SCK, CS_n and MOSI pass through two synchronizer flops, then SCK rising
// edges are counted while the synchronized CS_n is low. The frame is
// accepted only when the count is exactly 48; any other count raises
// frame_error for one clock and discards the command.
//
// The response to a frame is shifted out during the following frame: the
// reply word is captured one clock after cmd_valid and held until the next
// frame. A rejected frame answers with the reject word (REJECT_BIT in cmd,
// everything else zero) on the following frame. Before any frame has been
// received the response is zero.
//
// MISO is gated by the raw cs_n, so the pin is released as soon as the host
// deselects rather than a synchronizer delay later.
//
// Timing the host must respect, in system clocks:
//   * SCK high and low phases of at least 2 clocks, and at least 3 clocks
//     from SCK falling to the next SCK rising so the next MISO bit has
//     settled. Keep SCK at or below clk/4 for margin against the
//     unsynchronized inputs.
//   * CS_n high for at least 4 clocks between frames, so the response is
//     loaded before MISO is enabled again.
//   * SCK rising edges at least 2 clocks after CS_n falls and at least 2
//     clocks before it rises. An edge that lands on the synchronized
//     select/deselect is not counted, so the frame is rejected.
module v3_host_spi (
    input  wire        clk, rst_n, ena,
    input  wire        sck, cs_n, mosi,
    output wire        miso,
    output reg         cmd_valid,
    output reg         frame_error,
    output reg  [7:0]  cmd, addr,
    output reg  [31:0] payload,
    // The accepted frame, exposed one clock before cmd_valid. early_valid
    // pulses on the clock the 48-bit frame is accepted (the clock cmd_valid is
    // scheduled), carrying the command and address from the receive shift
    // register. The top uses it to pre-fetch a READ's program word so the
    // registered memory output is ready by the time cmd_valid captures the
    // reply. It changes nothing in this module's own response path.
    output wire        early_valid,
    output wire [7:0]  early_cmd,
    output wire [7:0]  early_addr,
    // Response word for the command being delivered; sampled while
    // cmd_valid is high.
    input  wire [47:0] reply
);
    localparam [5:0] FRAME_BITS = 6'd48;
    // Counting stops one past a legal frame; every count above 48 rejects.
    localparam [5:0] COUNT_LIMIT = 6'd49;
    localparam [47:0] REJECT_WORD = 48'h8000_0000_0000;

    (* async_reg = "true" *) reg [1:0] sck_sync, cs_sync, mosi_sync;
    reg sck_prev, cs_prev;
    reg [5:0] bit_count;
    reg [47:0] rx_shift, tx_shift;

    assign miso = rst_n && ena && !cs_n ? tx_shift[47] : 1'b0;

    // A 48-bit frame is accepted on the clock the synchronized CS_n rises; that
    // is the clock cmd_valid is scheduled for the next edge, so this pulses one
    // clock early. The command and address are taken straight from the receive
    // shift register, which already holds the completed frame.
    assign early_valid = !cs_prev && cs_sync[1] && (bit_count == FRAME_BITS);
    assign early_cmd   = rx_shift[47:40];
    assign early_addr  = rx_shift[39:32];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sck_sync <= 0; cs_sync <= 3; mosi_sync <= 0;
            sck_prev <= 0; cs_prev <= 1;
            bit_count <= 0;
            rx_shift <= 0; tx_shift <= 0;
            cmd_valid <= 0; frame_error <= 0;
            cmd <= 0; addr <= 0; payload <= 0;
        end else if (!ena) begin
            sck_sync <= 0; cs_sync <= 3; mosi_sync <= 0;
            sck_prev <= 0; cs_prev <= 1;
            bit_count <= 0;
            rx_shift <= 0; tx_shift <= 0;
            cmd_valid <= 0; frame_error <= 0;
            cmd <= 0; addr <= 0; payload <= 0;
        end else begin
            sck_sync <= {sck_sync[0], sck};
            cs_sync <= {cs_sync[0], cs_n};
            mosi_sync <= {mosi_sync[0], mosi};
            sck_prev <= sck_sync[1];
            cs_prev <= cs_sync[1];
            cmd_valid <= 0;
            frame_error <= 0;
            // The reply is the core's pre-execution snapshot for the command
            // presented this clock.
            if (cmd_valid) tx_shift <= reply;
            if (cs_prev && !cs_sync[1]) begin
                rx_shift <= 0;
                bit_count <= 0;
            end else if (!cs_prev && cs_sync[1]) begin
                if (bit_count == FRAME_BITS) begin
                    {cmd, addr, payload} <= rx_shift;
                    cmd_valid <= 1;
                end else begin
                    frame_error <= 1;
                    tx_shift <= REJECT_WORD;
                end
            end else if (!cs_sync[1]) begin
                if (!sck_prev && sck_sync[1]) begin
                    rx_shift <= {rx_shift[46:0], mosi_sync[1]};
                    if (bit_count < COUNT_LIMIT) bit_count <= bit_count + 1'b1;
                end
                if (sck_prev && !sck_sync[1]) tx_shift <= {tx_shift[46:0], 1'b0};
            end
        end
    end

`ifdef FORMAL
    // Proof bookkeeping only; none of this exists in the synthesized design.
    wire ok = rst_n && ena;

    // The first sampled clock is a reset.
    reg started;
    initial started = 0;
    always @(posedge clk) begin
        started <= 1;
        if (!started) assume(!rst_n);
    end

    // Consecutive previous clocks with the module out of reset and enabled.
    reg [3:0] hist;
    initial hist = 0;
    always @(posedge clk) hist <= ok ? {hist[2:0], 1'b1} : 4'b0;

    // Environment: CS_n stays high for at least 4 clocks after it rises.
    reg cs_q;
    reg [2:0] hi_run;
    initial begin cs_q = 1; hi_run = 0; end
    always @(posedge clk) begin
        cs_q <= cs_n;
        if (cs_n && !cs_q) hi_run <= 1;
        else if (cs_n && hi_run != 0 && hi_run < 4) hi_run <= hi_run + 1'b1;
        else if (!cs_n) hi_run <= 0;
        if (hi_run != 0 && hi_run < 4) assume(cs_n);
    end

    // Ghost SCK rising-edge count since the frame started, and the frame
    // outcome one clock after the deselect is seen.
    reg [7:0] g_count, g_count_q, g_fall;
    reg g_end_q;
    reg [47:0] g_resp;
    initial begin g_count = 0; g_count_q = 0; g_fall = 0; g_end_q = 0; g_resp = 0; end
    wire g_start = cs_prev && !cs_sync[1];
    wire g_end   = !cs_prev && cs_sync[1];
    wire g_sel   = !cs_prev && !cs_sync[1];
    wire g_rise  = g_sel && !sck_prev && sck_sync[1];
    wire g_fell  = g_sel && sck_prev && !sck_sync[1];
    always @(posedge clk) begin
        if (!ok) begin
            g_count <= 0; g_count_q <= 0; g_fall <= 0; g_end_q <= 0; g_resp <= 0;
        end else begin
            g_end_q <= g_end;
            g_count_q <= g_count;
            if (g_start) begin g_count <= 0; g_fall <= 0; end
            else begin
                if (g_rise && g_count != 8'hFF) g_count <= g_count + 1'b1;
                if (g_fell && g_fall != 8'hFF) g_fall <= g_fall + 1'b1;
            end
            // The response owed to the next frame: the reply for an accepted
            // command, or the reject word for a rejected frame.
            if (cmd_valid) g_resp <= reply;
            else if (frame_error) g_resp <= REJECT_WORD;
        end
    end
    wire [47:0] g_tx = g_resp << g_fall;

    always @(posedge clk) begin
        // Two-flop synchronization: each input reaches its second flop two
        // clocks after it is sampled, with no logic in between.
        if (ok && hist[0]) begin
            assert(sck_sync[0] == $past(sck));
            assert(cs_sync[0]  == $past(cs_n));
            assert(mosi_sync[0] == $past(mosi));
        end
        if (ok && hist[1]) begin
            assert(sck_sync[1] == $past(sck, 2));
            assert(cs_sync[1]  == $past(cs_n, 2));
            assert(mosi_sync[1] == $past(mosi, 2));
        end
        if (ok && hist[2]) begin
            assert(sck_prev == $past(sck, 3));
            assert(cs_prev  == $past(cs_n, 3));
        end

        // MISO is gated by the raw CS_n, with no synchronizer delay.
        if (cs_n) assert(!miso);
        if (ok && !cs_n) assert(miso == tx_shift[47]);

        if (ok) begin
            // The counter follows the synchronized SCK edges, saturating one
            // past a legal frame.
            assert(bit_count == (g_count > 8'd49 ? 8'd49 : g_count));
            assert(!(cmd_valid && frame_error));
            // A command or error is only reported on the clock after the
            // synchronized CS_n rises, never while a frame is selected.
            if (cmd_valid || frame_error) assert(g_end_q && cs_prev && cs_sync[1]);
            assert(cmd_valid   == (g_end_q && g_count_q == 8'd48));
            assert(frame_error == (g_end_q && g_count_q != 8'd48));
            // Exact-48 acceptance; rejection at 47 and at 49.
            if (g_end_q && g_count_q == 8'd48) assert(cmd_valid && !frame_error);
            if (g_end_q && g_count_q == 8'd47) assert(frame_error && !cmd_valid);
            if (g_end_q && g_count_q == 8'd49) assert(frame_error && !cmd_valid);
        end

        // Response timing. Once loaded, tx_shift holds the response owed to
        // the following frame; while selected it only shifts that response
        // out with zeros behind it, so the frame's own bits never reach MISO.
        if (ok && hist[0] && $past(cmd_valid)) assert(tx_shift == $past(reply));
        if (ok && hist[0] && $past(frame_error)) assert(tx_shift == REJECT_WORD);
        if (ok && cs_prev && cs_sync[1] && !cmd_valid && !frame_error)
            assert(tx_shift == g_resp);
        if (ok && g_sel) assert(tx_shift == g_tx);
    end
`endif
endmodule
