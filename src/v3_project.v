// SPDX-License-Identifier: Apache-2.0
`default_nettype none
//
// V3 top level: the Tiny Tapeout wrapper that integrates every V3 block.
//
// One shared timebase drives two programmable engines over eight shared
// protocol pins. The host SPI transport writes the two program memories,
// configures ownership and watchers, and starts and stops the engines. Each
// pin cell schedules a single pending commit from its owning engine; the two
// ownership masks are disjoint by construction so only one engine can ever
// strobe a given pin. A single shared CRC/LFSR unit serves both engines with
// fixed priority (engine 0 first). Capture observes the pins and engine flags.
//
// Pinout (matches info.yaml):
//   ui_in[0]  host SPI SCK        uo_out[0] host SPI MISO
//   ui_in[1]  host SPI CS_n       uo_out[1] engine 0 running
//   ui_in[2]  host SPI MOSI       uo_out[2] engine 1 running
//   ui_in[7:3] unused             uo_out[3] aggregate sticky error
//   uio[7:0]  protocol pins 0..7  uo_out[7:4] unused (driven low)
//
// The required file name and module name differ, which Verilator's
// DECLFILENAME style check flags; neither name can change, so the check is
// scoped off for this module only.
/* verilator lint_off DECLFILENAME */
module tt_um_mcranny_protocol_emulator_v3 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

    // ------------------------------------------------------------------
    // Host command codes (low four bits; bit 6 selects the engine, bits 5:4
    // are ignored). Codes 0x0A-0x0F echo with no effect, like NOP.
    // ------------------------------------------------------------------
    localparam [3:0] CMD_WRITE   = 4'h1;
    localparam [3:0] CMD_READ    = 4'h2;
    localparam [3:0] CMD_CONFIG  = 4'h3;
    localparam [3:0] CMD_WATCH   = 4'h4;
    localparam [3:0] CMD_START   = 4'h5;
    localparam [3:0] CMD_STOP    = 4'h6;
    localparam [3:0] CMD_VERSION = 4'h7;
    localparam [3:0] CMD_TXPUSH  = 4'h8;
    localparam [3:0] CMD_RXPOP   = 4'h9;

    localparam [31:0] VERSION_WORD = 32'h0300_0000;

    // ------------------------------------------------------------------
    // Shared timebase.
    // ------------------------------------------------------------------
    wire [23:0] tb_now;
    wire [7:0]  tb_epoch;

    v3_timebase u_timebase (
        .clk   (clk),
        .rst_n (rst_n),
        .now   (tb_now),
        .epoch (tb_epoch)
    );

    // ------------------------------------------------------------------
    // Input filter: uio_in -> filtered -> pin cells' pin_in.
    // The pin cell adds its own two-flop synchronizer, so the engine-visible
    // level is the debounced input synchronized into the clock domain. The
    // filter does not synchronize and the cell does not debounce, so there is
    // no double-filter.
    // ------------------------------------------------------------------
    wire [7:0] filtered;

    v3_input_filter u_filter (
        .clk      (clk),
        .rst_n    (rst_n),
        .samples  (uio_in),
        .filtered (filtered)
    );

    // Raw synchronized inputs for capture (two flops, no debounce), matching
    // model.Synchronizer feeding capture rather than the filtered level.
    reg [7:0] cap_sync0, cap_sync1;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cap_sync0 <= 8'b0;
            cap_sync1 <= 8'b0;
        end else begin
            cap_sync0 <= uio_in;
            cap_sync1 <= cap_sync0;
        end
    end

    // ------------------------------------------------------------------
    // Host SPI transport.
    // ------------------------------------------------------------------
    wire        host_miso;
    wire        host_cmd_valid;
    wire        host_frame_error;
    wire [7:0]  host_cmd;
    wire [7:0]  host_addr;
    wire [31:0] host_payload;
    wire        host_early_valid;
    wire [7:0]  host_early_cmd;
    wire [7:0]  host_early_addr;
    reg  [47:0] host_reply;   // combinational response for the current command

    v3_host_spi u_host (
        .clk         (clk),
        .rst_n       (rst_n),
        .ena         (ena),
        .sck         (ui_in[0]),
        .cs_n        (ui_in[1]),
        .mosi        (ui_in[2]),
        .miso        (host_miso),
        .cmd_valid   (host_cmd_valid),
        .frame_error (host_frame_error),
        .cmd         (host_cmd),
        .addr        (host_addr),
        .payload     (host_payload),
        .early_valid (host_early_valid),
        .early_cmd   (host_early_cmd),
        .early_addr  (host_early_addr),
        .reply       (host_reply)
    );

    wire [3:0] code       = host_cmd[3:0];
    wire       engine_sel = host_cmd[6];       // ENGINE_BIT

    // The accepted frame, one clock before cmd_valid, used to pre-fetch a READ
    // so the registered program word is ready when the reply is captured.
    wire [3:0] early_code       = host_early_cmd[3:0];
    wire       early_engine_sel = host_early_cmd[6];

    // ------------------------------------------------------------------
    // Configuration registers (written by CONFIG and WATCH while stopped).
    // ------------------------------------------------------------------
    reg [7:0] owner0_r, owner1_r;
    reg [7:0] od0_r,    od1_r;

    // Two edge watchers per engine: mask, value, edges, enable, abort.
    reg [7:0] e0_w0_mask, e0_w0_val, e0_w0_edges;
    reg [7:0] e0_w1_mask, e0_w1_val, e0_w1_edges;
    reg       e0_w0_en, e0_w1_en, e0_w0_abort, e0_w1_abort;
    reg [7:0] e1_w0_mask, e1_w0_val, e1_w0_edges;
    reg [7:0] e1_w1_mask, e1_w1_val, e1_w1_edges;
    reg       e1_w0_en, e1_w1_en, e1_w0_abort, e1_w1_abort;

    // Engine status (driven by the engine instances below).
    wire        eng0_running, eng1_running;
    wire [15:0] eng0_errors,  eng1_errors;

    wire both_stopped = !eng0_running && !eng1_running;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            owner0_r <= 8'b0; owner1_r <= 8'b0;
            od0_r    <= 8'b0; od1_r    <= 8'b0;
            e0_w0_mask <= 8'b0; e0_w0_val <= 8'b0; e0_w0_edges <= 8'b0;
            e0_w1_mask <= 8'b0; e0_w1_val <= 8'b0; e0_w1_edges <= 8'b0;
            e0_w0_en <= 1'b0; e0_w1_en <= 1'b0; e0_w0_abort <= 1'b0; e0_w1_abort <= 1'b0;
            e1_w0_mask <= 8'b0; e1_w0_val <= 8'b0; e1_w0_edges <= 8'b0;
            e1_w1_mask <= 8'b0; e1_w1_val <= 8'b0; e1_w1_edges <= 8'b0;
            e1_w0_en <= 1'b0; e1_w1_en <= 1'b0; e1_w0_abort <= 1'b0; e1_w1_abort <= 1'b0;
        end else if (!ena) begin
            // Hold configuration while the clock is disabled.
        end else if (host_cmd_valid) begin
            if (code == CMD_CONFIG && both_stopped) begin
                if (engine_sel == 1'b0) begin
                    owner0_r <= host_payload[7:0];
                    od0_r    <= host_payload[15:8];
                end else begin
                    owner1_r <= host_payload[7:0];
                    od1_r    <= host_payload[15:8];
                end
            end
            if (code == CMD_WATCH && both_stopped) begin
                if (engine_sel == 1'b0) begin
                    if (host_addr[0] == 1'b0) begin
                        e0_w0_en    <= 1'b1;
                        e0_w0_mask  <= host_payload[7:0];
                        e0_w0_val   <= host_payload[15:8];
                        e0_w0_edges <= host_payload[23:16];
                        e0_w0_abort <= host_payload[24];
                    end else begin
                        e0_w1_en    <= 1'b1;
                        e0_w1_mask  <= host_payload[7:0];
                        e0_w1_val   <= host_payload[15:8];
                        e0_w1_edges <= host_payload[23:16];
                        e0_w1_abort <= host_payload[24];
                    end
                end else begin
                    if (host_addr[0] == 1'b0) begin
                        e1_w0_en    <= 1'b1;
                        e1_w0_mask  <= host_payload[7:0];
                        e1_w0_val   <= host_payload[15:8];
                        e1_w0_edges <= host_payload[23:16];
                        e1_w0_abort <= host_payload[24];
                    end else begin
                        e1_w1_en    <= 1'b1;
                        e1_w1_mask  <= host_payload[7:0];
                        e1_w1_val   <= host_payload[15:8];
                        e1_w1_edges <= host_payload[23:16];
                        e1_w1_abort <= host_payload[24];
                    end
                end
            end
        end
    end

    // Ownership masks fed to the engines and the pin routing. Engine 1's mask
    // has engine 0's bits removed so the two are disjoint by construction
    // regardless of what the host wrote.
    wire [7:0] own0 = owner0_r;
    wire [7:0] own1 = owner1_r & ~owner0_r;

    // ------------------------------------------------------------------
    // Program memories and the host read/write/start decode.
    // ------------------------------------------------------------------
    wire [7:0]  eng0_fetch_addr, eng1_fetch_addr;
    wire [31:0] pm0_fetch_data,  pm1_fetch_data;
    wire        pm0_fetch_valid, pm1_fetch_valid;
    wire        pm0_complete,    pm1_complete;

    // Start/stop strobes: one host frame starts or stops the two engines
    // atomically.
    wire start_cmd = host_cmd_valid && (code == CMD_START);
    wire stop_cmd  = host_cmd_valid && (code == CMD_STOP);
    // START is all-or-nothing: a rejected START (any commanded engine's image
    // incomplete) strobes neither engine. start_reject is declared below.
    wire eng0_start = start_cmd && host_payload[0] && !start_reject;
    wire eng1_start = start_cmd && host_payload[1] && !start_reject;
    wire eng0_stop  = stop_cmd  && host_payload[0];
    wire eng1_stop  = stop_cmd  && host_payload[1];

    // Program writes are refused while the target engine is running.
    wire write0_en = host_cmd_valid && (code == CMD_WRITE) && (engine_sel == 1'b0) && !eng0_running;
    wire write1_en = host_cmd_valid && (code == CMD_WRITE) && (engine_sel == 1'b1) && !eng1_running;

    wire read0 = host_cmd_valid && (code == CMD_READ) && (engine_sel == 1'b0);
    wire read1 = host_cmd_valid && (code == CMD_READ) && (engine_sel == 1'b1);

    // Queue commands. TXPUSH carries its byte count in addr (1-4); an out of
    // range count drives a zero push count, which the FIFO rejects. RXPOP pops
    // the addressed engine's receive queue; its reply is combinational so the
    // host sees the popped record on the same frame it commands the pop.
    wire       txpush      = host_cmd_valid && (code == CMD_TXPUSH);
    wire       rxpop       = host_cmd_valid && (code == CMD_RXPOP);
    wire [2:0] txpush_count = (host_addr >= 8'd1 && host_addr <= 8'd4) ? host_addr[2:0] : 3'd0;
    wire       txpush0 = txpush && (engine_sel == 1'b0);
    wire       txpush1 = txpush && (engine_sel == 1'b1);
    wire       rxpop0  = rxpop  && (engine_sel == 1'b0);
    wire       rxpop1  = rxpop  && (engine_sel == 1'b1);

    // A READ reads a registered program word, valid the clock after its
    // address is presented, but the host reply is captured on the cmd_valid
    // clock. Pre-fetch the addressed word one clock early, off the early frame
    // the host transport exposes, so the word is ready before cmd_valid. The
    // pre-fetch only applies while the target engine is stopped; a running
    // engine keeps the fetch port, and A_MEN stays low while it stalls.
    wire read0_pre = host_early_valid && (early_code == CMD_READ) &&
                     (early_engine_sel == 1'b0) && !eng0_running;
    wire read1_pre = host_early_valid && (early_code == CMD_READ) &&
                     (early_engine_sel == 1'b1) && !eng1_running;

    wire        pm0_fetch_en   = eng0_running || eng0_start || read0 || read0_pre;
    wire [7:0]  pm0_fetch_addr = (eng0_running || eng0_start) ? eng0_fetch_addr :
                                 read0_pre ? host_early_addr : host_addr;
    wire        pm1_fetch_en   = eng1_running || eng1_start || read1 || read1_pre;
    wire [7:0]  pm1_fetch_addr = (eng1_running || eng1_start) ? eng1_fetch_addr :
                                 read1_pre ? host_early_addr : host_addr;

    v3_progmem u_progmem0 (
        .clk            (clk),
        .rst_n          (rst_n),
        .fetch_en       (pm0_fetch_en),
        .fetch_addr     (pm0_fetch_addr),
        .fetch_data     (pm0_fetch_data),
        .fetch_valid    (pm0_fetch_valid),
        .write_en       (write0_en),
        .write_addr     (host_addr),
        .write_data     (host_payload),
        .invalidate     (1'b0),
        .image_complete (pm0_complete)
    );

    v3_progmem u_progmem1 (
        .clk            (clk),
        .rst_n          (rst_n),
        .fetch_en       (pm1_fetch_en),
        .fetch_addr     (pm1_fetch_addr),
        .fetch_data     (pm1_fetch_data),
        .fetch_valid    (pm1_fetch_valid),
        .write_en       (write1_en),
        .write_addr     (host_addr),
        .write_data     (host_payload),
        .invalidate     (1'b0),
        .image_complete (pm1_complete)
    );

    // ------------------------------------------------------------------
    // Host reply (shifted out on the next frame). Rejections set REJECT_BIT.
    // ------------------------------------------------------------------
    wire write_reject  = (code == CMD_WRITE)  && (engine_sel ? eng1_running : eng0_running);
    wire config_reject = (code == CMD_CONFIG) && !both_stopped;
    wire watch_reject  = (code == CMD_WATCH)  && !both_stopped;
    wire start_reject  = (code == CMD_START)  &&
                         ((host_payload[0] && !pm0_complete) ||
                          (host_payload[1] && !pm1_complete));
    // TXPUSH is rejected when the queue will not take the push (a zero or over
    // four count, or the bytes do not fit). RXPOP is rejected when empty.
    wire       tx_push_ok_sel = engine_sel ? tx1_push_ok : tx0_push_ok;
    wire [4:0] rx_level_sel   = engine_sel ? rx1_level   : rx0_level;
    wire [15:0] rx_head_sel   = engine_sel ? rx1_head    : rx0_head;
    wire txpush_reject = (code == CMD_TXPUSH) && !tx_push_ok_sel;
    wire rxpop_reject  = (code == CMD_RXPOP)  && (rx_level_sel == 5'd0);
    wire reject = write_reject || config_reject || watch_reject || start_reject ||
                  txpush_reject || rxpop_reject;

    wire [31:0] read_data = engine_sel ? pm1_fetch_data : pm0_fetch_data;
    wire [7:0]  reply_cmd = reject ? (host_cmd | 8'h80) : host_cmd;

    reg [31:0] reply_payload_r;
    always @* begin
        reply_payload_r = 32'b0;
        if (code == CMD_VERSION) reply_payload_r = VERSION_WORD;
        else if (code == CMD_READ) reply_payload_r = read_data;
        else if (code == CMD_RXPOP && rx_level_sel != 5'd0)
            reply_payload_r = {16'b0, rx_head_sel};
    end

    always @* host_reply = {reply_cmd, host_addr, reply_payload_r};

    // ------------------------------------------------------------------
    // Shared pin cells and the per-pin commit routing.
    // ------------------------------------------------------------------
    wire [7:0]  eng0_commit,       eng1_commit;
    wire [7:0]  eng0_commit_value, eng1_commit_value;
    wire [7:0]  eng0_commit_oe,    eng1_commit_oe;
    wire [23:0] eng0_commit_due,   eng1_commit_due;
    wire [7:0]  eng0_clear_missed, eng1_clear_missed;

    wire [7:0]   slot_free_w;
    wire [7:0]   pin_level_w;
    wire [7:0]   pin_drv_value_w;
    wire [7:0]   pin_drv_oe_w;
    wire [7:0]   pin_missed_w;
    wire [7:0]   pin_edge_pol_w;
    wire [191:0] pin_edge_time_w;
    wire [7:0]   pin_oe_w;

    wire pin_enable = ena;

    genvar gi;
    generate
        for (gi = 0; gi < 8; gi = gi + 1) begin : pins
            wire owned0 = own0[gi];
            wire owned1 = own1[gi];

            wire        pin_commit = (owned0 && eng0_commit[gi]) |
                                     (owned1 && eng1_commit[gi]);
            wire        pin_cval   = owned1 ? eng1_commit_value[gi] : eng0_commit_value[gi];
            wire        pin_coe    = owned1 ? eng1_commit_oe[gi]    : eng0_commit_oe[gi];
            wire [23:0] pin_cdue   = owned1 ? eng1_commit_due       : eng0_commit_due;
            wire        pin_clrm   = (owned0 && eng0_clear_missed[gi]) |
                                     (owned1 && eng1_clear_missed[gi]);
            wire        pin_od     = owned1 ? od1_r[gi] : od0_r[gi];

            wire        s_free, p_out, p_oe, p_missed, p_pol, p_level;
            wire [23:0] p_etime;

            v3_pin_cell u_pin (
                .clk           (clk),
                .rst_n         (rst_n),
                .enable        (pin_enable),
                .now           (tb_now),
                .open_drain    (pin_od),
                .commit        (pin_commit),
                .commit_due    (pin_cdue),
                .commit_value  (pin_cval),
                .commit_oe     (pin_coe),
                .clear_missed  (pin_clrm),
                .pin_in        (filtered[gi]),
                .slot_free     (s_free),
                .pin_out       (p_out),
                .pin_oe        (p_oe),
                .missed        (p_missed),
                .edge_time     (p_etime),
                .edge_polarity (p_pol),
                .level         (p_level)
            );

            assign slot_free_w[gi]                = s_free;
            assign uio_out[gi]                    = p_out;
            assign pin_oe_w[gi]                   = p_oe;
            assign pin_missed_w[gi]               = p_missed;
            assign pin_edge_pol_w[gi]             = p_pol;
            assign pin_level_w[gi]                = p_level;
            // The pin cell exposes only its padded drive, not the raw latched
            // value/oe. These match the engine-visible driven value/oe for the
            // non-open-drain, enabled case (the differential uses open_drain=0).
            assign pin_drv_value_w[gi]            = p_out;
            assign pin_drv_oe_w[gi]               = p_oe;
            assign pin_edge_time_w[24*gi +: 24]   = p_etime;

`ifdef FORMAL
            // Black-box pin-cell release contract (proven in v3_pin_cell.ys):
            // the cell drops its output enable whenever it is disabled or in
            // reset.
            always @* assume ((pin_enable && rst_n) || !p_oe);
            // An engine can only strobe a pin that it owns.
            always @* if (pin_commit) assert (owned0 || owned1);
`endif
        end
    endgenerate

    assign uio_oe = pin_oe_w;

    // ------------------------------------------------------------------
    // Shared CRC/LFSR, engine 0 has fixed priority. v3_crc performs at most
    // one operation per clock, so it cannot chain two simultaneous requests
    // within a cycle: engine 1's operation is dropped whenever engine 0
    // issues one in the same cycle.
    // ------------------------------------------------------------------
    wire        crc0_cfg,  crc1_cfg;
    wire [15:0] crc0_poly, crc1_poly;
    wire        crc0_msb,  crc1_msb;
    wire        crc0_load, crc1_load;
    wire [15:0] crc0_seed, crc1_seed;
    wire        crc0_bit,  crc1_bit;
    wire        crc0_bitv, crc1_bitv;
    wire [15:0] crc_value;

    wire e0_crc_active = crc0_cfg || crc0_load || crc0_bit;

    wire        crc_cfg  = e0_crc_active ? crc0_cfg  : crc1_cfg;
    wire [15:0] crc_poly = e0_crc_active ? crc0_poly : crc1_poly;
    wire        crc_msb  = e0_crc_active ? crc0_msb  : crc1_msb;
    wire        crc_load = e0_crc_active ? crc0_load : crc1_load;
    wire [15:0] crc_seed = e0_crc_active ? crc0_seed : crc1_seed;
    wire        crc_bit  = e0_crc_active ? crc0_bit  : crc1_bit;
    wire        crc_bitv = e0_crc_active ? crc0_bitv : crc1_bitv;

    v3_crc u_crc (
        .clk       (clk),
        .rst_n     (rst_n),
        .configure (crc_cfg),
        .poly      (crc_poly),
        .msb_first (crc_msb),
        .load      (crc_load),
        .seed      (crc_seed),
        .shift     (crc_bit),
        .bit_in    (crc_bitv),
        .value     (crc_value)
    );

    // Observe-only / unused engine outputs. V3's host protocol exposes none of
    // these, so they are consumed by the lint sink below rather than driven to
    // a pin. They remain readable in simulation through the instance hierarchy.
    wire        e0_halted,  e1_halted;
    wire [7:0]  e0_fault,   e1_fault;
    wire [23:0] e0_cursor,  e1_cursor;
    wire [7:0]  e0_pc,      e1_pc;
    wire [7:0]  e0_marker,  e1_marker;
    wire [23:0] e0_r0, e0_r1, e0_r2, e0_r3;
    wire [23:0] e1_r0, e1_r1, e1_r2, e1_r3;
    wire [7:0]  e0_isr,    e1_isr;
    wire [7:0]  e0_events, e1_events;
    wire [1:0]  e0_link,   e1_link;
    wire        e0_dstall, e1_dstall;
    wire        e0_tx_pull,  e1_tx_pull;
    wire        e0_rx_push,  e1_rx_push;
    wire [7:0]  e0_rx_value, e1_rx_value;
    wire [7:0]  e0_rx_meta,  e1_rx_meta;

    // ------------------------------------------------------------------
    // Per-engine queues. The TX byte queue is filled by the host (TXPUSH) and
    // drained by the engine (PULL); the RX record queue is filled by the
    // engine (RX) and drained by the host (RXPOP). An RX push to a full queue
    // never happens in practice: the engine faults E_QUEUE first, so the
    // FIFO's drop-on-full is only a backstop. The queues survive START and
    // STOP; only reset clears them.
    // ------------------------------------------------------------------
    wire [7:0]  tx0_head,  tx1_head;
    wire [4:0]  tx0_level, tx1_level;
    wire        tx0_push_ok, tx1_push_ok;
    wire [15:0] rx0_head,  rx1_head;
    wire [4:0]  rx0_level, rx1_level;
    wire        rx0_push_ok, rx1_push_ok;

    v3_fifo #(.WIDTH(8), .DEPTH(16), .PUSH_MAX(4)) u_tx_fifo0 (
        .clk        (clk),
        .rst_n      (rst_n),
        .push_count (txpush0 ? txpush_count : 3'd0),
        .push_data  (host_payload),
        .push_ok    (tx0_push_ok),
        .pop        (e0_tx_pull),
        .head       (tx0_head),
        .level      (tx0_level)
    );

    v3_fifo #(.WIDTH(16), .DEPTH(16), .PUSH_MAX(1)) u_rx_fifo0 (
        .clk        (clk),
        .rst_n      (rst_n),
        .push_count (e0_rx_push),
        .push_data  ({e0_rx_value, e0_rx_meta}),
        .push_ok    (rx0_push_ok),
        .pop        (rxpop0),
        .head       (rx0_head),
        .level      (rx0_level)
    );

    v3_fifo #(.WIDTH(8), .DEPTH(16), .PUSH_MAX(4)) u_tx_fifo1 (
        .clk        (clk),
        .rst_n      (rst_n),
        .push_count (txpush1 ? txpush_count : 3'd0),
        .push_data  (host_payload),
        .push_ok    (tx1_push_ok),
        .pop        (e1_tx_pull),
        .head       (tx1_head),
        .level      (tx1_level)
    );

    v3_fifo #(.WIDTH(16), .DEPTH(16), .PUSH_MAX(1)) u_rx_fifo1 (
        .clk        (clk),
        .rst_n      (rst_n),
        .push_count (e1_rx_push),
        .push_data  ({e1_rx_value, e1_rx_meta}),
        .push_ok    (rx1_push_ok),
        .pop        (rxpop1),
        .head       (rx1_head),
        .level      (rx1_level)
    );

    // ------------------------------------------------------------------
    // Engine 0.
    // ------------------------------------------------------------------
    v3_engine u_engine0 (
        .clk           (clk),
        .rst_n         (rst_n),
        .ena           (ena),
        .now           (tb_now),
        .start         (eng0_start),
        .stop          (eng0_stop),
        .program_valid (pm0_complete),
        .owner         (own0),
        .open_drain    (od0_r),
        .fetch_addr    (eng0_fetch_addr),
        .fetch_data    (pm0_fetch_data),
        .fetch_written (pm0_fetch_valid),
        .commit        (eng0_commit),
        .commit_value  (eng0_commit_value),
        .commit_oe     (eng0_commit_oe),
        .commit_due    (eng0_commit_due),
        .slot_free     (slot_free_w),
        .pin_level     (pin_level_w),
        .pin_drv_value (pin_drv_value_w),
        .pin_drv_oe    (pin_drv_oe_w),
        .pin_missed    (pin_missed_w),
        .pin_edge_time (pin_edge_time_w),
        .pin_edge_polarity (pin_edge_pol_w),
        .clear_missed  (eng0_clear_missed),
        .watch0_en     (e0_w0_en),
        .watch0_mask   (e0_w0_mask),
        .watch0_value  (e0_w0_val),
        .watch0_edges  (e0_w0_edges),
        .watch1_en     (e0_w1_en),
        .watch1_mask   (e0_w1_mask),
        .watch1_value  (e0_w1_val),
        .watch1_edges  (e0_w1_edges),
        .abort_mask    ({6'b0, e0_w1_abort, e0_w0_abort}),
        .tx_level      (tx0_level),
        .tx_data       (tx0_head),
        .tx_pull       (e0_tx_pull),
        .rx_level      (rx0_level),
        .rx_push       (e0_rx_push),
        .rx_value      (e0_rx_value),
        .rx_meta       (e0_rx_meta),
        .crc_cfg       (crc0_cfg),
        .crc_poly      (crc0_poly),
        .crc_msb_first (crc0_msb),
        .crc_load      (crc0_load),
        .crc_seed      (crc0_seed),
        .crc_bit       (crc0_bit),
        .crc_bit_value (crc0_bitv),
        .crc_value     (crc_value),
        .running       (eng0_running),
        .halted        (e0_halted),
        .errors        (eng0_errors),
        .fault_code    (e0_fault),
        .cursor        (e0_cursor),
        .pc            (e0_pc),
        .marker        (e0_marker),
        .dbg_r0        (e0_r0),
        .dbg_r1        (e0_r1),
        .dbg_r2        (e0_r2),
        .dbg_r3        (e0_r3),
        .dbg_isr       (e0_isr),
        .dbg_events    (e0_events),
        .dbg_link_depth(e0_link),
        .dbg_stalled   (e0_dstall)
    );

    // ------------------------------------------------------------------
    // Engine 1.
    // ------------------------------------------------------------------
    v3_engine u_engine1 (
        .clk           (clk),
        .rst_n         (rst_n),
        .ena           (ena),
        .now           (tb_now),
        .start         (eng1_start),
        .stop          (eng1_stop),
        .program_valid (pm1_complete),
        .owner         (own1),
        .open_drain    (od1_r),
        .fetch_addr    (eng1_fetch_addr),
        .fetch_data    (pm1_fetch_data),
        .fetch_written (pm1_fetch_valid),
        .commit        (eng1_commit),
        .commit_value  (eng1_commit_value),
        .commit_oe     (eng1_commit_oe),
        .commit_due    (eng1_commit_due),
        .slot_free     (slot_free_w),
        .pin_level     (pin_level_w),
        .pin_drv_value (pin_drv_value_w),
        .pin_drv_oe    (pin_drv_oe_w),
        .pin_missed    (pin_missed_w),
        .pin_edge_time (pin_edge_time_w),
        .pin_edge_polarity (pin_edge_pol_w),
        .clear_missed  (eng1_clear_missed),
        .watch0_en     (e1_w0_en),
        .watch0_mask   (e1_w0_mask),
        .watch0_value  (e1_w0_val),
        .watch0_edges  (e1_w0_edges),
        .watch1_en     (e1_w1_en),
        .watch1_mask   (e1_w1_mask),
        .watch1_value  (e1_w1_val),
        .watch1_edges  (e1_w1_edges),
        .abort_mask    ({6'b0, e1_w1_abort, e1_w0_abort}),
        .tx_level      (tx1_level),
        .tx_data       (tx1_head),
        .tx_pull       (e1_tx_pull),
        .rx_level      (rx1_level),
        .rx_push       (e1_rx_push),
        .rx_value      (e1_rx_value),
        .rx_meta       (e1_rx_meta),
        .crc_cfg       (crc1_cfg),
        .crc_poly      (crc1_poly),
        .crc_msb_first (crc1_msb),
        .crc_load      (crc1_load),
        .crc_seed      (crc1_seed),
        .crc_bit       (crc1_bit),
        .crc_bit_value (crc1_bitv),
        .crc_value     (crc_value),
        .running       (eng1_running),
        .halted        (e1_halted),
        .errors        (eng1_errors),
        .fault_code    (e1_fault),
        .cursor        (e1_cursor),
        .pc            (e1_pc),
        .marker        (e1_marker),
        .dbg_r0        (e1_r0),
        .dbg_r1        (e1_r1),
        .dbg_r2        (e1_r2),
        .dbg_r3        (e1_r3),
        .dbg_isr       (e1_isr),
        .dbg_events    (e1_events),
        .dbg_link_depth(e1_link),
        .dbg_stalled   (e1_dstall)
    );

    // ------------------------------------------------------------------
    // Aggregate status outputs.
    // ------------------------------------------------------------------
    wire err_agg = (|eng0_errors) || (|eng1_errors);

    assign uo_out[0]   = host_miso;
    assign uo_out[1]   = eng0_running;
    assign uo_out[2]   = eng1_running;
    assign uo_out[3]   = err_agg;
    assign uo_out[7:4] = 4'b0;

    // ------------------------------------------------------------------
    // Capture: armed when an engine starts, recording the raw synchronized
    // pins, driven pins and engine flags against the shared {epoch, now}
    // timestamp. V3's host protocol has no capture read-out, so its outputs
    // are observable only in simulation; they are consumed below so lint sees
    // no dangling nets.
    // ------------------------------------------------------------------
    wire [63:0] cap_read_data;
    wire [5:0]  cap_count;
    wire [31:0] cap_trigger_timestamp;
    wire        cap_armed, cap_triggered, cap_truncated;
    wire [2:0]  cap_reason;
    wire [7:0]  cap_flags = {5'b0, err_agg, eng1_running, eng0_running};

    v3_capture #(.DEPTH(32)) u_capture (
        .clk               (clk),
        .rst_n             (rst_n),
        .arm               (eng0_start || eng1_start),
        .stop              (1'b0),
        .disable_capture   (1'b0),
        .trigger_mode      (2'b0),
        .trigger_mask      (8'b0),
        .now               (tb_now),
        .epoch             (tb_epoch),
        .pins_in           (cap_sync1),
        .pins_out          (uio_out),
        .pins_oe           (pin_oe_w),
        .flags             (cap_flags),
        .read_address      (5'b0),
        .read_data         (cap_read_data),
        .count             (cap_count),
        .trigger_timestamp (cap_trigger_timestamp),
        .armed             (cap_armed),
        .triggered         (cap_triggered),
        .truncated         (cap_truncated),
        .reason            (cap_reason)
    );

    // ------------------------------------------------------------------
    // Sink intentionally-unused inputs and observe-only outputs so the
    // -Wall lint reports no dangling nets.
    // ------------------------------------------------------------------
    wire _unused = &{1'b0,
                     ui_in[7:3],
                     host_frame_error,
                     cap_read_data, cap_count, cap_trigger_timestamp,
                     cap_armed, cap_triggered, cap_truncated, cap_reason,
                     e0_halted, e1_halted, e0_fault, e1_fault,
                     e0_cursor, e1_cursor, e0_pc, e1_pc, e0_marker, e1_marker,
                     e0_r0, e0_r1, e0_r2, e0_r3, e1_r0, e1_r1, e1_r2, e1_r3,
                     e0_isr, e1_isr, e0_events, e1_events, e0_link, e1_link,
                     e0_dstall, e1_dstall,
                     rx0_push_ok, rx1_push_ok};

`ifdef FORMAL
    // Property 1: the two ownership masks are disjoint.
    always @* assert ((own0 & own1) == 8'b0);

    // Property 4: an accepted START commanding both engines strobes both
    // starts in the same cycle.
    always @* if (start_cmd && host_payload[0] && host_payload[1] && !start_reject)
        assert (eng0_start && eng1_start);

    // Property 4b: START is all-or-nothing. A rejected START strobes neither
    // engine, so a partially-loaded pair can never be half-started.
    always @* if (start_cmd && start_reject)
        assert (!eng0_start && !eng1_start);

    // Property 5: a program write is refused while that engine is running.
    always @* begin
        if (write0_en) assert (!eng0_running);
        if (write1_en) assert (!eng1_running);
    end

    // Property 3: !ena or !rst_n releases every output enable.
    always @* if (!ena || !rst_n) assert (uio_oe == 8'b0);
`endif

endmodule
/* verilator lint_on DECLFILENAME */
`default_nettype wire
