// SPDX-License-Identifier: Apache-2.0
//
// V3 programmable engine: fetch pipeline, cursor, registers, pin commits,
// inputs, queues, events and CRC. One engine over eight shared pins.
//
// The program memory, pin cells, byte/record queues and the CRC unit are
// external; the engine talks to them over the interfaces below.
//
// The fetch is registered: the address on `fetch_addr` delivers its word and
// written bit on `fetch_data`/`fetch_written` the next cycle. A taken branch
// inserts a one-cycle bubble, so it costs exactly two cycles.
`default_nettype none
module v3_engine (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        ena,

    input  wire [23:0] now,          // shared free-running timebase
    input  wire        start,        // begin execution at address 0
    input  wire        stop,         // halt fetching without a fault
    input  wire        program_valid,// every program word has been written

    input  wire [7:0]  owner,        // pins this engine may commit to
    input  wire [7:0]  open_drain,   // open-drain mask (recorded only)

    // Program fetch interface (registered read, one cycle late).
    output wire [7:0]  fetch_addr,
    input  wire [31:0] fetch_data,
    input  wire        fetch_written,

    // Per-pin commit interface. A commit strobe schedules (value, oe) at
    // `commit_due` on the external pin cell. `slot_free[i]` is low while pin i
    // already holds a pending commit.
    output wire [7:0]  commit,
    output wire [7:0]  commit_value,
    output wire [7:0]  commit_oe,
    output wire [23:0] commit_due,
    input  wire [7:0]  slot_free,

    // Per-pin inputs from the external pin cells.
    input  wire [7:0]  pin_level,       // engine-visible (filtered) levels
    input  wire [7:0]  pin_drv_value,   // currently driven value per pin
    input  wire [7:0]  pin_drv_oe,      // currently driven oe per pin
    input  wire [7:0]  pin_missed,      // sticky missed-deadline per pin
    input  wire [191:0] pin_edge_time,  // last filtered edge timestamp per pin
    input  wire [7:0]  pin_edge_polarity,
    output wire [7:0]  clear_missed,    // clear missed flag on these pins

    // Edge watchers (set before start). Two watchers drive events[1:0].
    input  wire        watch0_en,
    input  wire [7:0]  watch0_mask,
    input  wire [7:0]  watch0_value,
    input  wire [7:0]  watch0_edges,
    input  wire        watch1_en,
    input  wire [7:0]  watch1_mask,
    input  wire [7:0]  watch1_value,
    input  wire [7:0]  watch1_edges,
    input  wire [7:0]  abort_mask,      // which events abort WAITEDGE/WAITFOR

    // TX byte queue (external).
    input  wire [4:0]  tx_level,
    input  wire [7:0]  tx_data,         // oldest byte, valid when tx_level != 0
    output wire        tx_pull,

    // RX record queue (external).
    input  wire [4:0]  rx_level,
    output wire        rx_push,
    output wire [7:0]  rx_value,
    output wire [7:0]  rx_meta,

    // Shared CRC unit (same-cycle operations).
    output wire        crc_cfg,
    output wire [15:0] crc_poly,
    output wire        crc_msb_first,
    output wire        crc_load,
    output wire [15:0] crc_seed,
    output wire        crc_bit,
    output wire        crc_bit_value,
    input  wire [15:0] crc_value,

    // Status.
    output wire        running,
    output wire        halted,
    output wire [15:0] errors,
    output wire [7:0]  fault_code,
    output wire [23:0] cursor,
    output wire [7:0]  pc,
    output wire [7:0]  marker,

    // Debug observability for the differential harness.
    output wire [23:0] dbg_r0,
    output wire [23:0] dbg_r1,
    output wire [23:0] dbg_r2,
    output wire [23:0] dbg_r3,
    output wire [7:0]  dbg_isr,
    output wire [7:0]  dbg_events,
    output wire [1:0]  dbg_link_depth,
    output wire        dbg_stalled
);
    `include "v3_opcodes.vh"

    localparam [15:0] E_ILLEGAL     = 16'h0001;
    localparam [15:0] E_INVALID_WORD= 16'h0002;
    localparam [15:0] E_STACK       = 16'h0004;
    localparam [15:0] E_OWNERSHIP   = 16'h0008;
    localparam [15:0] E_FIRMWARE    = 16'h0010;
    localparam [15:0] E_QUEUE       = 16'h0020;

    localparam [23:0] MASK = 24'hFFFFFF;

    // -- state ----------------------------------------------------------
    reg        running_r, halted_r, stalled_r;
    reg [15:0] errors_r;
    reg [7:0]  fault_code_r;
    reg [7:0]  pc_r;
    reg [23:0] cursor_r;
    reg [23:0] regfile [0:3];
    reg [7:0]  isr_r;
    reg [7:0]  marker_r;
    reg        f_timeout, f_interrupted, f_zero, f_carry;
    reg [7:0]  events_r;
    reg [7:0]  previous_r;
    reg [7:0]  link0_r, link1_r;
    reg [1:0]  link_depth_r;
    reg        bubble_r;
    reg        pull_empty_r;
    reg        waitedge_active_r;
    reg [23:0] waitedge_baseline_r;

    // -- current instruction --------------------------------------------
    wire [31:0] word = fetch_data;
    wire [5:0]  op   = word[31:26];
    wire        is_legal = legal(word);

    // operand field slices
    wire [7:0]  f_target   = word[7:0];
    wire [3:0]  f_cond     = word[11:8];
    wire [1:0]  f_djnz_reg = word[9:8];
    wire [1:0]  f_li_reg   = word[1:0];
    wire [23:0] f_li_val   = word[25:2];
    wire [1:0]  f_dst      = word[1:0];
    wire [1:0]  f_src      = word[3:2];
    wire [4:0]  f_amount   = word[6:2];
    wire [1:0]  f_breq_reg = word[9:8];
    wire [7:0]  f_breq_val = word[17:10];
    wire [23:0] f_ticks    = word[23:0];
    wire [1:0]  f_reg      = word[1:0];
    wire [7:0]  f_set_mask = word[7:0];
    wire [7:0]  f_set_val  = word[15:8];
    wire [9:0]  f_set_off  = word[25:16];
    wire [7:0]  f_oe_mask  = word[7:0];
    wire [7:0]  f_oe_oe    = word[15:8];
    wire [9:0]  f_oe_off   = word[25:16];
    wire [7:0]  f_drv_val  = word[7:0];
    wire [7:0]  f_drv_oe   = word[15:8];
    wire [9:0]  f_drv_off  = word[25:16];
    wire [2:0]  f_out_pin  = word[2:0];
    wire        f_out_lvl  = word[3];
    wire [9:0]  f_out_off  = word[13:4];
    wire [2:0]  f_in_pin   = word[2:0];
    wire        f_in_msb   = word[3];
    wire [2:0]  f_et_pin   = word[2:0];
    wire [1:0]  f_et_reg   = word[4:3];
    wire [2:0]  f_we_pin   = word[2:0];
    wire [1:0]  f_we_pol   = word[4:3];
    wire [2:0]  f_wf_pin   = word[2:0];
    wire        f_wf_lvl   = word[3];
    wire [7:0]  f_rx_meta  = word[7:0];
    wire [7:0]  f_mark     = word[7:0];
    wire [7:0]  f_wev_mask = word[7:0];
    wire [7:0]  f_cev_mask = word[7:0];
    wire [7:0]  f_cm_mask  = word[7:0];
    wire [15:0] f_crc_poly = word[15:0];
    wire        f_crc_msb  = word[16];
    wire [7:0]  f_fail     = word[7:0];

    // -- signed-distance helpers (24-bit modular) -----------------------
    function future;            // signed_distance(due, nowv) > 0
        input [23:0] due; input [23:0] nowv; reg [23:0] d;
        begin d = (due - nowv) & MASK; future = (d != 0) && (d[23] == 1'b0); end
    endfunction
    function passed_or_now;     // signed_distance(due, nowv) <= 0
        input [23:0] due; input [23:0] nowv; reg [23:0] d;
        begin d = (due - nowv) & MASK; passed_or_now = (d == 0) || d[23]; end
    endfunction
    function fresh;             // signed_distance(a, b) >= 0
        input [23:0] a; input [23:0] b; reg [23:0] d;
        begin d = (a - b) & MASK; fresh = (d < 24'h800000); end
    endfunction

    // -- edge watchers and events ---------------------------------------
    wire [7:0] pins_byte = pin_level;
    wire w0_hit = watch0_en && ((pins_byte & watch0_mask) == watch0_value)
                  && (|((pins_byte ^ previous_r) & watch0_edges));
    wire w1_hit = watch1_en && ((pins_byte & watch1_mask) == watch1_value)
                  && (|((pins_byte ^ previous_r) & watch1_edges));
    wire [7:0] watcher_newbits = {6'b0, w1_hit, w0_hit};
    wire [7:0] events_live = events_r | watcher_newbits;

    // -- ALU ------------------------------------------------------------
    wire [23:0] rd_dst = regfile[f_dst];
    wire [23:0] rd_src = regfile[f_src];
    wire [24:0] add_res = {1'b0, rd_dst} + {1'b0, rd_src};
    wire [24:0] sub_res = {1'b0, rd_dst} - {1'b0, rd_src};
    wire [24:0] shl_res = {1'b0, rd_dst} << f_amount;
    wire [23:0] shr_res = (f_amount >= 5'd24) ? 24'd0 : (rd_dst >> f_amount);
    wire        shr_carry = (f_amount == 5'd0) ? 1'b0
                            : (((rd_dst >> (f_amount - 5'd1)) & 24'd1) != 24'd0);
    wire [23:0] djnz_dec = (regfile[f_djnz_reg] - 24'd1) & MASK;

    // -- commit decode --------------------------------------------------
    reg        is_commit;
    reg [7:0]  ctarget, crequire, cval, coe;
    reg [9:0]  coff;
    reg        cfault_extra;
    always @* begin
        is_commit = 1'b0; ctarget = 8'b0; crequire = 8'b0;
        cval = 8'b0; coe = 8'b0; coff = 10'b0; cfault_extra = 1'b0;
        case (op)
            `OP_SET: begin
                is_commit = 1'b1; ctarget = f_set_mask; crequire = f_set_mask;
                cval = f_set_val; coe = pin_drv_oe; coff = f_set_off;
            end
            `OP_OE: begin
                is_commit = 1'b1; ctarget = f_oe_mask; crequire = f_oe_mask;
                cval = pin_drv_value; coe = f_oe_oe; coff = f_oe_off;
            end
            `OP_DRIVE: begin
                is_commit = 1'b1; ctarget = owner; crequire = 8'b0;
                cval = f_drv_val; coe = f_drv_oe; coff = f_drv_off;
                cfault_extra = (|(f_drv_oe & ~owner));
            end
            `OP_OUT: begin
                is_commit = 1'b1; ctarget = (8'b1 << f_out_pin);
                crequire = (8'b1 << f_out_pin);
                cval = ({7'b0, f_out_lvl} << f_out_pin);
                coe = (8'b1 << f_out_pin); coff = f_out_off;
            end
            default: ;
        endcase
    end
    wire commit_fault = is_commit && ((|(crequire & ~owner)) || cfault_extra);
    wire commit_stall = is_commit && (|(ctarget & ~slot_free));
    wire [23:0] commit_due_w = (cursor_r + {14'b0, coff}) & MASK;

    // -- wait resolution ------------------------------------------------
    wire [23:0] we_baseline = waitedge_active_r ? waitedge_baseline_r : now;
    wire [23:0] we_edge     = pin_edge_time[24*f_we_pin +: 24];
    wire        we_fresh    = fresh(we_edge, we_baseline);
    wire        we_pol_ok   = (f_we_pol == 2'd0) ? (pin_edge_polarity[f_we_pin] == 1'b1)
                            : (f_we_pol == 2'd1) ? (pin_edge_polarity[f_we_pin] == 1'b0)
                            : 1'b1;
    wire        we_matched  = we_fresh && we_pol_ok;
    wire        wf_matched  = (pin_level[f_wf_pin] == f_wf_lvl);
    wire        wait_aborted = (|(events_live & abort_mask));
    wire        wait_timeout = passed_or_now(cursor_r, now);
    wire        wev_match    = (|(events_live & f_wev_mask));
    wire        wev_timeout  = passed_or_now(cursor_r, now);

    // -- branch condition -----------------------------------------------
    reg cond_true;
    always @* begin
        case (f_cond)
            4'd0: cond_true = f_timeout;
            4'd1: cond_true = f_interrupted;
            4'd2: cond_true = (|pin_missed);
            4'd3: cond_true = (tx_level == 5'd0);
            4'd4: cond_true = (rx_level == 5'd16);
            4'd5: cond_true = pull_empty_r;
            4'd6: cond_true = events_live[0];
            4'd7: cond_true = events_live[1];
            4'd8: cond_true = f_zero;
            4'd9: cond_true = f_carry;
            default: cond_true = 1'b0;
        endcase
    end

    // -- control decision -----------------------------------------------
    reg        do_stall, do_fault, do_redirect, do_halt;
    reg [15:0] fbits;
    reg [7:0]  btarget;
    always @* begin
        do_stall = 1'b0; do_fault = 1'b0; do_redirect = 1'b0; do_halt = 1'b0;
        fbits = 16'b0; btarget = f_target;
        case (op)
            `OP_NOP: ;
            `OP_HALT: do_halt = 1'b1;
            `OP_JMP: do_redirect = 1'b1;
            `OP_BRC: do_redirect = cond_true;
            `OP_DJNZ: do_redirect = (djnz_dec != 24'd0);
            `OP_CALL: begin
                if (link_depth_r == 2'd2) begin do_fault = 1'b1; fbits = E_STACK; end
                else do_redirect = 1'b1;
            end
            `OP_RET: begin
                if (link_depth_r == 2'd0) begin do_fault = 1'b1; fbits = E_STACK; end
                else begin
                    do_redirect = 1'b1;
                    btarget = (link_depth_r == 2'd2) ? link1_r : link0_r;
                end
            end
            `OP_FAIL: do_halt = 1'b1;
            `OP_BREQ: do_redirect = (regfile[f_breq_reg] == {16'b0, f_breq_val});
            `OP_WAITTIME: do_stall = future(cursor_r, now);
            `OP_SET, `OP_OE, `OP_DRIVE, `OP_OUT: begin
                if (commit_fault) begin do_fault = 1'b1; fbits = E_OWNERSHIP; end
                else if (commit_stall) do_stall = 1'b1;
            end
            `OP_WAITEDGE: do_stall = !(wait_aborted || we_matched || wait_timeout);
            `OP_WAITFOR:  do_stall = !(wait_aborted || wf_matched || wait_timeout);
            `OP_PULL: do_stall = (tx_level == 5'd0);
            `OP_RX: if (rx_level == 5'd16) begin do_fault = 1'b1; fbits = E_QUEUE; end
            `OP_WAITEVENT: do_stall = !(wev_match || wev_timeout);
            default: ; // single-cycle register/time/input/crc ops
        endcase
    end

    // -- execution gating ------------------------------------------------
    wire exec       = running_r && ena && !bubble_r && !stop;
    wire exec_valid = exec && is_legal && fetch_written;
    wire retire     = exec_valid && !do_stall && !do_fault;
    wire taken_branch = retire && do_redirect;
    wire ret_empty    = exec_valid && (op == `OP_RET) && (link_depth_r == 2'd0);
    wire f_active     = rst_n && ena && running_r && !stop;

    // open_drain is reserved for a future open-drain commit path and is not yet
    // consumed by the datapath; taken_branch/ret_empty/f_active drive the
    // FORMAL assertions only. Sink them so a plain -Wall build stays clean.
    wire _unused_eng = &{1'b0, open_drain, taken_branch, ret_empty, f_active};

    // -- external output strobes ----------------------------------------
    assign commit        = (retire && is_commit) ? ctarget : 8'b0;
    assign commit_value  = cval;
    assign commit_oe     = coe;
    assign commit_due    = commit_due_w;
    assign tx_pull       = retire && ((op == `OP_PULL) ||
                                      (op == `OP_PULLNB && tx_level != 5'd0));
    assign rx_push       = retire && (op == `OP_RX);
    assign rx_value      = isr_r;
    assign rx_meta       = f_rx_meta;
    assign crc_cfg       = retire && (op == `OP_CRCCFG);
    assign crc_poly      = f_crc_poly;
    assign crc_msb_first = f_crc_msb;
    assign crc_load      = retire && (op == `OP_CRCLOAD);
    assign crc_seed      = regfile[f_reg][15:0];
    assign crc_bit       = retire && (op == `OP_CRCBIT);
    assign crc_bit_value = regfile[f_reg][0];
    assign clear_missed  = (retire && op == `OP_CLEARMISS) ? (f_cm_mask & owner) : 8'b0;

    // -- prefetch address (registered read one cycle late) --------------
    assign fetch_addr =
        !running_r ? pc_r :
        bubble_r   ? pc_r :
        do_stall   ? pc_r :
        do_redirect? btarget :
                     (pc_r + 8'd1);

    // -- events next-state ----------------------------------------------
    wire [7:0] events_next = (retire && op == `OP_CLEAREVENT)
                             ? (events_live & ~f_cev_mask) : events_live;

    // -- sequential ------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            running_r <= 1'b0; halted_r <= 1'b0; stalled_r <= 1'b0;
            errors_r <= 16'b0; fault_code_r <= 8'b0;
            pc_r <= 8'b0; cursor_r <= 24'b0;
            regfile[0] <= 24'b0; regfile[1] <= 24'b0;
            regfile[2] <= 24'b0; regfile[3] <= 24'b0;
            isr_r <= 8'b0; marker_r <= 8'b0;
            f_timeout <= 1'b0; f_interrupted <= 1'b0; f_zero <= 1'b0; f_carry <= 1'b0;
            events_r <= 8'b0; previous_r <= 8'b0;
            link0_r <= 8'b0; link1_r <= 8'b0; link_depth_r <= 2'b0;
            bubble_r <= 1'b0; pull_empty_r <= 1'b0;
            waitedge_active_r <= 1'b0; waitedge_baseline_r <= 24'b0;
        end else if (!ena) begin
            // Clock disabled: hold all state.
        end else begin
            marker_r <= 8'b0;

            // Edge watchers run every running clock, before the pipeline.
            if (running_r) begin
                previous_r <= pins_byte;
                events_r   <= events_next;
            end

            if (stop) begin
                running_r <= 1'b0;
                stalled_r <= 1'b0;
            end else if (start && !running_r && program_valid) begin
                running_r <= 1'b1;
                halted_r  <= 1'b0;
                stalled_r <= 1'b0;
                pc_r      <= 8'b0;
                cursor_r  <= now;
                events_r  <= 8'b0;
                marker_r  <= 8'b0;
                pull_empty_r <= 1'b0;
                bubble_r  <= 1'b0;
                link_depth_r <= 2'b0;
                waitedge_active_r <= 1'b0;
            end else if (running_r) begin
                if (bubble_r) begin
                    bubble_r  <= 1'b0;
                    stalled_r <= 1'b0;
                end else if (!is_legal) begin
                    errors_r  <= errors_r | E_ILLEGAL;
                    running_r <= 1'b0; halted_r <= 1'b1; stalled_r <= 1'b0;
                end else if (!fetch_written) begin
                    errors_r  <= errors_r | E_INVALID_WORD;
                    running_r <= 1'b0; halted_r <= 1'b1; stalled_r <= 1'b0;
                end else if (do_fault) begin
                    errors_r  <= errors_r | fbits;
                    running_r <= 1'b0; halted_r <= 1'b1; stalled_r <= 1'b0;
                end else if (do_stall) begin
                    stalled_r <= 1'b1;
                    if (op == `OP_WAITEDGE && !waitedge_active_r) begin
                        waitedge_active_r  <= 1'b1;
                        waitedge_baseline_r <= now;
                    end
                end else begin
                    // -- retire: datapath --------------------------------
                    stalled_r <= 1'b0;
                    case (op)
                        `OP_LI:  regfile[f_li_reg] <= f_li_val;
                        `OP_MOV: regfile[f_dst] <= regfile[f_src];
                        `OP_ADD: begin regfile[f_dst] <= add_res[23:0];
                                 f_carry <= add_res[24]; f_zero <= (add_res[23:0] == 24'd0); end
                        `OP_SUB: begin regfile[f_dst] <= sub_res[23:0];
                                 f_carry <= sub_res[24]; f_zero <= (sub_res[23:0] == 24'd0); end
                        `OP_AND: begin regfile[f_dst] <= rd_dst & rd_src;
                                 f_zero <= ((rd_dst & rd_src) == 24'd0); end
                        `OP_OR:  begin regfile[f_dst] <= rd_dst | rd_src;
                                 f_zero <= ((rd_dst | rd_src) == 24'd0); end
                        `OP_XOR: begin regfile[f_dst] <= rd_dst ^ rd_src;
                                 f_zero <= ((rd_dst ^ rd_src) == 24'd0); end
                        `OP_SHL: begin regfile[f_dst] <= shl_res[23:0];
                                 f_carry <= shl_res[24]; f_zero <= (shl_res[23:0] == 24'd0); end
                        `OP_SHR: begin regfile[f_dst] <= shr_res;
                                 f_carry <= shr_carry; f_zero <= (shr_res == 24'd0); end
                        `OP_DJNZ: regfile[f_djnz_reg] <= djnz_dec;
                        `OP_CALL: begin
                                 if (link_depth_r == 2'd0) link0_r <= pc_r + 8'd1;
                                 else link1_r <= pc_r + 8'd1;
                                 link_depth_r <= link_depth_r + 2'd1;
                        end
                        `OP_RET: link_depth_r <= link_depth_r - 2'd1;
                        `OP_ADVANCE:   cursor_r <= (cursor_r + f_ticks) & MASK;
                        `OP_ADVANCER:  cursor_r <= (cursor_r + regfile[f_reg]) & MASK;
                        `OP_CURSORSET: cursor_r <= regfile[f_reg] & MASK;
                        `OP_CURSORGET: regfile[f_reg] <= cursor_r;
                        `OP_NOW:       regfile[f_reg] <= now;
                        `OP_SAMPLE:    regfile[f_reg] <= {16'b0, pin_level};
                        `OP_IN:  isr_r <= f_in_msb
                                 ? (((isr_r << 1) | {7'b0, pin_level[f_in_pin]}) & 8'hFF)
                                 : ((isr_r >> 1) | (pin_level[f_in_pin] ? 8'h80 : 8'h00));
                        `OP_EDGETIME: regfile[f_et_reg] <= pin_edge_time[24*f_et_pin +: 24];
                        `OP_MOVIS:    regfile[f_reg] <= {16'b0, isr_r};
                        `OP_CLEARIS:  isr_r <= 8'b0;
                        `OP_WAITEDGE: begin
                                 waitedge_active_r <= 1'b0;
                                 if (wait_aborted) begin f_interrupted <= 1'b1; f_timeout <= 1'b0; end
                                 else if (we_matched) begin f_interrupted <= 1'b0; f_timeout <= 1'b0; end
                                 else begin f_timeout <= 1'b1; f_interrupted <= 1'b0; end
                        end
                        `OP_WAITFOR: begin
                                 if (wait_aborted) begin f_interrupted <= 1'b1; f_timeout <= 1'b0; end
                                 else if (wf_matched) begin f_interrupted <= 1'b0; f_timeout <= 1'b0; end
                                 else begin f_timeout <= 1'b1; f_interrupted <= 1'b0; end
                        end
                        `OP_PULL:   regfile[f_reg] <= {16'b0, tx_data};
                        `OP_PULLNB: begin
                                 pull_empty_r <= (tx_level == 5'd0);
                                 if (tx_level != 5'd0) regfile[f_reg] <= {16'b0, tx_data};
                        end
                        `OP_MARK:   marker_r <= f_mark;
                        `OP_WAITEVENT: begin
                                 if (wev_match) begin f_timeout <= 1'b0; f_interrupted <= 1'b0; end
                                 else begin f_timeout <= 1'b1; f_interrupted <= 1'b0; end
                        end
                        `OP_CRCGET: regfile[f_reg] <= {8'b0, crc_value};
                        default: ; // RX, CLEAREVENT, CLEARMISS, CRC* via strobes
                    endcase

                    // -- retire: control ---------------------------------
                    if (do_halt) begin
                        running_r <= 1'b0; halted_r <= 1'b1;
                        if (op == `OP_FAIL) begin
                            errors_r <= errors_r | E_FIRMWARE;
                            fault_code_r <= f_fail;
                        end
                    end else if (do_redirect) begin
                        pc_r <= btarget; bubble_r <= 1'b1;
                    end else begin
                        pc_r <= pc_r + 8'd1;
                    end
                end
            end
        end
    end

    // -- outputs --------------------------------------------------------
    assign running    = running_r;
    assign halted     = halted_r;
    assign errors     = errors_r;
    assign fault_code = fault_code_r;
    assign cursor     = cursor_r;
    assign pc         = pc_r;
    assign marker     = marker_r;
    assign dbg_r0     = regfile[0];
    assign dbg_r1     = regfile[1];
    assign dbg_r2     = regfile[2];
    assign dbg_r3     = regfile[3];
    assign dbg_isr    = isr_r;
    assign dbg_events = events_r;
    assign dbg_link_depth = link_depth_r;
    assign dbg_stalled = stalled_r;

`ifdef FORMAL
    // Fetch is abstracted as an arbitrary word one cycle after the address:
    // `fetch_data` and `fetch_written` are free inputs, so every instruction
    // stream is over-approximated. The pins, queues and CRC are external, so
    // their levels/data are free inputs too; the properties below are the
    // engine's own guarantees at those interfaces.
    reg        f_past_valid;
    reg        f_rstn_prev;
    reg [15:0] f_errors_prev;
    reg        f_bubble_prev;
    reg        f_taken_prev;
    reg        f_retempty_prev;
    reg        f_active_prev;
    initial f_past_valid = 1'b0;
    always @(posedge clk) begin
        f_past_valid    <= 1'b1;
        f_rstn_prev     <= rst_n;
        f_errors_prev   <= errors_r;
        f_bubble_prev   <= bubble_r;
        f_taken_prev    <= taken_branch;
        f_retempty_prev <= ret_empty;
        f_active_prev   <= f_active;

        if (!f_past_valid) assume (!rst_n);

        if (f_past_valid && rst_n) begin
            // Return-stack depth never exceeds two.
            assert (link_depth_r <= 2'd2);
            // A commit strobe is only ever asserted for an owned pin.
            assert ((commit & ~owner) == 8'b0);
            // The engine never pushes a full RX queue nor pulls an empty TX
            // queue, so it keeps the external queue levels within 0..16.
            if (rx_push) assert (rx_level != 5'd16);
            if (tx_pull) assert (tx_level != 5'd0);
            // A bubble cycle has no side effects (the branch's second cycle).
            if (bubble_r)
                assert (commit == 8'b0 && !tx_pull && !rx_push &&
                        !crc_cfg && !crc_load && !crc_bit && clear_missed == 8'b0);
        end

        if (f_past_valid && rst_n && f_rstn_prev) begin
            // Error bits are sticky: none ever clears while reset is released.
            assert ((errors_r & f_errors_prev) == f_errors_prev);
            // A taken branch is followed by exactly one bubble cycle, so it
            // costs exactly two cycles.
            if (f_taken_prev) assert (bubble_r);
            if (f_bubble_prev && f_active_prev) assert (!bubble_r);
            // RET on an empty stack faults E_STACK and halts.
            if (f_retempty_prev)
                assert ((errors_r & E_STACK) != 16'b0 && !running_r);
        end
    end
`endif
endmodule
`default_nettype wire
